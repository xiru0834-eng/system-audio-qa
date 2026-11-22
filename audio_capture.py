"""Read only Windows WASAPI playback loopback endpoints."""

import audioop
import io
import math
import threading
import time
import wave
from array import array
from collections import deque

import pyaudiowpatch as pyaudio


class NoAudio(Exception):
    pass


def playback_devices(audio):
    devices = {
        int(item["index"]): item
        for item in audio.get_loopback_device_info_generator()
        if item.get("isLoopbackDevice")
    }
    try:
        default = int(audio.get_default_wasapi_loopback()["index"])
    except (OSError, LookupError):
        default = None
    return devices, default if default in devices else None


class AudioBuffer:
    def __init__(self, rate, channels, max_seconds=45, clock=time.perf_counter):
        self.rate = rate
        self.channels = channels
        self.frame_bytes = channels * 2
        self.capacity = int(rate * max_seconds) * self.frame_bytes
        self.clock = clock
        self.last_chunk_at = None
        self.chunks = deque()
        self.size = 0
        self.lock = threading.Lock()
        self.level = 0

    def append(self, pcm):
        if not pcm:
            return
        peak = audioop.max(pcm, 2)
        now = self.clock()
        with self.lock:
            if self.last_chunk_at is not None:
                gap = now - self.last_chunk_at - len(pcm) / (self.rate * self.frame_bytes)
                if gap > 0.35:
                    gap_frames = min(math.floor(gap * self.rate),
                                     self.capacity // self.frame_bytes)
                    self._append(bytes(gap_frames * self.frame_bytes))
            self.last_chunk_at = now
            self.level = peak
            self._append(pcm)

    def _append(self, pcm):
        pcm = pcm[-self.capacity:]
        self.chunks.append(pcm)
        self.size += len(pcm)
        while self.size > self.capacity:
            excess = self.size - self.capacity
            oldest = self.chunks.popleft()
            if len(oldest) > excess:
                self.chunks.appendleft(oldest[excess:])
            self.size -= min(len(oldest), excess)

    @property
    def frame_count(self):
        with self.lock:
            return self.size // self.frame_bytes

    def clear(self):
        with self.lock:
            self.chunks.clear()
            self.size = 0
            self.level = 0
            self.last_chunk_at = None

    def meter(self):
        with self.lock:
            if self.last_chunk_at is None or self.clock() - self.last_chunk_at > 0.5:
                return 0
            return self.level

    def snapshot(self, seconds, *, ended_at=None):
        ended_at = self.clock() if ended_at is None else ended_at
        with self.lock:
            if self.last_chunk_at is None:
                raise NoAudio("还没有收到播放音频。请先播放声音。")
            age = ended_at - self.last_chunk_at
            skip = max(0, round(-age * self.rate))
            wanted = min(max(0, round((seconds - max(0, age)) * self.rate)),
                         self.size // self.frame_bytes - skip)
            if wanted < self.rate // 4:
                raise NoAudio("最近一段没有足够的播放音频。")
            remaining = wanted * self.frame_bytes
            skip *= self.frame_bytes
            selected = []
            for chunk in reversed(self.chunks):
                if skip >= len(chunk):
                    skip -= len(chunk)
                    continue
                stop = len(chunk) - skip
                skip = 0
                size = min(stop, remaining)
                selected.append(chunk[stop - size:stop])
                remaining -= size
                if remaining == 0:
                    break
        return b"".join(reversed(selected))

    def encode(self, pcm):
        if self.channels == 2:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        elif self.channels > 2:
            samples = array("h", pcm)
            pcm = array("h", (
                sum(samples[i:i + self.channels]) // self.channels
                for i in range(0, len(samples), self.channels)
            )).tobytes()
        pcm, _ = audioop.ratecv(pcm, 2, 1, self.rate, 16000, None)
        # Trim long quiet edges, leaving 400 ms of context on each side.
        block_size = 3200
        audible = [
            i for i in range(0, len(pcm), block_size)
            if audioop.rms(pcm[i:i + block_size], 2) >= 25
        ]
        if not audible:
            raise NoAudio("最近一段没有检测到播放声音。请检查所选扬声器或耳机。")
        pcm = pcm[max(0, audible[0] - 4 * block_size):audible[-1] + 5 * block_size]
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        return output.getvalue()

    def wav(self, seconds):
        return self.encode(self.snapshot(seconds))


class PlaybackCapture:
    def __init__(self, device_index=None, *, on_chunk=None):
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.on_chunk = on_chunk
        self.callback_errors = 0
        try:
            devices, default = playback_devices(self.audio)
            index = default if device_index is None else device_index
            if index not in devices:
                raise NoAudio("找不到所选播放设备。请刷新设备列表再试。")
            device = devices[index]
            self.device_index = index
            self.is_playback_loopback = bool(device["isLoopbackDevice"])
            rate = int(device["defaultSampleRate"])
            channels = int(device["maxInputChannels"])
            self.device_name = str(device["name"]).removesuffix(" [Loopback]")
            self.buffer = AudioBuffer(rate, channels)
            self.stream = self.audio.open(
                format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                input_device_index=index, frames_per_buffer=1024, start=False,
                stream_callback=self._capture,
            )
            self.stream.start_stream()
        except Exception:
            self.close()
            raise

    def _capture(self, data, frames, timing, status):
        self.buffer.append(data)
        if self.on_chunk is not None:
            try:
                self.on_chunk(data, frames)
            except Exception:
                self.callback_errors += 1
        return None, pyaudio.paContinue

    def is_active(self):
        return self.stream is not None and self.stream.is_active()

    def close(self):
        try:
            if self.stream is not None:
                try:
                    if self.stream.is_active():
                        self.stream.stop_stream()
                finally:
                    self.stream.close()
                    self.stream = None
        finally:
            self.audio.terminate()

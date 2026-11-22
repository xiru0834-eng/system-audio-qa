"""Bounded audio handoff and stateful PCM conversion for live transcription."""

import audioop
import queue
import threading
from array import array
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioPacket:
    sequence: int
    frames: int
    data: bytes
    gap_before_frames: int = 0


class BoundedAudioQueue:
    def __init__(self, max_chunks=60):
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self._queue = queue.Queue(maxsize=max_chunks)
        self._lock = threading.Lock()
        self._sequence = 0
        self._pending_gap_frames = 0
        self.dropped_frames = 0

    def put(self, data, *, frames):
        if not data or frames <= 0:
            return True
        with self._lock:
            self._sequence += 1
            packet = AudioPacket(self._sequence, frames, bytes(data), self._pending_gap_frames)
            try:
                self._queue.put_nowait(packet)
            except queue.Full:
                self._pending_gap_frames += frames
                self.dropped_frames += frames
                return False
            self._pending_gap_frames = 0
            return True

    def get_nowait(self):
        return self._queue.get_nowait()

    def clear(self):
        discarded = 0
        while True:
            try:
                discarded += self._queue.get_nowait().frames
            except queue.Empty:
                break
        with self._lock:
            self.dropped_frames += discarded
            self._pending_gap_frames += discarded
        return discarded

    def __len__(self):
        return self._queue.qsize()


class StreamingPcmConverter:
    def __init__(self, *, sample_rate, channels, target_rate=16000, chunk_ms=100):
        if sample_rate <= 0 or channels <= 0 or target_rate <= 0 or chunk_ms <= 0:
            raise ValueError("Audio format values must be positive")
        self.sample_rate = sample_rate
        self.channels = channels
        self.target_rate = target_rate
        self.chunk_bytes = target_rate * 2 * chunk_ms // 1000
        self._rate_state = None
        self._pending = bytearray()

    def feed(self, pcm):
        if not pcm:
            return ()
        frame_bytes = self.channels * 2
        if len(pcm) % frame_bytes:
            raise ValueError("PCM chunk ends with an incomplete sample frame")

        mono = self._downmix(pcm)
        converted, self._rate_state = audioop.ratecv(
            mono, 2, 1, self.sample_rate, self.target_rate, self._rate_state,
        )
        self._pending.extend(converted)
        return self._take_complete_chunks()

    def flush(self):
        chunks = list(self._take_complete_chunks())
        if self._pending:
            remainder = bytes(self._pending)
            self._pending.clear()
            chunks.append(remainder)
        return tuple(chunks)

    def reset(self):
        self._rate_state = None
        self._pending.clear()

    def _downmix(self, pcm):
        if self.channels == 1:
            return pcm
        if self.channels == 2:
            return audioop.tomono(pcm, 2, 0.5, 0.5)

        samples = array("h", pcm)
        mono = array("h", (
            sum(samples[index:index + self.channels]) // self.channels
            for index in range(0, len(samples), self.channels)
        ))
        return mono.tobytes()

    def _take_complete_chunks(self):
        chunks = []
        while len(self._pending) >= self.chunk_bytes:
            chunks.append(bytes(self._pending[:self.chunk_bytes]))
            del self._pending[:self.chunk_bytes]
        return tuple(chunks)

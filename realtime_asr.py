"""DashScope Fun-ASR-Realtime websocket protocol and worker."""

import json
import queue
import threading
import time
import uuid

from dashscope_client import REGIONS
from realtime_audio import StreamingPcmConverter


class RealtimeASRError(Exception):
    pass


def realtime_asr_url(workspace_id, region):
    if region not in REGIONS:
        raise ValueError("请选择北京或新加坡服务地域。")
    if not workspace_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
                                for char in workspace_id):
        raise ValueError("请填写有效的百炼业务空间 ID。")
    return f"wss://{workspace_id}.{REGIONS[region]}.maas.aliyuncs.com/api-ws/v1/inference"


def run_task_message(task_id, sample_rate=16000):
    return {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": "fun-asr-realtime",
            "parameters": {"sample_rate": sample_rate, "format": "wav"},
            "input": {},
        },
    }


def finish_task_message(task_id):
    return {
        "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {"input": {}},
    }


def wav_stream_header(sample_rate=16000, channels=1, bits_per_sample=16):
    """Return a streaming RIFF/WAVE header followed by binary PCM frames."""
    import struct

    bytes_per_sample = bits_per_sample // 8
    block_align = channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    return b"".join((
        b"RIFF", struct.pack("<I", 0xFFFFFFFF), b"WAVE",
        b"fmt ", struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate,
                             block_align, bits_per_sample),
        b"data", struct.pack("<I", 0xFFFFFFFF),
    ))


def parse_asr_event(message):
    header = message.get("header", {})
    event = header.get("event")
    if event == "task-started":
        return {"kind": "started"}
    if event == "task-finished":
        return {"kind": "finished"}
    if event == "task-failed":
        code = header.get("error_code", "unknown")
        raise RealtimeASRError(f"实时语音服务失败（{code}）。请检查 Key、地域和网络。")
    if event != "result-generated":
        return None

    sentence = message.get("payload", {}).get("output", {}).get("sentence", {})
    if sentence.get("heartbeat") or not sentence.get("text", "").strip():
        return None
    words = sentence.get("words", [])
    return {
        "kind": "caption",
        "sentence_id": sentence.get("sentence_id"),
        "begin_ms": sentence.get("begin_time", 0),
        "end_ms": sentence.get("end_time", 0),
        "text": sentence["text"],
        "sentence_begin": bool(sentence.get("sentence_begin")),
        "is_final": bool(sentence.get("sentence_end")),
        "words": words if isinstance(words, list) else [],
        "usage": message.get("payload", {}).get("usage"),
    }


class DashScopeRealtimeASR:
    def __init__(self, *, api_key, workspace_id, region, audio_queue,
                 sample_rate, channels, session_started_at, emit, connection_factory=None):
        self.url = realtime_asr_url(workspace_id, region)
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ValueError("请先在设置中保存百炼 API Key。")
        self.workspace_id = workspace_id.strip()
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.session_started_at = session_started_at
        self.emit = emit
        self.connection_factory = connection_factory
        self.stop_event = threading.Event()
        self.thread = None
        self.socket = None
        self.connection_id = 0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="fun-asr-realtime", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=2):
        if self.thread:
            self.thread.join(timeout)

    def _connect(self):
        if self.connection_factory:
            return self.connection_factory(self.url, self.api_key, self.workspace_id)
        import websocket

        return websocket.create_connection(
            self.url,
            header=[f"Authorization: Bearer {self.api_key}",
                    f"X-DashScope-WorkSpace: {self.workspace_id}"],
            timeout=10,
            enable_multithread=True,
        )

    def _run(self):
        self.emit("status", {"state": "connecting"})
        for attempt in range(2):
            if self.stop_event.is_set():
                break
            self.connection_id += 1
            connection_offset_ms = round((time.perf_counter() - self.session_started_at) * 1000)
            try:
                self._run_connection(connection_offset_ms)
                break
            except Exception as error:
                if self.stop_event.is_set():
                    break
                if attempt == 0:
                    lost_frames = self.audio_queue.clear()
                    self.emit("status", {
                        "state": "reconnecting",
                        "dropped_frames": lost_frames,
                        "error": self._safe_error(error),
                    })
                    continue
                self.emit("status", {"state": "error", "error": self._safe_error(error)})
        self.emit("status", {"state": "stopped" if self.stop_event.is_set() else "finished"})

    def _run_connection(self, connection_offset_ms):
        from websocket import WebSocketTimeoutException

        connection = self._connect()
        self.socket = connection
        connection.settimeout(0.05)
        task_id = uuid.uuid4().hex
        connection.send(json.dumps(run_task_message(task_id), ensure_ascii=False))
        converter = StreamingPcmConverter(sample_rate=self.sample_rate, channels=self.channels)
        started = False
        header_sent = False
        ready_deadline = time.monotonic() + 15
        task_finished = False
        try:
            while not self.stop_event.is_set() and not task_finished:
                if started:
                    header_sent = self._send_audio(connection, converter, header_sent)
                try:
                    raw = connection.recv()
                except WebSocketTimeoutException:
                    if not started and time.monotonic() >= ready_deadline:
                        raise RealtimeASRError("语音服务未确认开始任务。")
                    continue
                if not raw:
                    raise RealtimeASRError("语音连接被服务端关闭。")
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as error:
                    raise RealtimeASRError("收到无法读取的实时语音事件。") from error
                event = parse_asr_event(message)
                if event is None:
                    continue
                if event["kind"] == "started":
                    started = True
                    ready_deadline = float("inf")
                    self.emit("status", {"state": "connected", "connection_id": self.connection_id})
                elif event["kind"] == "finished":
                    task_finished = True
                elif event["kind"] == "caption":
                    event["connection_id"] = self.connection_id
                    event["offset_ms"] = connection_offset_ms
                    self.emit("caption", event)
        finally:
            if self.stop_event.is_set() and started:
                self._finish_connection(connection, converter, task_id, header_sent)
            try:
                connection.close()
            finally:
                self.socket = None

    def _send_audio(self, connection, converter, header_sent):
        sent_chunks = 0
        while sent_chunks < 8 and not self.stop_event.is_set():
            try:
                packet = self.audio_queue.get_nowait()
            except queue.Empty:
                break
            if packet.gap_before_frames:
                converter.reset()
                self.emit("status", {
                    "state": "audio_gap",
                    "dropped_frames": packet.gap_before_frames,
                })
            for pcm in converter.feed(packet.data):
                if not header_sent:
                    connection.send_binary(wav_stream_header())
                    header_sent = True
                connection.send_binary(pcm)
                sent_chunks += 1
        return header_sent

    def _finish_connection(self, connection, converter, task_id, header_sent):
        from websocket import WebSocketTimeoutException

        for pcm in converter.flush():
            if not header_sent:
                connection.send_binary(wav_stream_header())
                header_sent = True
            connection.send_binary(pcm)
        connection.send(json.dumps(finish_task_message(task_id)))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                raw = connection.recv()
            except WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw:
                break
            try:
                event = parse_asr_event(json.loads(raw))
            except (ValueError, json.JSONDecodeError, RealtimeASRError):
                continue
            if event and event["kind"] == "caption":
                event["connection_id"] = self.connection_id
                self.emit("caption", event)
            elif event and event["kind"] == "finished":
                break

    @staticmethod
    def _safe_error(error):
        if isinstance(error, RealtimeASRError):
            return str(error)
        return "实时语音连接失败。请检查网络、Key 和播放设备后重试。"

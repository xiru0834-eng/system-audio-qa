import json
import queue
import unittest
import threading
import time

import httpx

from dashscope_client import DashScopeClient, dashscope_endpoint
from openai_client import ApiError
from realtime_asr import parse_asr_event, run_task_message, wav_stream_header
from realtime_asr import DashScopeRealtimeASR
from realtime_audio import BoundedAudioQueue


class RealtimeProtocolTests(unittest.TestCase):
    def test_endpoint_is_workspace_and_region_specific(self):
        self.assertEqual(
            dashscope_endpoint("workspace-123", "北京"),
            "https://workspace-123.cn-beijing.maas.aliyuncs.com",
        )
        self.assertEqual(
            dashscope_endpoint("workspace-123", "新加坡"),
            "https://workspace-123.ap-southeast-1.maas.aliyuncs.com",
        )
        with self.assertRaises(ValueError):
            dashscope_endpoint("bad/workspace", "北京")

    def test_run_task_uses_documented_duplex_fun_asr_protocol(self):
        message = run_task_message("task-1", sample_rate=16000)

        self.assertEqual(message["header"]["action"], "run-task")
        self.assertEqual(message["header"]["streaming"], "duplex")
        self.assertEqual(message["payload"]["model"], "fun-asr-realtime")
        self.assertEqual(message["payload"]["parameters"]["sample_rate"], 16000)
        self.assertEqual(message["payload"]["parameters"]["format"], "wav")
        self.assertNotIn("vocabulary", message["payload"]["parameters"])

    def test_streaming_wav_header_declares_pcm_16khz_mono(self):
        header = wav_stream_header()

        self.assertEqual(header[:4], b"RIFF")
        self.assertEqual(header[8:12], b"WAVE")
        self.assertEqual(header[20:22], (1).to_bytes(2, "little"))
        self.assertEqual(header[22:24], (1).to_bytes(2, "little"))
        self.assertEqual(header[24:28], (16000).to_bytes(4, "little"))
        self.assertEqual(header[34:36], (16).to_bytes(2, "little"))

    def test_interim_final_and_word_timestamps_are_parsed(self):
        result = parse_asr_event({
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {
                "sentence_id": 4,
                "begin_time": 100,
                "end_time": 900,
                "text": "请求超时后重试。",
                "sentence_begin": True,
                "sentence_end": False,
                "words": [{"begin_time": 100, "end_time": 300, "text": "请求"}],
            }}},
        })

        self.assertEqual(result["sentence_id"], 4)
        self.assertFalse(result["is_final"])
        self.assertEqual(result["words"][0]["text"], "请求")
        self.assertTrue(parse_asr_event({
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {"text": "完成。", "sentence_end": True}}},
        })["is_final"])
        self.assertIsNone(parse_asr_event({
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {"heartbeat": True}}},
        }))

    def test_worker_streams_audio_and_keeps_interim_captions_live(self):
        events = []
        caption_received = threading.Event()
        audio = BoundedAudioQueue(max_chunks=4)
        audio.put(b"\x01\x00" * 1600, frames=1600)

        class FakeSocket:
            def __init__(self):
                self.sent = []
                self.messages = queue.Queue()
                self.messages.put(json.dumps({"header": {"event": "task-started"}}))
                self.messages.put(json.dumps({
                    "header": {"event": "result-generated"},
                    "payload": {"output": {"sentence": {
                        "sentence_id": 1, "begin_time": 0, "end_time": 100,
                        "text": "并发写入会怎样？", "sentence_end": False,
                    }}},
                }))
                self.timeout = 0
                self.closed = False

            def settimeout(self, value):
                self.timeout = value

            def send(self, value):
                self.sent.append(("text", value))

            def send_binary(self, value):
                self.sent.append(("binary", value))

            def recv(self):
                try:
                    return self.messages.get(timeout=self.timeout)
                except queue.Empty:
                    for kind, value in self.sent:
                        if kind == "text" and json.loads(value)["header"]["action"] == "finish-task":
                            return json.dumps({"header": {"event": "task-finished"}})
                    from websocket import WebSocketTimeoutException
                    raise WebSocketTimeoutException("test idle")

            def close(self):
                self.closed = True

        socket = FakeSocket()
        client = DashScopeRealtimeASR(
            api_key="test-key", workspace_id="workspace-123", region="北京",
            audio_queue=audio, sample_rate=16000, channels=1,
            session_started_at=time.perf_counter(), emit=lambda kind, value: (
                events.append((kind, value)), caption_received.set() if kind == "caption" else None
            ), connection_factory=lambda *_args: socket,
        )
        client.start()
        self.assertTrue(caption_received.wait(2))
        client.stop()
        client.join(2)

        self.assertEqual(events[0][0], "status")
        self.assertEqual(next(kind for kind, _ in events if kind == "caption"), "caption")
        text_frames = [json.loads(value) for kind, value in socket.sent if kind == "text"]
        binary_frames = [value for kind, value in socket.sent if kind == "binary"]
        self.assertEqual(text_frames[0]["header"]["action"], "run-task")
        self.assertEqual(text_frames[-1]["header"]["action"], "finish-task")
        self.assertEqual(binary_frames[0][:4], b"RIFF")
        self.assertEqual(len(binary_frames[1]), 3200)
        self.assertTrue(socket.closed)

    def test_stopping_without_audio_still_finishes_the_server_task(self):
        ready = threading.Event()

        class IdleSocket:
            def __init__(self):
                self.sent = []
                self.closed = False

            def settimeout(self, _value):
                pass

            def send(self, value):
                self.sent.append(json.loads(value))

            def send_binary(self, _value):
                raise AssertionError("No audio was queued")

            def recv(self):
                if len(self.sent) == 1:
                    return json.dumps({"header": {"event": "task-started"}})
                if any(item["header"]["action"] == "finish-task" for item in self.sent):
                    return json.dumps({"header": {"event": "task-finished"}})
                from websocket import WebSocketTimeoutException
                raise WebSocketTimeoutException("idle")

            def close(self):
                self.closed = True

        socket = IdleSocket()
        client = DashScopeRealtimeASR(
            api_key="test-key", workspace_id="workspace-123", region="北京",
            audio_queue=BoundedAudioQueue(), sample_rate=16000, channels=1,
            session_started_at=time.perf_counter(),
            emit=lambda kind, _value: ready.set() if kind == "status" else None,
            connection_factory=lambda *_args: socket,
        )
        client.start()
        self.assertTrue(ready.wait(1))
        time.sleep(0.1)
        client.stop()
        client.join(2)

        self.assertEqual([item["header"]["action"] for item in socket.sent], ["run-task", "finish-task"])
        self.assertTrue(socket.closed)


class DashScopeTextTests(unittest.TestCase):
    def test_qwen_flash_streams_incremental_answer_and_usage(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, text="".join([
                'event:result\ndata:{"output":{"choices":[{"message":{"content":"我会先"}}]}}\n\n',
                'event:result\ndata:{"output":{"choices":[{"message":{"content":"复核结果。"},"finish_reason":"stop"}]},"usage":{"input_tokens":12,"output_tokens":5}}\n\n',
            ]))

        with DashScopeClient("test-key", "workspace-123", "北京",
                             transport=httpx.MockTransport(respond)) as client:
            chunks = list(client.answer(
                "两个请求同时写入会怎样？", prepared_input="本题原话：两个请求同时写入会怎样？"
            ))

        self.assertEqual(chunks, ["我会先", "复核结果。"])
        self.assertEqual(requests[0].headers["Authorization"], "Bearer test-key")
        self.assertIn("/text-generation/generation", str(requests[0].url))
        body = json.loads(requests[0].content)
        self.assertEqual(body["model"], "qwen-flash")
        self.assertTrue(body["parameters"]["incremental_output"])
        self.assertEqual(body["parameters"]["max_tokens"], 1200)
        self.assertIn("本题原话", body["input"]["messages"][1]["content"])

    def test_incomplete_qwen_stream_is_reported_as_failure(self):
        transport = httpx.MockTransport(lambda _: httpx.Response(
            200, text='data:{"output":{"choices":[{"message":{"content":"部分答案"}}]}}\n\n',
        ))
        with DashScopeClient("test-key", "workspace-123", "北京", transport=transport) as client:
            with self.assertRaises(ApiError):
                list(client.answer("题目？"))


if __name__ == "__main__":
    unittest.main()

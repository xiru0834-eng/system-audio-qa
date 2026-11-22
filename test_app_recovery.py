import json
import socket
import threading
import time
import tkinter as tk
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx

from deepseek_client import DeepSeekClient
from main import AudioQA
from self_test import wait_for
from windows_probe import prevent_test_activation


class AppRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = self.root = tk.Tk()
        root.withdraw()
        self.app = AudioQA(root, api_factory=self.local_client, memory_path=":memory:",
                           shortcut_path=Path(self.temp.name) / "hotkey.json")
        self.addCleanup(self.app.close)
        self.app.key.insert(0, "local-test-key")
        for name, value in (("company", "Test Alpha"), ("role", "AI 开发"), ("round", "二面")):
            self.app.draft[name].insert(0, value)
        self.app.replace_text(self.app.profile, "测试简历。")
        self.app.start_session()

    def local_client(self, key):
        client = DeepSeekClient(key)
        client.http.base_url = httpx.URL(f"http://127.0.0.1:{self.server.server_port}/")
        client.http.timeout = httpx.Timeout(0.6)
        return client

    def start_test_server(self):
        suite = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                mode = suite.result
                if mode == "disconnect":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if mode == "hold":
                    suite.held.set()
                    suite.release.wait(2)
                if mode == "timeout":
                    time.sleep(1)
                code = mode if isinstance(mode, int) else 200
                self.send_response(code)
                self.end_headers()
                if code != 200:
                    return
                text = "旧场结果。" if mode == "hold" else "本轮回答。"
                frames = [{"choices": [{"delta": {"content": text}, "finish_reason": None}]}]
                if mode != "partial":
                    frames.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                data = "".join(f"data: {json.dumps(item)}\n\n" for item in frames)
                try:
                    self.wfile.write((data + ("data: [DONE]\n\n" if mode != "partial" else "")).encode())
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def test_network_failures_preserve_question_and_can_retry(self):
        self.start_test_server()
        self.app.replace_text(self.app.transcript, "怎样设计缓存？")
        for mode, message in ((401, "Key 无效"), (429, "额度不足"), (503, "HTTP 503"),
                              ("disconnect", "连接失败"), ("timeout", "连接失败"), ("partial", "提前结束")):
            with self.subTest(mode=mode):
                self.result = mode
                self.app.request_text()
                wait_for(lambda: not self.app.busy, self.root.update, timeout=4)
                self.assertEqual(self.app.timing["status"], "error")
                self.assertIn(message, self.app.status.cget("text"))
                self.assertEqual(self.app.transcript.get("1.0", "end-1c"), "怎样设计缓存？")
                self.result = 200
                self.app.retry_answer()
                wait_for(lambda: not self.app.busy, self.root.update, timeout=4)
                self.assertEqual(self.app.timing["status"], "done")
                self.assertEqual(self.app.answer.get("1.0", "end-1c"), "本轮回答。")

    def test_cancel_and_switch_session_drop_a_late_answer(self):
        self.start_test_server()
        self.held, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.result = "hold"
        self.app.request(question="旧场问题？")
        wait_for(self.held.is_set, self.root.update, timeout=2)
        prefix = self.app.session.prefix
        self.app.draft["company"].delete(0, "end")
        self.app.draft["company"].insert(0, "Test Beta")
        self.assertEqual(self.app.session.prefix, prefix)
        self.app.cancel_request()
        self.app.start_session()
        self.assertNotEqual(self.app.session.prefix, prefix)
        self.release.set()
        self.result = 200
        self.app.request(question="新场问题？")
        wait_for(lambda: not self.app.busy, self.root.update, timeout=4)
        self.root.update()
        self.assertEqual(self.app.answer.get("1.0", "end-1c"), "本轮回答。")
        self.assertEqual(self.app.session.history, [("新场问题？", "本轮回答。")])

    def test_stream_preserves_top_visible_line(self):
        self.root.geometry("+80+80")
        self.root.attributes("-alpha", 0)
        prevent_test_activation(self.root)
        self.root.deiconify()
        self.app.replace_text(self.app.answer, "\n".join(f"回答第 {i} 行" for i in range(80)))
        self.root.update()
        self.app.answer.yview("28.0")
        self.root.update_idletasks()
        prior = self.app.answer.index("@0,0")
        self.app.timing = {"sentence_candidates": []}
        self.app.characters_received = 0
        self.app.update_latency = time.perf_counter()
        for _ in range(30):
            self.app.response_event("chunk", (self.app.request_id, "\n" + "新的一行。\n" * 20))
            self.root.update_idletasks()
            self.assertEqual(self.app.answer.index("@0,0"), prior)

    def test_one_click_listening_shows_panel_and_stops_capture(self):
        capture = Mock(device_name="Test speakers")
        capture.buffer.meter.return_value = 0
        self.app.shortcut = None
        with patch("main.PlaybackCapture", return_value=capture) as open_capture, \
                patch.object(self.app, "show_listening_panel", wraps=self.app.show_listening_panel) as show:
            self.app.begin_button.invoke()
            wait_for(lambda: self.app.capture is capture, self.root.update, timeout=3)
            open_capture.assert_called_once()
            show.assert_called_once()
            self.assertEqual(self.app.listening_status.cget("text"), "正在监听系统音频")
            self.app.listening_stop.invoke()
            self.assertIsNone(self.app.capture)
            self.assertEqual(self.app.listening_panel.state(), "withdrawn")
            capture.close.assert_called_once()

    def test_one_click_listening_reports_connection_failure(self):
        with patch("main.PlaybackCapture", side_effect=OSError("test playback failure")):
            self.app.begin_button.invoke()
            wait_for(lambda: "无法开始缓冲" in self.app.status.cget("text"), self.root.update, timeout=3)
            self.assertIsNone(self.app.capture)
            self.assertIsNone(self.app.listening_panel)
            self.assertFalse(self.app.begin_button.instate(("disabled",)))

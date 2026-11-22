import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import tkinter as tk

from main import AudioQA, select_startup_provider


class LiveCaptionAppTests(unittest.TestCase):
    def test_startup_uses_saved_deepseek_when_realtime_service_is_unconfigured(self):
        self.assertEqual(
            select_startup_provider(
                {"DeepSeek": True, "OpenAI": False, "阿里云百炼": False},
                preferred=None,
                workspace_id="",
            ),
            "DeepSeek",
        )

    def test_startup_prefers_configured_realtime_service_over_fallback(self):
        self.assertEqual(
            select_startup_provider(
                {"DeepSeek": True, "OpenAI": False, "阿里云百炼": True},
                preferred=None,
                workspace_id="workspace-123",
            ),
            "阿里云百炼",
        )

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = AudioQA(
            self.root,
            shortcut_path=Path(self.temp.name) / "hotkey.json",
            memory_path=":memory:",
        )
        self.app.draft["company"].insert(0, "Test company")
        self.app.draft["role"].insert(0, "AI engineer")
        self.app.draft["round"].insert(0, "一面")
        self.app.replace_text(self.app.profile, "已确认项目资料")
        self.assertTrue(self.app.start_session())
        self.root.update_idletasks()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_caption_updates_live_preview_without_requesting_answer(self):
        self.app.asr_state = "connected"
        prior_request_id = self.app.request_id
        self.app._on_asr_caption({
            "connection_id": 1,
            "sentence_id": 2,
            "begin_ms": 0,
            "end_ms": 800,
            "text": "你怎么验证结果？",
            "is_final": True,
            "words": [
                {"begin_time": 0, "end_time": 800, "text": "你怎么验证结果？"},
            ],
        })

        self.assertEqual(self.app.transcript.get("1.0", "end-1c"), "你怎么验证结果？")
        self.assertIn("你怎么验证结果？", self.app.caption_preview.cget("text"))
        self.assertEqual(self.app.request_id, prior_request_id)
        self.assertFalse(self.app.busy)

    def test_answer_button_freezes_caption_cutoff_and_uses_question_turn(self):
        self.app.capture = Mock()
        self.app.capture.buffer = Mock()
        self.app.asr_state = "connected"
        self.app.asr_started_at = time.perf_counter() - 2
        self.app._on_asr_caption({
            "connection_id": 1,
            "sentence_id": 1,
            "begin_ms": 0,
            "end_ms": 1000,
            "text": "先读状态再执行。",
            "is_final": True,
            "words": [],
        })
        with patch.object(self.app, "request") as request:
            self.app.request_audio(triggered_at=self.app.asr_started_at + 1.5)

        request.assert_called_once()
        turn = request.call_args.kwargs["turn"]
        self.assertEqual(turn.question, "先读状态再执行。")
        self.assertEqual(turn.cutoff_ms, 1500)
        self.assertTrue(request.call_args.kwargs["allow_parallel"])

    def test_deepseek_answers_from_buffered_system_audio_on_manual_trigger(self):
        self.app.capture = Mock()
        self.app.capture.buffer.snapshot.return_value = b"recorded-loopback"
        self.app.provider.set("DeepSeek")
        self.app.asr_state = "text_only"

        with patch.object(self.app, "request") as request:
            self.app.request_audio(triggered_at=time.perf_counter())

        request.assert_called_once()
        self.assertIs(request.call_args.kwargs["buffer"], self.app.capture.buffer)
        self.assertEqual(request.call_args.kwargs["audio"], b"recorded-loopback")

    def test_listening_panel_exposes_question_answer_and_caption_separately(self):
        self.app.build_listening_panel()
        self.app.replace_text(self.app.transcript, "当前题目")
        answer = "\n".join(f"第 {number} 句。先给做法，再讲依据与验证。" for number in range(1, 19))
        self.app.replace_text(self.app.answer, answer[:160])
        self.app.live_transcript = "最近字幕"
        self.app.update_listening_panel()
        self.app.append_answer_text(self.app.answer, answer[160:])
        self.app.update_listening_panel()
        self.app.listening_panel.update_idletasks()

        self.assertEqual(self.app.listening_question.cget("text"), "当前题目")
        self.assertEqual(self.app.listening_answer.get("1.0", "end-1c"), answer)
        self.assertEqual(self.app.listening_caption.cget("text"), "字幕：最近字幕")
        self.assertEqual(self.app.listening_panel.winfo_width(), round(430 * self.app.ui_scale))

    def test_length_choice_survives_preset_and_is_locked_for_the_round(self):
        field = self.app.draft["answer_length"]
        standard, detailed = field.cget("values")
        self.app.memory.save_preset({"company": "旧预设"})
        self.app.load_interview_preset()
        self.assertEqual(field.get(), standard)
        field.set(detailed)
        self.app.save_interview_preset()
        field.set(standard)
        self.app.load_interview_preset()
        self.assertEqual(field.get(), detailed)
        self.assertTrue(self.app.start_session(allow_generic=True))
        field.set(standard)
        self.assertIn(detailed, self.app.session.prefix)


if __name__ == "__main__":
    unittest.main()

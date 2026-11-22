import json
import unittest

import httpx

from deepseek_client import DeepSeekClient
from openai_client import ApiError, INSTRUCTIONS, interview_instructions


def stream(*events, done=True):
    lines = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, text=lines + ("data: [DONE]\n\n" if done else ""))


class DeepSeekTests(unittest.TestCase):
    def test_fixed_language_is_given_as_a_top_level_instruction(self):
        prepared = '{"locked_interview":{"language":"中文"}}\n{"question_or_transcript":"Why?"}'
        self.assertIn("简体中文回答，哪怕当前问题全部是英文", interview_instructions(prepared))
        self.assertEqual(interview_instructions('{"locked_interview":{"language":"自动"}}\n{}'), INSTRUCTIONS)

    def test_interview_prompt_separates_known_facts_from_unrecorded_details(self):
        for requirement in (
            "Do not infer a test design",
            "does not mean it did not happen",
            "A measured outcome does not prove the measurement method",
            "Do not convert an unrecorded fact into a confident negative claim",
            "For a missing selection rationale",
            "Start with one directly useful sentence",
        ):
            self.assertIn(requirement, INSTRUCTIONS)

    def test_prompt_requests_a_complete_spoken_answer_with_a_point_of_view(self):
        self.assertIn("8–12 sentence", INSTRUCTIONS)
        self.assertIn("point of view", INSTRUCTIONS)
        prepared = ('{"locked_interview":{"language":"中文",'
                    '"answer_length":"详细（12–18句）"}}\n{}')
        self.assertIn("12–18 句", interview_instructions(prepared))

    def test_transcription_and_answer_use_official_endpoints_and_same_key(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            self.assertEqual(request.url.host, "api.deepseek.com")
            self.assertEqual(request.headers["Authorization"], "Bearer example-test-key")
            if request.url.path == "/v1/tools/asr":
                self.assertIn(b'filename="playback.wav"', request.content)
                self.assertIn(b"RIFF", request.content)
                return httpx.Response(200, json={"text": "项目怎么做的？"})
            body = json.loads(request.content)
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["temperature"], 0.2)
            self.assertEqual(body["max_tokens"], 1200)
            self.assertTrue(body["stream"])
            self.assertEqual(body["messages"][1]["content"], "locked prefix\nquestion")
            return stream(
                {"choices": [{"delta": {"content": "回答"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            )

        with DeepSeekClient("example-test-key", transport=httpx.MockTransport(handler)) as client:
            question = client.transcribe(b"RIFF")
            self.assertEqual(question, "项目怎么做的？")
            self.assertEqual("".join(client.answer(question, prepared_input="locked prefix\nquestion")), "回答")
        self.assertEqual(paths, ["/v1/tools/asr", "/v1/chat/completions"])

    def test_deep_mode_ignores_reasoning_and_reports_truncation(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["thinking"], {"type": "enabled"})
            self.assertEqual(body["reasoning_effort"], "high")
            self.assertNotIn("temperature", body)
            return stream(
                {"choices": [{"delta": {"reasoning_content": "private"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "回答"}, "finish_reason": "length"}]},
            )

        with DeepSeekClient("example-test-key", transport=httpx.MockTransport(handler)) as client:
            result = client.answer("question", deep=True)
            self.assertEqual(next(result), "回答")
            with self.assertRaises(ApiError):
                next(result)

    def test_premature_disconnect_and_insufficient_balance(self):
        def handler(request):
            if request.url.path.endswith("/asr"):
                return httpx.Response(402)
            return stream({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
                          done=False)

        with DeepSeekClient("example-test-key", transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ApiError, "余额"):
                client.transcribe(b"RIFF")
            with self.assertRaisesRegex(ApiError, "提前结束"):
                list(client.answer("question"))

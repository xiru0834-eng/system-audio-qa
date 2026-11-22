import io
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from zipfile import ZipFile

import httpx

from audio_capture import AudioBuffer, NoAudio, playback_devices
from openai_client import ApiError, OpenAIClient
from profile_text import WORD, read_profile


class AudioTests(unittest.TestCase):
    def test_only_playback_devices(self):
        desktop = {"index": 11, "name": "Speakers [Loopback]", "isLoopbackDevice": True}
        mic = {"index": 2, "name": "Microphone", "isLoopbackDevice": False}

        class Audio:
            def get_default_wasapi_loopback(self):
                return desktop

            def get_loopback_device_info_generator(self):
                return iter((mic, desktop))

        devices, default = playback_devices(Audio())
        self.assertEqual(devices, {11: desktop})
        self.assertEqual(default, 11)

    def test_latest_bounded_audio_is_a_mono_wav(self):
        audio = AudioBuffer(48000, 2, max_seconds=2, clock=lambda: 1.0)
        audio.append(struct.pack("<hh", -1000, -1000) * 48000)
        audio.append(struct.pack("<hh", 1200, 1200) * 48000 * 2)
        with wave.open(io.BytesIO(audio.wav(1))) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels()), (16000, 1))
            self.assertEqual(wav.getnframes(), 16000)
            self.assertEqual(wav.readframes(1), struct.pack("<h", 1200))
        self.assertEqual(audio.frame_count, 96000)

    def test_silent_and_empty_audio_do_not_make_a_request(self):
        audio = AudioBuffer(48000, 2)
        with self.assertRaises(NoAudio):
            audio.wav(15)
        audio.append(bytes(48000 * 4))
        with self.assertRaises(NoAudio):
            audio.wav(15)

    def test_snapshot_excludes_audio_older_than_the_lookback(self):
        now = 1
        audio = AudioBuffer(16000, 1, clock=lambda: now)
        audio.append(struct.pack("<h", 1024) * 16000)
        now += 15
        with self.assertRaises(NoAudio):
            audio.wav(15)

    def test_snapshot_ends_at_trigger_even_if_ui_handles_it_later(self):
        now = 10
        audio = AudioBuffer(16000, 1, clock=lambda: now)
        before = struct.pack("<h", 800) * 16000
        after = struct.pack("<h", 1400) * 16000
        audio.append(before)
        now += 1
        audio.append(after)
        self.assertEqual(audio.snapshot(1, ended_at=10), before)
        self.assertEqual(audio.snapshot(0.75, ended_at=10.5),
                         before[-8000:] + after[:16000])


class ProfileTests(unittest.TestCase):
    def test_text_and_word_import(self):
        with tempfile.TemporaryDirectory() as directory:
            text_file = Path(directory) / "项目.txt"
            text_file.write_bytes("系统集成与测试".encode("gb18030"))
            self.assertEqual(read_profile(text_file), "系统集成与测试")
            docx = Path(directory) / "resume.docx"
            namespace = WORD[1:-1]
            with ZipFile(docx, "w") as archive:
                archive.writestr("word/document.xml", (
                    f'<w:document xmlns:w="{namespace}"><w:body><w:p>'
                    "<w:r><w:t>Developed a service.</w:t></w:r>"
                    "</w:p></w:body></w:document>"
                ))
            self.assertEqual(read_profile(docx), "Developed a service.")


class ApiTests(unittest.TestCase):
    def test_transcribe_then_stream_personalized_answer(self):
        requests = []

        def respond(request):
            requests.append(request.url.path)
            self.assertEqual(request.url.host, "api.openai.com")
            self.assertEqual(request.headers["Authorization"], "Bearer local-test-key")
            if request.url.path.endswith("/transcriptions"):
                self.assertIn(b"gpt-transcribe", request.content)
                self.assertIn(b"RIFF", request.content)
                self.assertNotIn(b"demo-resume", request.content)
                return httpx.Response(200, json={"text": "Explain your cache."})
            payload = json.loads(request.content)
            self.assertFalse(payload["store"])
            self.assertTrue(payload["stream"])
            self.assertIn("demo-resume", payload["input"])
            self.assertIn("Explain your cache.", payload["input"])
            events = [
                {"type": "response.output_text.delta", "delta": "I used "},
                {"type": "response.output_text.delta", "delta": "Redis."},
                {"type": "response.completed"},
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))

        with OpenAIClient("local-test-key", transport=httpx.MockTransport(respond)) as api:
            self.assertEqual(requests, [])
            transcript = api.transcribe(b"RIFF")
            self.assertEqual("".join(api.answer(transcript, "demo-resume")), "I used Redis.")
        self.assertEqual(requests, ["/v1/audio/transcriptions", "/v1/responses"])

    def test_incomplete_stream_is_not_reported_as_success(self):
        transport = httpx.MockTransport(lambda _: httpx.Response(
            200, text='data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
        ))
        with OpenAIClient("local-test-key", transport=transport) as api:
            with self.assertRaises(ApiError):
                list(api.answer("Question", ""))

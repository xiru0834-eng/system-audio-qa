"""Opt-in Windows smoke test. Renders test speech and uses a mocked API."""

import audioop
import base64
import ctypes
import io
import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pyaudiowpatch as pyaudio

from audio_capture import AudioBuffer, PlaybackCapture
from hotkey import DEFAULT, HOTKEY_ID, MOD_ALT, MOD_CONTROL, MOD_SHIFT, WM_HOTKEY
from hotkey import Hotkey, Shortcut, load_shortcut, user32
from deepseek_client import DeepSeekClient
from window_privacy import EXCLUDE, native_window, read_affinity, set_privacy
from windows_probe import capture_probe, press_shift_z, prevent_test_activation, raise_test_window


def wait_for(predicate, update=lambda: None, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Smoke test condition timed out")
        update()
        time.sleep(0.02)


def launch_args(flag, argument):
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).with_name("main.py")))
    return [*command, flag, str(argument)]


def peer(folder):
    workspace = Path(folder)
    capture = PlaybackCapture()
    try:
        (workspace / "ready").write_text(capture.device_name, encoding="utf-8")
        deadline = workspace / "deadline-seconds"
        wait_for((workspace / "measured").is_file, timeout=float(deadline.read_text()) if deadline.exists() else 15)
        (workspace / "frames").write_text(str(capture.buffer.frame_count), encoding="utf-8")
    finally:
        capture.close()


def render_playback(source=None):
    audio = pyaudio.PyAudio()
    try:
        host = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        device = audio.get_device_info_by_index(host["defaultOutputDevice"])
        rate = int(device["defaultSampleRate"])
        channels = int(device["maxOutputChannels"])
        stream = audio.open(
            format=pyaudio.paInt16, channels=channels, rate=rate,
            output=True, output_device_index=device["index"],
        )
        try:
            if source is None:
                pcm = bytes(rate * channels * 2)
            else:
                with wave.open(str(source), "rb") as spoken:
                    assert spoken.getsampwidth() == 2 and spoken.getnchannels() == 1
                    pcm, _ = audioop.ratecv(spoken.readframes(spoken.getnframes()), 2, 1,
                                            spoken.getframerate(), rate, None)
                    pcm = audioop.mul(pcm, 2, 0.18)
                    pcm = b"".join(pcm[i:i + 2] * channels for i in range(0, len(pcm), 2))
            stream.write(pcm)
        finally:
            stream.close()
    finally:
        audio.terminate()


def check_concurrent_capture(app):
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        process = subprocess.Popen(launch_args("--loopback-peer", folder),
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for(lambda: (folder / "ready").is_file() or process.poll() is not None)
            assert (folder / "ready").read_text(encoding="utf-8") == app.capture.device_name
            render_playback()
            (folder / "measured").touch()
            assert process.wait(timeout=15) == 0
            first = app.capture.buffer.frame_count
            second = int((folder / "frames").read_text(encoding="utf-8"))
            assert first > 0 and second > 0, (first, second)
            return {"first_process_frames": first, "second_process_frames": second}
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def verify():
    from main import AudioQA

    calls = []
    payloads = []
    speech_stats = {}
    question = "你是怎么降低接口延迟的？"
    answer = "我用 Redis 缓存了重复查询，先减少数据库往返。"
    mock_transcript = question

    def mock_api(request):
        calls.append(request.url.path)
        assert request.url.host == "api.deepseek.com"
        if request.url.path == "/v1/tools/asr":
            multipart = request.content
            start = multipart.index(b"RIFF")
            length = int.from_bytes(multipart[start + 4:start + 8], "little") + 8
            with wave.open(io.BytesIO(multipart[start:start + length])) as submitted:
                pcm = submitted.readframes(submitted.getnframes())
                if not speech_stats:
                    speech_stats.update(real_playback_duration_ms=round(len(pcm) / 32),
                                        real_playback_rms=audioop.rms(pcm, 2))
                assert audioop.rms(pcm, 2) > 25
            return httpx.Response(200, json={"text": mock_transcript})
        body = json.loads(request.content)
        prefix, suffix = body["messages"][1]["content"].split("\n", 1)
        locked = json.loads(prefix)["locked_interview"]
        assert locked["company"] == "Demo Company" and locked["round"] == "二面"
        assert locked["language"] == "中文"
        context = json.loads(suffix)
        assert "resume" not in locked
        assert "Redis" in context["candidate_sources"][0]["text"]
        assert context["question_or_transcript"] == question
        payloads.append((prefix, body["thinking"]["type"]))
        events = [
            {"choices": [{"delta": {"content": answer[:8]}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": answer[8:]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events)
                              + "data: [DONE]\n\n")

    root = tk.Tk()
    root.withdraw()
    workspace = tempfile.TemporaryDirectory()
    shortcut_path = Path(workspace.name) / "hotkey.json"
    app = AudioQA(root, api_factory=lambda key: DeepSeekClient(key, transport=httpx.MockTransport(mock_api)),
                  shortcut_path=shortcut_path, memory_path=":memory:")
    alternative = None
    try:
        assert app.shortcut == DEFAULT and DEFAULT.label == "Shift+Z"
        app.set_shortcut(None)
        app.key.insert(0, "local-smoke-test")
        app.provider.set("OpenAI")
        assert app.key.get() == ""
        app.provider.set("DeepSeek")
        assert app.key.get() == "local-smoke-test"
        app.profile.insert("1.0", "Demo project: used Redis to cache query results.")
        app.draft["terms"].insert(0, "RAG=Grok")
        for field, value in (("company", "Demo Company"), ("role", "AI Engineer"), ("round", "二面")):
            app.draft[field].insert(0, value)
        app.save_interview_preset()
        assert app.memory.load_preset()["company"] == "Demo Company"
        assert "resume" not in app.memory.load_preset()
        app.start_session()
        assert app.session
        app.draft["company"].delete(0, "end")
        app.draft["company"].insert(0, "New company, not yet applied")
        app.start_button.invoke()
        wait_for(lambda: app.capture is not None, root.update)
        assert app.capture.is_playback_loopback
        wasapi = app.capture.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        assert app.capture.device_index != wasapi["defaultInputDevice"]
        capture_result = check_concurrent_capture(app)
        assert calls == []

        # Desktop notifications can mix with the deliberately silent render stream.
        silent_buffer = AudioBuffer(16000, 1)
        app.request(buffer=silent_buffer, audio=bytes(16000 * 2))
        wait_for(lambda: not app.busy, root.update)
        assert calls == []
        assert "没有检测到播放声音" in app.status.cget("text")

        app.record_button.invoke()
        app.record_hotkey(SimpleNamespace(keysym="F10", state=0x5, keycode=0x79))
        primary = Shortcut(MOD_CONTROL | MOD_SHIFT, 0x79)
        assert app.shortcut == primary and load_shortcut(shortcut_path) == primary
        assert app.hotkey is not None
        conflict = None
        try:
            conflict = Hotkey(MOD_CONTROL | MOD_SHIFT, lambda: None, key=0x79)
        except OSError as error:
            assert error.winerror == 1409, error
        else:
            conflict.close()
            raise AssertionError("Windows accepted the same combination twice")
        ready = threading.Event()
        alternative = Hotkey(MOD_CONTROL | MOD_ALT, ready.set, key=0x79)
        assert user32.PostThreadMessageW(alternative.thread_id, WM_HOTKEY, HOTKEY_ID, 0)
        assert ready.wait(3)
        app.set_shortcut(Shortcut(MOD_CONTROL | MOD_ALT, 0x79))
        assert "已被占用" in app.status.cget("text")
        assert app.shortcut == primary and app.hotkey is not None

        app.set_shortcut(DEFAULT)
        assert app.hotkey is not None and app.shortcut == DEFAULT
        with tempfile.TemporaryDirectory() as folder:
            synth = Path(folder) / "question.wav"
            command = ("Add-Type -AssemblyName System.Speech; "
                       "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                       "$v=$s.GetInstalledVoices() | Where-Object "
                       "{ $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1; "
                       "if (-not $v) { throw 'No Mandarin voice installed' }; $s.SelectVoice($v.VoiceInfo.Name); "
                       "$s.SetOutputToWaveFile('question.wav'); "
                       f"$s.Speak('{question}'); $s.Dispose()")
            encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-EncodedCommand", encoded],
                           cwd=folder, check=True, timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            app.capture.buffer.clear()
            render_playback(synth)
        press_shift_z()
        wait_for(lambda: app.status.cget("text").startswith("回答已生成"), root.update)
        assert app.transcript.get("1.0", "end-1c") == question
        assert app.answer.get("1.0", "end-1c") == answer
        assert "Redis" in app.source_excerpts[0]["text"]
        assert calls == ["/v1/tools/asr", "/v1/chat/completions"]
        timing = dict(app.timing)
        app.update_preflight()
        assert "已填 Key" in app.preflight_status.cget("text")
        assert "已注册" in app.preflight_status.cget("text")
        with patch.object(app.capture.buffer, "snapshot", return_value=b"\xe8\x03" * 16000):
            app.audio_check = (time.perf_counter() - 4, app.capture)
            app.finish_audio_check()
        assert "峰值" in app.status.cget("text") and len(calls) == 2
        app.mode.set("深度")
        app.retry_button.invoke()
        wait_for(lambda: app.status.cget("text").startswith("回答已生成"), root.update)
        assert calls == ["/v1/tools/asr", "/v1/chat/completions", "/v1/chat/completions"]
        assert len(payloads) == 2 and payloads[0][0] == payloads[1][0]
        assert [mode for _, mode in payloads] == ["disabled", "enabled"]
        assert app.history_list.size() == 1
        app.history_list.selection_set(0)
        app.show_history()
        assert answer in app.history_text.get("1.0", "end-1c")
        selected = app.selected_turn_id
        app.toggle_pin()
        for index in range(12):
            app.session.record(f"Later question {index}", f"Later answer {index}")
            app.refresh_history()
            assert app.selected_turn_id == selected
        assert app.pinned_turn.id == selected and answer in app.history_text.get("1.0", "end-1c")
        app.toggle_pin()
        assert app.selected_turn_id is None
        assert app.answer_pane.grid_info()["column"] == 1
        app.toggle_compact()
        assert app.answer_pane.grid_info()["row"] == 1
        app.toggle_compact()

        mock_transcript = "Grok 里怎么减少幻觉？"
        test_samples = AudioBuffer(16000, 1)
        audible = b"\xe8\x03" * 16000
        app.request(buffer=test_samples, audio=audible)
        wait_for(lambda: app.timing.get("status") == "review", root.update)
        assert calls[-1] == "/v1/tools/asr" and len(calls) == 4
        assert app.transcript.get("1.0", "end-1c") == mock_transcript
        app.toggle_review_term()
        assert app.transcript.get("1.0", "end-1c").startswith("RAG")
        app.toggle_review_term()
        assert app.transcript.get("1.0", "end-1c") == mock_transcript
        mock_transcript = "回滚之后，你没有。"
        app.request(buffer=test_samples, audio=audible)
        wait_for(lambda: app.timing.get("status") == "review", root.update)
        assert len(calls) == 5 and calls[-1] == "/v1/tools/asr"
        assert not app.answer.get("1.0", "end-1c")
        old_capture = app.capture
        app.next_device_poll = 0
        with patch("main.default_playback_id", return_value="simulated-new-default"):
            wait_for(lambda: app.capture is not None and app.capture is not old_capture, root.update)
            assert app.capture.is_playback_loopback and app.capture.buffer is not old_capture.buffer
        assert app.session.prefix == payloads[0][0]

        card_question = "讲一下你做过的 Kubernetes 发布？"
        card_answer = "我负责 Kubernetes 发布和回滚演练。团队用的是 10% 灰度。"
        app.answer_cards.insert("1.0", f"{card_question} => {card_answer}")
        app.start_session()
        app.mode.set("快速")
        with patch.object(app, "get_key", return_value=""):
            app.request(question=card_question)
            wait_for(lambda: app.timing.get("status") == "done", root.update)
        assert len(calls) == 5 and app.timing["answer_path"] == "confirmed_card"
        assert app.answer.get("1.0", "end-1c") == card_answer
        assert app.source_excerpts == [{"source": "本场已确认口述卡", "text": card_answer}]
        mock_transcript = card_question
        app.request(buffer=test_samples, audio=audible)
        wait_for(lambda: app.timing.get("status") == "done", root.update)
        assert len(calls) == 6 and calls[-1] == "/v1/tools/asr"
        assert app.timing["answer_path"] == "confirmed_card"
        assert app.answer.get("1.0", "end-1c") == card_answer
        card_flow = dict(app.timing)

        hwnd = native_window(root)
        set_privacy(hwnd, True)
        assert read_affinity(hwnd) == EXCLUDE
        set_privacy(hwnd, False)
        assert read_affinity(hwnd) == 0
        def receiver(path):
            subprocess.run(launch_args("--capture-receiver", path), check=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)

        try:
            capture_result["capture_receiver"] = capture_probe(root, workspace.name, receiver)
        except (OSError, RuntimeError, subprocess.SubprocessError, tk.TclError) as error:
            capture_result["capture_receiver"] = {"pass": False, "error": str(error)}
        return {
            **capture_result,
            **speech_stats,
            "selected_audio_is_playback_loopback": app.capture.is_playback_loopback,
            "hotkey": "Shift+Z via Windows SendInput and the registered global hotkey",
            "stable_prefix_and_text_only_deep_retry": True,
            "preset_saved_without_resume": True,
            "question_sources_are_available_for_review": True,
            "visible_recent_history_after_mode_retry": True,
            "pinned_selection_survived_12_new_turns": True,
            "normal_and_compact_layout_grid": True,
            "local_preflight_without_cloud_call": True,
            "transcript_review_blocks_automatic_generation": True,
            "default_change_reopens_and_clears_audio_test": "simulated endpoint ID, real WASAPI reopen",
            "native_capture_affinity_enable_disable": True,
            "timing_mock_transport_ms": timing,
            "windows_hotkey_conflict": 1409,
            "custom_hotkey_saved_and_restored_after_conflict": True,
            "alternative_hotkey_delivered": True,
            "no_network_on_silence": True,
            "confirmed_card_text_no_api_key_and_no_network": True,
            "confirmed_card_audio_uses_asr_only": True,
            "confirmed_card_mock_asr_ui_timing_ms": card_flow,
            "ui_api_flow": "mock transcription and streamed answer passed",
        }
    finally:
        if alternative is not None:
            alternative.close()
        app.close()
        workspace.cleanup()


def receive_gdi_samples(folder):
    subprocess.run(launch_args("--capture-receiver", folder), check=True, timeout=15,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def verify_privacy(receiver=receive_gdi_samples, transport="GDI BitBlt"):
    from main import AudioQA

    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        app = AudioQA(root, shortcut_path=folder / "hotkey.json", memory_path=":memory:")
        try:
            root.geometry("730x620+36+70")
            app.source_view.geometry("325x250+300+125")
            app.tabs.select(app.qa_tab)
            app.source_excerpts = [{"source": "Synthetic reference", "text": "Privacy probe"}]
            backdrop = tk.Toplevel(root, background="#284e93")
            backdrop.withdraw()
            backdrop.overrideredirect(True)
            backdrop.geometry("650x270+12+70")
            background_hwnd = prevent_test_activation(backdrop)
            windows = (root, app.source_view)
            colors = ("#13da71", "#cc38b4")
            markers = []
            for window, color in zip(windows, colors):
                marker = tk.Frame(window, background=color)
                marker.place(x=6, y=40, width=24, height=24)
                marker.lift()
                markers.append(marker)
                prevent_test_activation(window)
            root.deiconify()
            backdrop.deiconify()
            root.update()
            raise_test_window(background_hwnd)
            raise_test_window(native_window(root))
            marker_pixels = (0x71DA13, 0xB438CC)
            records = []
            for step in range(11):
                enabled = bool(step % 2)
                if step in (3, 8):
                    app.toggle_compact()
                app.source_view.withdraw()
                app.privacy.set(not enabled)
                app.privacy_toggle.invoke()
                assert app.privacy.get() == enabled
                assert app.privacy_caption.get() != "保护未确认"
                assert read_affinity(native_window(app.source_view)) == (EXCLUDE if enabled else 0)
                app.show_sources()
                root.update()
                raise_test_window(native_window(app.source_view))
                assert app.source_view.winfo_viewable() and root.winfo_viewable()
                affinity = [read_affinity(native_window(window)) for window in windows]
                assert affinity == [EXCLUDE if enabled else 0] * len(windows)
                coordinates = [[item.winfo_rootx() + 12, item.winfo_rooty() + 12] for item in markers]
                (folder / "coordinates.json").write_text(json.dumps(coordinates), encoding="utf-8")
                (folder / "samples.json").unlink(missing_ok=True)
                ctypes.windll.dwmapi.DwmFlush()
                receiver(folder)
                pixels = json.loads((folder / "samples.json").read_text(encoding="ascii"))
                assert len(pixels) == len(markers)
                visibility = [pixel == expected for pixel, expected in zip(pixels, marker_pixels)]
                records.append({"enabled": enabled, "compact": app.compact,
                                "affinity_main_reference": affinity,
                                "marker_visible_main_reference": visibility,
                                "background_visible_main_reference": [pixel == 0x934E28 for pixel in pixels]})
            return {"transport": transport, "protection_on_tests": 5, "protection_off_tests": 6,
                    "both_windows_remained_mapped": True, "records": records,
                    "pass": all(row["marker_visible_main_reference"] == [not row["enabled"]] * 2
                                and row["background_visible_main_reference"] == [row["enabled"]] * 2
                                for row in records)}
        finally:
            app.close()


def verify_one_click_listening():
    from main import AudioQA

    calls = []
    answer_parts = (
        "我会先处理拖一分钟就会造成不可逆损失的那一件。"
        "然后我会问清两件事各自的截止时间和影响范围。"
        "一件事如果已经影响线上用户，另一件只是内部交付，我会先找能止损的动作。",
        "\n\n同时我会给另一个负责人一个具体的回复时间，并请能接手的同事先把信息收齐。"
        "如果两件事都影响用户，我会先看哪边的故障范围还在扩大。"
        "比如一边只是报表延迟，另一边已经不能接单，我会先保证用户能提交订单。",
        "\n\n止损后，我会用实际指标确认影响已经收敛，比如核对订单失败率是否回到正常区间。"
        "确认以后再回到第二件事，重新核对时间和人手。"
        "如果第二件事也有马上到期的外部承诺，我会协调并行处理。",
    )
    answer = "".join(answer_parts)

    def mock_api(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/tools/asr":
            start = request.content.index(b"RIFF")
            length = int.from_bytes(request.content[start + 4:start + 8], "little") + 8
            with wave.open(io.BytesIO(request.content[start:start + length])) as wav:
                pcm = wav.readframes(wav.getnframes())
                assert audioop.rms(pcm, 2) > 25
            return httpx.Response(200, json={"text": "两件紧急的事同时找上你，你怎么安排？"})
        events = [{"choices": [{"delta": {"content": part}, "finish_reason": None}]} for part in answer_parts]
        events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        data = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=data + "data: [DONE]\n\n")

    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch)
        app = AudioQA(root, api_factory=lambda key: DeepSeekClient(key, transport=httpx.MockTransport(mock_api)),
                      shortcut_path=folder / "hotkey.json", memory_path=":memory:")
        try:
            app.provider.set("DeepSeek")
            app.key.insert(0, "local-smoke-test")
            original_build = app.build_listening_panel

            def build_without_activation():
                original_build()
                prevent_test_activation(app.listening_panel)

            with patch.object(app, "build_listening_panel", side_effect=build_without_activation):
                started = time.perf_counter()
                app.begin_button.invoke()
                wait_for(lambda: app.capture is not None and app.listening_panel is not None, root.update)
                connection_ms = round((time.perf_counter() - started) * 1000, 1)
            root.update()
            assert app.listening_panel.winfo_viewable()
            assert app.listening_status.cget("text") == "正在监听系统音频"
            assert app.listening_panel.winfo_width() >= 404 * app.ui_scale - 1
            capture = app.capture
            assert capture.is_playback_loopback
            wasapi = capture.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            assert capture.device_index != wasapi["defaultInputDevice"]
            assert not calls

            sample = folder / "sample.wav"
            rate = 16000
            with wave.open(str(sample), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                wav.writeframes(b"".join(
                    int(1600 * math.sin(2 * math.pi * 330 * offset / rate)).to_bytes(2, "little", signed=True)
                    for offset in range(rate // 2)
                ))
            capture.buffer.clear()
            render_playback(sample)
            frames = capture.buffer.frame_count
            peak = audioop.max(capture.buffer.snapshot(4), 2)
            assert peak > 25, (capture.device_name, frames, peak)
            assert app.hotkey is not None, app.capture_label.cget("text")
            assert user32.PostThreadMessageW(app.hotkey.thread_id, WM_HOTKEY, HOTKEY_ID, 0)
            try:
                wait_for(lambda: app.timing.get("status"), root.update, timeout=4)
            except TimeoutError as error:
                raise AssertionError((app.status.cget("text"), app.timing, calls)) from error
            assert app.timing["status"] == "done", (app.status.cget("text"), app.timing, calls)
            assert app.listening_answer.get("1.0", "end-1c") == answer
            assert calls == ["/v1/tools/asr", "/v1/chat/completions"]
            app.listening_stop.invoke()
            assert app.capture is None and app.hotkey is None and capture.stream is None
            assert not app.listening_panel.winfo_viewable()
            return {"connect_ms": connection_ms, "real_loopback_frames": frames, "peak_pcm": peak, "microphone_selected": False,
                    "hotkey": "registered Windows Shift+Z with a posted hotkey message",
                    "api": "mock transcription and streamed response", "answer_in_panel": True,
                    "answer_characters": len(answer), "answer_chunks": len(answer_parts),
                    "capture_released_on_stop": True}
        finally:
            app.close()


def run_listening(path):
    run(path, check=verify_one_click_listening)


def run_privacy(path):
    run(path, check=verify_privacy)


def run(path, *, check=verify):
    report = Path(path)
    try:
        details = check()
        result = {"ok": details.get("pass", True), **details}
    except Exception:
        report.write_text(json.dumps({"ok": False, "traceback": traceback.format_exc()}, indent=2),
                          encoding="utf-8")
        raise SystemExit(1)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not result["ok"]:
        raise SystemExit(1)

"""Opt-in live checks for quiet speech, long pauses and early triggering."""

import audioop
import getpass
import os
import sys
import tkinter as tk
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark_deepseek import PROFILES, synthesize
from hotkey import HOTKEY_ID, WM_HOTKEY, user32
from main import AudioQA
from retest_ms import answer_result, trigger_and_wait, write_report
from self_test import render_playback, wait_for


def wav_parts(path):
    with wave.open(str(path), "rb") as stream:
        return stream.getparams(), stream.readframes(stream.getnframes())


def write_wav(path, params, pcm):
    with wave.open(str(path), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(pcm)


def verify(output, key):
    phrases = [
        ("P91", "A", "你在客服检索项目里负责哪部分？", "quiet source"),
        ("P92", "A", "先等一下。", "before the pause"),
        ("P93", "A", "回滚之后怎么确认数据没出问题？", "after the pause"),
        ("P94", "A", "回滚之后，你没有", "before the hotkey"),
        ("P95", "A", "核实过数据库一致性吗？", "last words after the hotkey"),
    ]
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        synthesize(phrases, folder)
        params, pcm = wav_parts(folder / "P91.wav")
        write_wav(folder / "quiet.wav", params, audioop.mul(pcm, 2, 0.25))
        params, lead = wav_parts(folder / "P92.wav")
        tail_params, tail = wav_parts(folder / "P93.wav")
        assert params[:3] == tail_params[:3]
        pause = bytes(round(params.framerate * 1.8) * params.nchannels * params.sampwidth)
        write_wav(folder / "pause.wav", params, lead + pause + tail)
        root = tk.Tk()
        root.withdraw()
        app = AudioQA(root, memory_path=":memory:", shortcut_path=folder / "hotkey.json")
        cases = {}
        try:
            for field in ("company", "role", "round"):
                app.draft[field].insert(0, PROFILES["A"][field])
            app.replace_text(app.profile, PROFILES["A"]["resume"])
            app.key.insert(0, key)
            app.start_session()
            app.start_capture()
            wait_for(lambda: app.capture is not None and app.hotkey is not None, root.update)
            for name in ("quiet", "pause"):
                app.capture.buffer.clear()
                render_playback(folder / f"{name}.wav")
                trigger_and_wait(app, root)
                cases[name] = answer_result(app)
            app.capture.buffer.clear()
            render_playback(folder / "P94.wav")
            old = app.request_id
            assert user32.PostThreadMessageW(app.hotkey.thread_id, WM_HOTKEY, HOTKEY_ID, 0)
            wait_for(lambda: app.request_id != old, root.update)
            render_playback(folder / "P95.wav")
            wait_for(lambda: not app.busy, root.update, timeout=65)
            cases["early_trigger"] = answer_result(app)
            write_report(output, {"endpoint": app.capture.device_name, "cases": cases})
        finally:
            app.close()


if __name__ == "__main__":
    credential = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek credential (hidden): ").strip()
    if not credential:
        raise SystemExit("Missing credential")
    verify(Path(sys.argv[1]), credential)

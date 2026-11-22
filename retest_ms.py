"""Opt-in packaged release probes using synthetic profiles and audio."""

import ctypes
import json
import os
import subprocess
import time
import tkinter as tk
from contextlib import nullcontext
from ctypes import wintypes
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark_deepseek import PROFILES, load_cases, synthesize
from hotkey import HOTKEY_ID, MOD_ALT, MOD_CONTROL, WM_HOTKEY, Shortcut, user32
from self_test import launch_args, render_playback, wait_for


def write_report(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def trigger_and_wait(app, root, *, timeout=65):
    previous = app.request_id
    if not app.hotkey or not user32.PostThreadMessageW(app.hotkey.thread_id, WM_HOTKEY, HOTKEY_ID, 0):
        raise RuntimeError("The registered hotkey did not accept the test message")
    wait_for(lambda: app.request_id != previous and not app.busy, root.update, timeout=timeout)


def answer_result(app):
    return {"transcript": app.transcript.get("1.0", "end-1c"), "answer": app.answer.get("1.0", "end-1c"),
            "timing_ms": dict(app.timing), "status": app.status.cget("text")}


def live(path):
    from main import AudioQA

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required for the live release probe")
    cases = load_cases()
    rows = []
    deep_results = []
    report = {"method": "real WASAPI playback, registered hotkey thread, real DeepSeek, packaged window controls",
              "trigger": "PostThreadMessageW to the registered Shift+Z thread", "planned": len(cases),
              "quick_cases": rows, "depth_cases": deep_results, "finished": False}
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        synthesize(cases, folder)
        root = tk.Tk()
        root.withdraw()
        app = AudioQA(root, shortcut_path=folder / "hotkey.json", memory_path=":memory:")
        try:
            app.key.insert(0, key)
            app.start_capture()
            wait_for(lambda: app.capture is not None and app.hotkey is not None, root.update)
            report["endpoint"] = app.capture.device_name
            current_profile = None
            for case_id, profile, question, rubric in cases:
                if profile != current_profile:
                    values = PROFILES[profile]
                    for field in ("company", "role", "round", "terms"):
                        app.draft[field].delete(0, "end")
                        app.draft[field].insert(0, values.get(field, ""))
                    app.replace_text(app.profile, values["resume"])
                    app.start_session()
                    current_profile = profile
                app.capture.buffer.clear()
                render_playback(folder / f"{case_id}.wav")
                trigger_and_wait(app, root)
                row = {"id": case_id, "profile": profile, "question": question, "rubric": rubric,
                       **answer_result(app)}
                if app.timing.get("status") == "review" and app.review_replacement:
                    app.toggle_review_term()
                    app.request_text()
                    wait_for(lambda: not app.busy, root.update, timeout=65)
                    row["confirmed"] = answer_result(app)
                rows.append(row)
                write_report(path, report)

            for case_id in ("P11", "P18"):
                question = next(row[2] for row in cases if row[0] == case_id)
                app.mode.set("深度")
                app.request(question=question)
                wait_for(lambda: not app.busy, root.update, timeout=65)
                deep_results.append({"id": case_id, "question": question, **answer_result(app)})
                write_report(path, report)
            report["finished"] = True
        finally:
            write_report(path, report)
            app.close()


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (field, ctypes.c_size_t) for field in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def resident_mib():
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD)
    counts = ProcessMemory()
    counts.cb = ctypes.sizeof(counts)
    if not query(kernel.GetCurrentProcess(), ctypes.byref(counts), counts.cb):
        raise ctypes.WinError()
    return round(counts.WorkingSetSize / 1048576, 3)


class SimulatedCloud:
    def transcribe(self, _wav):
        return "稳定性测试问题？"

    def answer(self, *_args, **_kwargs):
        yield "稳定性测试回答。"


def soak(path, *, duration=3600, triggers=30):
    from main import AudioQA

    interval = duration / triggers
    root = tk.Tk()
    root.withdraw()
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        app = AudioQA(root, api_factory=lambda _: nullcontext(SimulatedCloud()),
                      shortcut_path=folder / "hotkey.json", memory_path=":memory:")
        app.set_shortcut(Shortcut(MOD_CONTROL | MOD_ALT, 0x78))  # Ctrl+Alt+F9 is separate from the live probe.
        app.key.insert(0, "local-soak-test")
        for field, value in (("company", "模拟团队"), ("role", "AI 工程师"), ("round", "二面")):
            app.draft[field].insert(0, value)
        app.replace_text(app.profile, "模拟资料。")
        app.start_session()
        peer_folder = folder / "peer"
        peer_folder.mkdir()
        (peer_folder / "deadline-seconds").write_text(str(duration + 40), encoding="ascii")
        peer = subprocess.Popen(launch_args("--loopback-peer", peer_folder), creationflags=subprocess.CREATE_NO_WINDOW)
        report = {"requested_seconds": duration, "requested_triggers": triggers,
                  "finished": False, "endpoint": "", "samples": []}
        try:
            app.start_capture()
            wait_for(lambda: app.capture is not None and app.hotkey is not None, root.update)
            wait_for((peer_folder / "ready").is_file, root.update)
            report["endpoint"] = app.capture.device_name
            started = time.monotonic()
            cpu_start = time.process_time()
            memory_start = resident_mib()
            write_report(path, report)
            for attempt in range(1, triggers + 1):
                due = started + interval * attempt
                while time.monotonic() < due:
                    root.update()
                    time.sleep(0.04)
                if peer.poll() is not None or not app.capture or not app.capture.is_active():
                    raise RuntimeError("One of the two WASAPI clients stopped")
                app.capture.buffer.clear()
                render_playback()  # The actual render endpoint plays silence with cloud transport disabled.
                trigger_and_wait(app, root, timeout=10)
                row = {"trigger": attempt, "elapsed_s": round(time.monotonic() - started, 2),
                       "result": app.timing.get("status"), "message": app.status.cget("text"),
                       "mib": resident_mib(), "primary_frames": app.capture.buffer.frame_count}
                report["samples"].append(row)
                write_report(path, report)
            (peer_folder / "measured").touch()
            wait_for((peer_folder / "frames").is_file, root.update)
            peer.wait(15)
            elapsed = time.monotonic() - started
            cpu_percent = (time.process_time() - cpu_start) * 100 / elapsed
            report.update(finished=True, elapsed_s=round(elapsed, 2), start_mib=memory_start,
                          end_mib=resident_mib(), cpu_percent_one_core=round(cpu_percent, 3),
                          cpu_percent_machine=round(cpu_percent / (os.cpu_count() or 1), 3),
                          peer_frames=int((peer_folder / "frames").read_text(encoding="ascii")))
        finally:
            if peer.poll() is None:
                peer.terminate()
                peer.wait(8)
            write_report(path, report)
            app.close()


def run(path, name):
    try:
        (live if name == "live" else soak)(Path(path))
    except Exception:
        import traceback

        failed = Path(path).with_suffix(".error.txt")
        failed.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)

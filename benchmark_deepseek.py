"""Measure real cloud calls with synthetic, independently reviewable pilot samples."""

import argparse
import base64
import getpass
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from contextlib import nullcontext
from pathlib import Path

import httpx

from audio_capture import AudioBuffer
from deepseek_client import DeepSeekClient
from openai_client import ApiError
from question_review import review_question
from session import InterviewSession


HERE = Path(__file__).resolve().parent
PROFILES = {
    "A": {"company": "模拟零售团队", "role": "AI 应用工程师", "round": "二面", "language": "中文",
          "terms": "RAG=Grok;100 并发=100并80;模型、索引=模型索引",
          "resume": "客服检索项目。本人负责 Redis 缓存设计和压测。100 并发下，p95 从 420 ms 降到 270 ms。"
                    "索引使用 HNSW。本人未负责模型微调。"},
    "B": {"company": "模拟制造团队", "role": "平台工程师", "round": "一面", "language": "中文",
          "terms": "加量=加亮",
          "resume": "本人负责 Kubernetes 发布和回滚演练。团队采用 10% 灰度。"},
}
DESKTOP_QUESTION = ("P01", "A", "你在客服检索项目里负责哪部分？", "准确说出 Redis 缓存设计和压测")
SENTENCE_END = re.compile(r"[。！？!?]|(?<!\d)\.(?!\d)")


def load_cases():
    rows = (HERE / "PILOT_CASES.md").read_text(encoding="utf-8").splitlines()
    return [tuple(field.strip() for field in row.strip("|").split("|"))
            for row in rows if re.match(r"^\| P\d{2} \|", row)]


def synthesize(cases, folder):
    manifest = folder / "cases.json"
    manifest.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8-sig")
    command = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $items = Get-Content -LiteralPath 'cases.json' -Raw | ConvertFrom-Json
    foreach ($item in $items) {
        $culture = if ($item[0] -eq 'P18') { 'en-US' } else { 'zh-CN' }
        $voice = $s.GetInstalledVoices() | Where-Object {
            $_.Enabled -and $_.VoiceInfo.Culture.Name -eq $culture
        } | Select-Object -First 1
        if (-not $voice) { throw "No installed voice for $culture" }
        $s.SelectVoice($voice.VoiceInfo.Name)
        $s.SetOutputToWaveFile($item[0] + '.wav')
        $s.Speak([string]$item[2])
        $s.SetOutputToNull()
    }
} finally { $s.Dispose() }
"""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
                   cwd=folder, check=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW)


def elapsed(start):
    return round((time.perf_counter() - start) * 1000, 1)


def run_case(api, session, case, folder):
    test_id, profile_id, expected, rubric = case
    with wave.open(str(folder / f"{test_id}.wav"), "rb") as source:
        assert source.getsampwidth() == 2
        buffer = AudioBuffer(source.getframerate(), source.getnchannels())
        pcm = source.readframes(source.getnframes())
    start = time.perf_counter()
    wav = buffer.encode(pcm)
    with wave.open(io.BytesIO(wav), "rb") as prepared:
        audio_ms = round(prepared.getnframes() * 1000 / prepared.getframerate())
    result = {"id": test_id, "profile": profile_id, "expected": expected, "rubric": rubric,
              "audio_ms": audio_ms, "encode_ms": elapsed(start)}
    try:
        transcript = api.transcribe(wav)
        result.update(transcript=transcript, transcript_ms=elapsed(start))
        review = review_question(transcript, session.aliases)
        if review.message:
            result.update(status="review", review_ms=elapsed(start), review=review.message,
                          complete_ms=elapsed(start), corrected_question=review.replacement)
            if review.replacement:
                # Simulate an explicit confirmation and measure the extra generation separately.
                # Human review and typing time are outside this benchmark.
                corrected_start = time.perf_counter()
                prepared = session.input_for(review.replacement)
                corrected = "".join(api.answer(review.replacement, prepared_input=prepared))
                result.update(confirmed_answer=corrected, confirmed_generation_ms=elapsed(corrected_start))
                session.record(review.replacement, corrected)
            return result
        prepared = session.input_for(transcript)
        answer = ""
        for chunk in api.answer(transcript, prepared_input=prepared):
            if chunk.strip():
                result.setdefault("first_text_ms", elapsed(start))
            answer += chunk
            boundary = SENTENCE_END.search(answer)
            if boundary and answer[:boundary.start()].strip():
                result.setdefault("first_sentence_boundary_ms", elapsed(start))
                result.setdefault("first_sentence", answer[:boundary.end()])
        result.update(answer=answer, complete_ms=elapsed(start), status="ok")
        session.record(transcript, answer)
    except (ApiError, httpx.HTTPError) as error:
        safe_error = str(error) if isinstance(error, ApiError) else type(error).__name__
        result.update(status="failed", error=safe_error, complete_ms=elapsed(start))
    return result


def summarize(rows):
    result = {}
    for key in ("audio_ms", "encode_ms", "transcript_ms", "first_text_ms",
                "first_sentence_boundary_ms", "complete_ms"):
        values = sorted(row[key] for row in rows if row["status"] == "ok" and key in row)
        if values:
            result[key] = {"n": len(values), "p50": values[math.ceil(len(values) * .50) - 1],
                           "p95": values[math.ceil(len(values) * .95) - 1]}
    return result


def desktop_probe(key, output, *, confirmed_card=False):
    import tkinter as tk

    from hotkey import DEFAULT
    from main import AudioQA
    from self_test import render_playback, wait_for
    from windows_probe import press_shift_z

    question = DESKTOP_QUESTION
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        synthesize([question], folder)
        root = tk.Tk()
        root.withdraw()
        app = AudioQA(root, shortcut_path=folder / "shortcut.json", memory_path=":memory:")
        try:
            app.key.insert(0, key)
            profile = PROFILES["A"]
            app.profile.insert("1.0", profile["resume"])
            if confirmed_card:
                app.answer_cards.insert("1.0", question[2] + " => 我负责 Redis 缓存设计和压测。"
                                        "100 并发时，p95 从 420 ms 降到了 270 ms。")
            for field in ("company", "role", "round"):
                app.draft[field].insert(0, profile[field])
            app.start_session()
            app.start_capture()
            wait_for(lambda: app.capture is not None, root.update)
            if app.hotkey is None or app.shortcut != DEFAULT:
                raise RuntimeError("Shift+Z is occupied")
            app.capture.buffer.clear()
            render_playback(folder / "P01.wav")
            press_shift_z()
            wait_for(lambda: bool(app.timing.get("status")), root.update, timeout=62)
            result = {
                "method": ("Windows playback → WASAPI → Shift+Z → live DeepSeek ASR → approved script → widgets"
                           if confirmed_card else
                           "Windows playback → WASAPI → Shift+Z → live DeepSeek → application widgets"),
                "build": "packaged EXE" if getattr(sys, "frozen", False) else "source-mode desktop application",
                "input": question[2],
                "transcript": app.transcript.get("1.0", "end-1c"),
                "answer": app.answer.get("1.0", "end-1c"),
                "timing_ms": app.timing,
                "status": app.status.cget("text"),
            }
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if app.timing.get("status") != "done":
                raise RuntimeError("Desktop probe did not complete")
            expected_path = "confirmed_card" if confirmed_card else "cloud"
            if app.timing.get("answer_path") != expected_path:
                raise RuntimeError("Desktop probe used an unexpected answer path")
        finally:
            app.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--packaged", type=Path)
    parser.add_argument("--card", action="store_true", help="Use an approved script in the desktop probe")
    parser.add_argument("--reuse-connection", action="store_true")
    args = parser.parse_args()
    if args.card and not (args.desktop or args.packaged):
        parser.error("--card requires --desktop or --packaged")
    key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek credential (hidden): ").strip()
    if not key:
        raise SystemExit("Missing credential")
    if args.packaged:
        child_env = dict(os.environ, DEEPSEEK_API_KEY=key)
        flag = "--live-card-probe" if args.card else "--live-desktop-probe"
        subprocess.run([str(args.packaged.resolve()), flag, str(args.output.resolve())],
                       env=child_env, check=True, timeout=95, creationflags=subprocess.CREATE_NO_WINDOW)
        return
    if args.desktop:
        desktop_probe(key, args.output, confirmed_card=args.card)
        return
    cases = load_cases()
    sessions = {name: InterviewSession(profile) for name, profile in PROFILES.items()}
    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        synthesize(cases, folder)
        shared_client = DeepSeekClient(key) if args.reuse_connection else nullcontext()
        with shared_client as client:
            for case in cases:
                with (nullcontext(client) if client else DeepSeekClient(key)) as api:
                    row = run_case(api, sessions[case[1]], case, folder)
                rows.append(row)
                print(row["id"], row["status"], row.get("transcript_ms"), row.get("first_text_ms"),
                      row["complete_ms"], flush=True)
                report = {
                    "method": "Windows TTS WAV, production ASR and streamed chat clients",
                    "client_scope": "whole run" if args.reuse_connection else "per case",
                    "mode": "DeepSeek Flash quick",
                    "completed": len(rows),
                    "successes": sum(item["status"] == "ok" for item in rows),
                    "review_count": sum(item["status"] == "review" for item in rows),
                    "planned": len(cases),
                    "percentiles_ms": summarize(rows),
                    "cases": rows,
                }
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

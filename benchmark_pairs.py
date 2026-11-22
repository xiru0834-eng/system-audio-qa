"""Paired cold versus reused HTTP-client checks with the pilot audio only."""

import getpass
import json
import math
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark_deepseek import PROFILES, load_cases, run_case, synthesize
from deepseek_client import DeepSeekClient
from session import InterviewSession


def percentiles(values):
    values = sorted(values)
    count = len(values)
    if not values:
        return {"n": 0}
    return {"n": count, "p50": values[math.ceil(count * 0.5) - 1], "p95": values[math.ceil(count * 0.95) - 1]}


def main(output):
    key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek credential (hidden): ").strip()
    if not key:
        raise SystemExit("Missing credential")
    cases = load_cases()
    planned = cases + [case for case in cases if int(case[0][1:]) % 2]
    rows = []
    report = {"method": "actual DeepSeek with same synthetic audio and isolated context in each pair",
              "planned": len(planned), "order": "alternating client scope", "pairs": rows}
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        synthesize(cases, folder)
        with DeepSeekClient(key) as reused:
            for index, case in enumerate(planned, 1):
                pair = {"pair": index, "id": case[0]}
                order = ("cold", "reused") if index % 2 else ("reused", "cold")
                for condition in order:
                    session = InterviewSession(PROFILES[case[1]])
                    if condition == "reused":
                        row = run_case(reused, session, case, folder)
                    else:
                        with DeepSeekClient(key) as cold:
                            row = run_case(cold, session, case, folder)
                    pair[condition] = {name: row[name] for name in (
                        "status", "transcript_ms", "first_text_ms", "first_sentence_boundary_ms", "complete_ms"
                    ) if name in row}
                rows.append(pair)
                print(index, pair["id"], pair["cold"]["status"], pair["reused"]["status"], flush=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    eligible = [pair for pair in rows if pair["cold"]["status"] == pair["reused"]["status"] == "ok"]
    report["summary"] = {
        metric: {condition: percentiles([pair[condition][metric] for pair in eligible])
                 for condition in ("cold", "reused")}
        for metric in ("transcript_ms", "first_text_ms", "complete_ms")
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

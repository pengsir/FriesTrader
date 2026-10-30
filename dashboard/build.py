#!/usr/bin/env python3
"""Build a self-contained, read-only dashboard from the repo's logs.

Usage: python3 dashboard/build.py [--open]
Reads trade_log.jsonl and the git history of pending_proposals.jsonl (Phase A
overwrites that file each run, so history lives in git). Writes
dashboard/dist/index.html with the data embedded. Touches nothing else.
"""
import json
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "dist" / "index.html"


def parse(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def phase_a_runs():
    runs = {}
    commits = git("log", "--format=%H", "--", "pending_proposals.jsonl").split()
    for sha in commits:
        rows = parse(git("show", f"{sha}:pending_proposals.jsonl"))
        if not rows:
            continue
        key = (rows[0].get("date"), rows[0].get("timestamp"))
        if key in runs:
            continue
        runs[key] = {
            "date": key[0],
            "timestamp": key[1],
            "screened": [r for r in rows if r.get("stage") == "screened"],
            "theses": [r for r in rows if r.get("stage") == "thesis"],
            "summary": [r for r in rows if r.get("stage") == "summary"],
        }
    return sorted(runs.values(), key=lambda r: (r["date"] or "", r["timestamp"] or ""))


def main():
    log = parse((ROOT / "trade_log.jsonl").read_text())
    data = {"phaseA": phase_a_runs(), "phaseB": log}
    tpl = (Path(__file__).parent / "template.html").read_text()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    print(f"wrote {OUT} ({len(data['phaseA'])} Phase A runs, {len(log)} Phase B log lines)")
    if "--open" in sys.argv:
        webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()

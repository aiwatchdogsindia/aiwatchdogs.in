#!/usr/bin/env python3
"""Render plain-language headlines for a JSON file of findings and check them.

Usage: uv run python scripts/plain_language_report.py samples.json [--show N] [--rule RULE] [--all]

Prints old title -> new headline + brief, then a list of problems:
  * generic fallback used (the dedicated writer raised or returned nothing)
  * headline or brief clipped with an ellipsis (source text was too long to fit)
  * neutrality-lint violation in the new text
  * a number in the new text that appears nowhere in the stored finding
Exit code is 1 when any hard problem (fallback, lint, ungrounded number) is found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aiwatchdogs.core.plain import _writer_for, plain_brief
from aiwatchdogs.core.safety import lint_neutrality

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in _NUM.findall(text):
        value = raw.replace(",", "").rstrip(".")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        out.add(value)
    return out


def _derived(source: set[str]) -> set[str]:
    """Numbers a writer may fairly derive: rounding, rupees->crore/lakh, dates, day->month/year scale."""
    out = set(source)
    floats = []
    for s in source:
        try:
            floats.append(float(s))
        except ValueError:
            pass
    for f in floats:
        for scaled in (f, f / 1e7, f / 1e5, f / 30.0, f / 30.44, f / 365.0, f / 365.25, f / 12.0, f * 100):
            for digits in (0, 1, 2):
                r = f"{round(scaled, digits):.{digits}f}"
                if "." in r:
                    r = r.rstrip("0").rstrip(".")
                out.add(r)
        if f == int(f):
            out.add(str(int(f)))
            out.add(str(int(f))[-2:])  # 2024 -> 24 (as in 2024-25)
            out.add(str(int(f)).lstrip("0"))  # 07 -> 7 in dates
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--show", type=int, default=12, help="examples to print per rule")
    ap.add_argument("--rule", default=None)
    ap.add_argument("--all", action="store_true", help="print every finding")
    args = ap.parse_args()

    findings = json.loads(Path(args.path).read_text())
    shown: dict[str, int] = {}
    hard: list[str] = []
    soft: list[str] = []
    for f in findings:
        rule = str(f.get("rule_triggered") or "")
        if args.rule and rule != args.rule:
            continue
        out = plain_brief(f)
        headline, brief = out["headline"], out["brief"]
        fid = f.get("id")
        writer = _writer_for(str(f.get("methodology_code") or "").upper())
        try:
            direct = writer(f) if writer else None
        except Exception as exc:  # noqa: BLE001
            direct = None
            hard.append(f"{fid} [{rule}] writer raised {type(exc).__name__}: {exc}")
        if not direct or not str(direct[0]).strip():
            hard.append(f"{fid} [{rule}] generic fallback used")
        if headline.endswith("…"):
            soft.append(f"{fid} [{rule}] headline clipped: {headline}")
        if brief.endswith("…"):
            soft.append(f"{fid} [{rule}] brief clipped: {brief}")
        if not brief:
            soft.append(f"{fid} [{rule}] empty brief")
        lint = lint_neutrality(headline, brief or headline, ["placeholder"])
        if not lint.passed:
            hard.append(f"{fid} [{rule}] lint: {lint.violations} :: {headline} / {brief}")
        source_text = json.dumps(f, ensure_ascii=False, default=str)
        allowed = _derived(_numbers(source_text))
        stray = sorted(n for n in _numbers(f"{headline} {brief}") if n not in allowed)
        if stray:
            hard.append(f"{fid} [{rule}] numbers not in source {stray} :: {headline} / {brief}")
        if args.all or shown.get(rule, 0) < args.show:
            shown[rule] = shown.get(rule, 0) + 1
            print(f"[{rule}] {fid}")
            print(f"  OLD: {f.get('title')}")
            print(f"  NEW: {headline}  ({len(headline)})")
            print(f"       {brief}  ({len(brief)})")
    print(f"\n{len(findings)} findings · {len(hard)} hard problems · {len(soft)} soft problems")
    for line in hard[:60]:
        print("HARD", line)
    for line in soft[:40]:
        print("SOFT", line)
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())

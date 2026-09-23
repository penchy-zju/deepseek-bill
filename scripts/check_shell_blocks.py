#!/usr/bin/env python3
"""Syntax-check every `run:` shell block inside the workflow files.

Why: a shell syntax error inside a workflow step does not stop the workflow from being
loaded, but it fails at runtime with a confusing error. Extracting each block and running
`bash -n` on it gives a precise, early signal — and it complements check_workflow.py, which
only validates the YAML structure.

Skips gracefully when bash is unavailable (e.g. a bare Windows Python), so it can live in
the same test list that runs everywhere.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def run_blocks(text: str) -> list[tuple[int, str]]:
    """Return (starting line number, shell body) for every `run: |` block."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)run:\s*\|-?\s*$", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        start = i + 2                       # 1-based line number of the first body line
        i += 1
        body: list[str] = []
        prefix = " " * (indent + 2)
        while i < len(lines):
            line = lines[i]
            if line.strip() == "":
                body.append("")
                i += 1
                continue
            if not line.startswith(prefix):
                break
            body.append(line[len(prefix):])
            i += 1
        while body and body[-1].strip() == "":
            body.pop()
        blocks.append((start, "\n".join(body)))
    return blocks


def main() -> int:
    bash = shutil.which("bash")
    if not bash:
        print("bash not found; skipping shell syntax checks")
        return 0
    if not WORKFLOWS:
        print("no workflow files found")
        return 1

    problems = 0
    total = 0
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        blocks = run_blocks(text)
        print(f"{wf.relative_to(ROOT)}: {len(blocks)} run block(s)")
        for start, body in blocks:
            if not body.strip():
                continue
            total += 1
            # capture bytes: bash may emit locale-specific bytes on stderr
            proc = subprocess.run([bash, "-n"], input=body.encode("utf-8"),
                                  capture_output=True)
            stderr = proc.stderr.decode("utf-8", "replace")
            first = body.strip().splitlines()[0][:60]
            if proc.returncode == 0:
                print(f"  OK   line {start}: {first}")
            else:
                problems += 1
                print(f"  FAIL line {start}: {first}")
                for err in stderr.strip().splitlines()[:6]:
                    print(f"        {err}")

    print(f"\n{total} block(s) checked, {problems} with syntax errors")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

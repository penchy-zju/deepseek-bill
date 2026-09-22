#!/usr/bin/env python3
"""Sanity-check the workflow file itself before it is ever committed.

A workflow with invalid YAML is silently ignored by GitHub — it stops appearing in the
Actions list and even `workflow_dispatch` disappears, which is a confusing failure mode.
This catches syntax and structural problems early. PyYAML is optional: if it is missing we
report that the deep check was skipped rather than failing the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deepseek-cron.yml"

try:
    import yaml
except ImportError:  # pragma: no cover - depends on runner image
    print("PyYAML not installed; skipping deep workflow validation")
    print("(basic file presence checked:", WORKFLOW.exists(), ")")
    raise SystemExit(0 if WORKFLOW.exists() else 1)

problems: list[str] = []

if not WORKFLOW.is_file():
    problems.append(f"{WORKFLOW} does not exist")
else:
    text = WORKFLOW.read_text(encoding="utf-8")
    # A heredoc terminator accidentally placed at column 0 is the specific mistake that
    # broke this file once: it silently ends the YAML block scalar and corrupts the doc.
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problems.append(f"YAML parse error: {exc}")
        doc = None

    if isinstance(doc, dict):
        # PyYAML implements YAML 1.1, where a bare `on:` key parses as the boolean True
        # (GitHub's own parser treats it as the string 'on'). Accept either spelling.
        triggers = doc.get("on", doc.get(True))
        if triggers is None:
            problems.append("missing 'on' trigger block (note: PyYAML parses bare 'on:' as True)")
        else:
            if "schedule" not in triggers:
                problems.append("missing schedule trigger")
            if "workflow_dispatch" not in triggers:
                problems.append("missing workflow_dispatch trigger (manual runs would be impossible)")
            for entry in triggers.get("schedule", []) or []:
                if not entry.get("cron"):
                    problems.append("schedule entry without cron")
        perms = (doc.get("permissions") or {})
        if perms.get("contents") != "write":
            problems.append("permissions.contents must be 'write' (the workflow commits data back)")
        jobs = doc.get("jobs") or {}
        steps = ((jobs.get("export-usage") or {}).get("steps")) or []
        if not steps:
            problems.append("job 'export-usage' has no steps")
        else:
            names = [s.get("name") or s.get("uses") for s in steps]
            for required in ("Checkout repository", "Publish to gh-pages"):
                if required not in names:
                    problems.append(f"expected step {required!r} is missing")
            # every step must be either a `uses` or have a `run` body
            for name, step in zip(names, steps):
                if not step.get("uses") and not step.get("run"):
                    problems.append(f"step {name!r} has neither 'uses' nor 'run'")
    elif doc is not None:
        problems.append(f"top level is {type(doc).__name__}, expected a mapping")

if problems:
    print("workflow validation FAILED:")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)

print("workflow validation OK:")
print(f"  triggers: {sorted((doc.get('on', doc.get(True)) or {}).keys())}")
print(f"  steps: {len(doc['jobs']['export-usage']['steps'])}")
for i, step in enumerate(doc["jobs"]["export-usage"]["steps"], 1):
    print(f"    {i}. {step.get('name') or step.get('uses')}")
raise SystemExit(0)

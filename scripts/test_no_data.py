#!/usr/bin/env python3
"""No-usage / no-data scenarios must be handled deliberately, not crash.

Covers the whole chain for a day (or a repo) with no data:
  * the archive contains amount.csv with only a header      -> skip, nothing committed
  * the archive's amount.csv is 0 bytes                     -> same (nothing to parse)
  * the archive has no amount.csv at all                    -> hard error
  * data/ has no days / only header-only days               -> page renders an empty state
                                                              and verification still passes
  * a header-only day next to real days                     -> real days verified normally

The last two are the important ones: verification crashing on a legitimate no-usage day
would fail the whole workflow (that is exactly what used to happen).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = "user_id,start_time_iso,end_time_iso,model,api_key_name,api_key,type,price,amount"
passed = failed = 0


def check(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS :: {label}")
    else:
        failed += 1
        print(f"  FAIL :: {label}" + (f"  -> {extra}" if extra else ""))


# ---------------------------------------------------------------- extract-step behaviour
def extract_decision(blob: bytes, cost_only: bool = False) -> dict:
    """Mirror the workflow's extract step for one day and report what it would do."""
    with zipfile.ZipFile(io.BytesIO(), "w") as _:
        pass
    names = ["cost.csv"] if cost_only else ["amount.csv"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, blob if n == "amount.csv" else "date,cost\n")
    with zipfile.ZipFile(buf) as z:
        cand = [n for n in z.namelist()
                if n.lower().endswith(".csv") and "cost" not in n.lower()
                and ("amount" in n.lower() or "usage" in n.lower())]
        if not cand:
            return {"outcome": "error", "reason": "no amount csv"}
        raw = z.read(cand[0])

    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [l for l in text.split("\n")]
    header = lines[0].lstrip("\ufeff") if lines else ""
    body = [l for l in lines[1:] if l.strip()]
    written = bool(body) and bool(header.strip())
    if not header.strip():
        return {"outcome": "emptyskip", "reason": "no header at all"}
    if not body:
        return {"outcome": "skip", "reason": "header only", "rows": 0}
    return {"outcome": "write", "rows": len(body)}


print("=== extract step: archives with nothing to parse ===")
r = extract_decision((HEADER + "\r\n").encode())
check(r["outcome"] == "skip", "header-only day -> skip (nothing committed)", str(r))
r = extract_decision(b"")
check(r["outcome"] in ("skip", "emptyskip"), "0-byte amount.csv -> skip, not a crash", str(r))
r = extract_decision(b"", cost_only=True)
check(r["outcome"] == "error", "cost-only archive -> hard error", str(r))

# ---------------------------------------------------------------- dashboard with no data
def sandbox(days: dict[str, str]) -> Path:
    """Minimal repo: scripts + generated files rebuilt from the given day files."""
    tmp = Path(tempfile.mkdtemp(prefix="nodata-")).resolve()
    (tmp / "data").mkdir()
    for name in ("scripts", "index.html", "site-data.json"):
        src = ROOT / name
        shutil.copytree(src, tmp / name) if src.is_dir() else shutil.copy(src, tmp / name)
    for date, content in days.items():
        (tmp / "data" / f"amount-{date}.csv").write_text(content, encoding="utf-8", newline="\n")
    return tmp


def build_and_verify(tmp: Path) -> tuple[int, int, str, dict]:
    env = {**__import__("os").environ, "PYTHONUTF8": "1"}
    b = subprocess.run([sys.executable, "scripts/build_site.py"], cwd=tmp,
                       capture_output=True, text=True, env=env)
    v = subprocess.run(["node", "scripts/verify_site.js"], cwd=tmp, capture_output=True, text=True)
    sd = json.loads((tmp / "site-data.json").read_text(encoding="utf-8"))
    return b.returncode, v.returncode, v.stdout, sd


print("\n=== dashboard with no usable data ===")
tmp = sandbox({})
try:
    rc_b, rc_v, out, sd = build_and_verify(tmp)
    check(rc_b == 0, "build succeeds with no data at all")
    check(sd["dates"] == [] and sd["default_date"] is None, "page has no dates", str(sd["dates"]))
    html = (tmp / "index.html").read_text(encoding="utf-8")
    check("还没有" in html, "page carries the empty-data message")
    check(rc_v == 0, "verification PASSES instead of crashing (was a TypeError)", out.strip()[-160:])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== a day that exists but has no usage ===")
tmp = sandbox({"2026-09-24": HEADER + "\n"})
try:
    rc_b, rc_v, out, sd = build_and_verify(tmp)
    check(rc_b == 0, "build succeeds with only a header-only day")
    check(sd["dates"] == [], "header-only day contributes no date to the page", str(sd["dates"]))
    check(rc_v == 0, "verification passes", out.strip()[-160:])
    check("header but no data rows" in out, "verification notes the header-only file")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== header-only day alongside real days ===")
real = (ROOT / "data" / "amount-2026-09-23.csv").read_text(encoding="utf-8")
tmp = sandbox({"2026-09-23": real, "2026-09-24": HEADER + "\n"})
try:
    rc_b, rc_v, out, sd = build_and_verify(tmp)
    check(rc_b == 0, "build succeeds")
    check(sd["dates"] == ["2026-09-23"], "only the real day appears", str(sd["dates"]))
    check(rc_v == 0, "real day still fully verified", out.strip()[-160:])
    m = re.search(r"RESULT: (\d+/\d+)", out)
    check(bool(m), "verification reported a result", out.strip()[-160:])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

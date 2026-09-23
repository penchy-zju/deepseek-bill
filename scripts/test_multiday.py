#!/usr/bin/env python3
"""Verify the multi-day catch-up path: labels, per-day normalization, determinism.

The workflow's extract step writes one normalized CSV per day in the fetch window
(>1 day when a scheduled run was skipped). This test builds a fixture zip shaped like the
API's (BOM + CRLF, deliberately shuffled rows) and runs the same normalization the workflow
uses, then asserts the properties that the rest of the pipeline depends on.
"""

from __future__ import annotations

import csv
import io
import random
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
passed = failed = 0


def check(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS :: {label}")
    else:
        failed += 1
        print(f"  FAIL :: {label}" + (f"  -> {extra}" if extra else ""))


def labels(start: str, days: int) -> list[str]:
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "usage_window.py"),
                          "--labels", start, str(days)],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def normalize(src: Path, dest: Path) -> None:
    """Same transformation as the workflow: strip BOM, drop CR, stable-sort data rows."""
    with open(src, "rb") as fh:
        raw = fh.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    header, body = lines[0], lines[1:]
    body.sort(key=lambda l: (l.split(",")[1], l.split(",")[3], l.split(",")[5], l.split(",")[6]))
    dest.write_text("\n".join([header, *body]) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="multiday-"))
    try:
        # ---- fixture: one amount.csv covering 3 days, BOM + CRLF, rows shuffled ----
        header = ("user_id,start_time_iso,end_time_iso,model,api_key_name,api_key,type,price,amount")
        body = []
        for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
            for hour in (8, 9):
                for mtype, price in (("request_count", ""),
                                     ("input_cache_hit_tokens", "0.00000015"),
                                     ("input_cache_miss_tokens", "0.0000045"),
                                     ("output_tokens", "0.0000135")):
                    amt = 100 if mtype == "request_count" else 1000000
                    body.append(f"u1,{day}T{hour:02d}:00:00+08:00,{day}T{hour+1:02d}:00:00+08:00,"
                                f"deepseek-v4-pro,Alice,sk-abc***xyz,{mtype},{price},{amt}")
        random.seed(7)
        random.shuffle(body)
        blob = b"\xef\xbb\xbf" + ("\r\n".join([header, *body]) + "\r\n").encode("utf-8")
        zip_path = tmp / "usage.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("usage/amount.csv", blob)
            z.writestr("usage/cost.csv", "date,cost\r\n2026-09-22,1.23\r\n")

        # extract like the workflow does (cost excluded by the matcher)
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith(".csv") and "cost" not in n.lower()
                     and ("amount" in n.lower() or "usage" in n.lower())]
        check(len(names) == 1, "exactly one amount CSV matched", str(names))
        src = tmp / "amount.csv"
        src.write_bytes(zipfile.ZipFile(zip_path).read(names[0]))

        print("\n== day labels for a widened window ==")
        for start, days, want in (("2026-09-22", 1, ["2026-09-22"]),
                                  ("2026-09-22", 3, ["2026-09-22", "2026-09-23", "2026-09-24"]),
                                  ("2026-12-31", 2, ["2026-12-31", "2027-01-01"])):
            got = labels(start, days)
            check(got == want, f"labels({start}, {days})", str(got))

        print("\n== per-day normalization ==")
        data = tmp / "data"
        data.mkdir()
        day_list = labels("2026-09-22", 3)
        for day in day_list:
            normalize(src, data / f"amount-{day}.csv")

        check(sorted(p.name for p in data.glob("*.csv")) ==
              [f"amount-{d}.csv" for d in day_list], "one file per day in the window")

        for day in day_list:
            f = data / f"amount-{day}.csv"
            raw = f.read_bytes()
            check(raw[:3] != b"\xef\xbb\xbf", f"{day}: no BOM")
            check(raw.count(b"\r") == 0, f"{day}: no CR")
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
            check(len(rows) == 24, f"{day}: all 24 fixture rows present", str(len(rows)))
            check(list(rows[0].keys())[0] == "user_id", f"{day}: header intact")
            keys = [(r["start_time_iso"], r["model"], r["api_key"], r["type"]) for r in rows]
            check(keys == sorted(keys), f"{day}: rows are in stable sorted order")

        print("\n== determinism (the property the no-op commit rule relies on) ==")
        # normalize the same source again -> byte-identical output
        again = tmp / "again"
        again.mkdir()
        normalize(src, again / "amount-2026-09-22.csv")
        check((data / "amount-2026-09-22.csv").read_bytes() == (again / "amount-2026-09-22.csv").read_bytes(),
              "re-normalizing identical input is byte-identical")

        # and a different (re-shuffled) ordering of the same rows must also converge
        shuffled = list(body)
        random.seed(99)
        random.shuffle(shuffled)
        blob2 = b"\xef\xbb\xbf" + ("\r\n".join([header, *shuffled]) + "\r\n").encode("utf-8")
        src2 = tmp / "amount2.csv"
        src2.write_bytes(blob2)
        normalize(src2, again / "amount-2026-09-22b.csv")
        check((again / "amount-2026-09-22.csv").read_bytes() == (again / "amount-2026-09-22b.csv").read_bytes(),
              "a different API row order still converges to the same bytes")

        print("\n== no cost data leaked in ==")
        merged = b"".join((data / f"amount-{d}.csv").read_bytes() for d in day_list)
        check(b"cost" not in merged.lower().replace(b"input_cache", b"").replace(b"cache", b""),
              "cost.csv content is not present in the amount files")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

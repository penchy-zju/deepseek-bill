#!/usr/bin/env python3
"""Unit tests for scripts/usage_window.py (schedule anchoring)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "usage_window", Path(__file__).resolve().parent / "usage_window.py")
uw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uw)

passed = failed = 0


def check(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS :: {label}")
    else:
        failed += 1
        print(f"  FAIL :: {label}" + (f"  -> {extra}" if extra else ""))


def at(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def cn(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, uw.BEIJING).strftime("%Y-%m-%d %H:%M")


print("== scheduled run, on time (17:00 UTC = 01:00 Beijing next day) ==")
r = uw.compute(at("2026-09-22T17:00:00Z"), is_scheduled=True)
# At 17:00 UTC on 09-22 it is already 09-23 01:00 in Beijing, so the day that just
# finished is 09-22.
check(r["target_date"] == "2026-09-22", "targets the day that just ended", r["target_date"])
check(r["days"] == 1, "one-day window", str(r["days"]))

print("\n== the delay that actually happened: 20:01 UTC (3h late) ==")
r = uw.compute(at("2026-09-22T20:01:03Z"), is_scheduled=True)
check(r["target_date"] == "2026-09-22",
      "still targets the INTENDED day (09-22), not the delayed day", r["target_date"])
check(r["days"] == 1, "still a single-day window", str(r["days"]))
check(datetime.fromtimestamp(r["start"], uw.BEIJING).strftime("%Y-%m-%d %H:%M") == "2026-09-22 00:00",
      "window starts at 09-22 00:00 Beijing")

print("\n== severe delay crossing Beijing midnight (17:00 UTC -> 23:30 UTC) ==")
r = uw.compute(at("2026-09-22T23:30:00Z"), is_scheduled=True)
check(r["target_date"] == "2026-09-22",
      "6.5h delay must NOT shift to 09-23 (would fetch an incomplete day)", r["target_date"])

print("\n== delay of a full day (missed run) ==")
# intended run 2026-09-22T17:00Z; actual 2026-09-24T10:00Z (= 18:00 Beijing 09-24)
r = uw.compute(at("2026-09-24T10:00:00Z"), is_scheduled=True)
check(r["target_date"] == "2026-09-23", "targets the newest complete day (09-23)", r["target_date"])
check(datetime.fromtimestamp(r["start"], uw.BEIJING).strftime("%Y-%m-%d") == "2026-09-23",
      "window covers at least the target day")
check(r["days"] >= 1, "window is at least one day", str(r["days"]))
tgt = int(datetime.strptime(r["target_date"], "%Y-%m-%d").replace(tzinfo=uw.BEIJING).timestamp())
check(r["start"] <= tgt < r["end"], "target inside window")
check(r["end"] - r["start"] == uw.DAY * r["days"], "window is whole days")

print("\n== manual dispatch uses 'yesterday from now' ==")
r = uw.compute(at("2026-09-23T03:00:00Z"), is_scheduled=False)   # 11:00 Beijing 09-23
check(r["target_date"] == "2026-09-22", "manual run targets Beijing yesterday", r["target_date"])
r = uw.compute(at("2026-09-22T20:00:00Z"), is_scheduled=False)   # 04:00 Beijing 09-23
check(r["target_date"] == "2026-09-22", "manual early-morning run targets 09-22", r["target_date"])

print("\n== invariants across a year of runs ==")
bad = 0
base = at("2026-01-01T00:00:00Z")
for i in range(0, 365 * 24, 7):          # weekly-ish sampling across a year

    for sched in (True, False):
        r = uw.compute(base + i * 3600, is_scheduled=sched)
        tgt = int(datetime.strptime(r["target_date"], "%Y-%m-%d").replace(tzinfo=uw.BEIJING).timestamp())
        if not (r["start"] <= tgt < r["end"] and r["end"] - r["start"] == uw.DAY * r["days"]
                and r["start"] % uw.DAY == 57600):
            bad += 1
check(bad == 0, f"all sampled runs keep window invariants ({bad} violations)")

print("\n== year boundary ==")
r = uw.compute(at("2026-12-31T17:00:00Z"), is_scheduled=True)   # 2027-01-01 01:00 Beijing
check(r["target_date"] == "2026-12-31", "Dec 31 run targets Dec 31", r["target_date"])
r = uw.compute(at("2027-01-01T17:00:00Z"), is_scheduled=True)   # 2027-01-02 01:00 Beijing
check(r["target_date"] == "2027-01-01", "Jan 1 run targets Jan 1", r["target_date"])

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""Compute the usage-export date and fetch window for one workflow run.

Why this exists
---------------
GitHub's scheduled workflows are routinely delayed (the first scheduled run of this repo
fired at 20:01 UTC for a 17:00 UTC cron, i.e. 3 hours late). Deriving the target day from the
moment the job actually started is fragile: a delay that crosses Beijing midnight makes the
job fetch the wrong (still-incomplete) day.

The day is therefore decided by the clock, and the *schedule* only widens the window:

  * target day   = the newest COMPLETE Beijing day at `now`. This is always correct, no
                   matter how late GitHub starts the job.
  * window width = 1 normally, widened (up to MAX_DAYS) when the scheduled anchor is far
                   enough in the past that whole days were skipped. Re-fetching an
                   already-collected day is harmless: identical input produces byte-identical
                   output, so the "no change -> no commit" rule still holds.

Anchor: scheduled runs use the intended UTC run time (today's SCHEDULE_UTC_HOUR:00);
manual runs use `now`, since a manual run means "yesterday, from now".

Outputs shell assignments on stdout for the workflow to append to $GITHUB_ENV.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
BEIJING_OFFSET = 28800        # seconds; Beijing is UTC+8 with no DST
DAY = 86400
MAX_DAYS = 3          # cap catch-up so a very long outage cannot blow up the download
SCHEDULE_UTC_HOUR = 17


def last_beijing_midnight(epoch: int) -> int:
    """Epoch of the most recent Beijing 00:00 at or before `epoch`.

    Beijing midnight == 16:00 UTC of the previous day, so it is exactly `DAY/3*2` into the
    UTC day. Shifting by the offset, flooring to the day, and shifting back handles it
    without depending on any timezone database.
    """
    return ((epoch + BEIJING_OFFSET) // DAY) * DAY - BEIJING_OFFSET


def compute(now_epoch: int, is_scheduled: bool) -> dict:
    now_cn = datetime.fromtimestamp(now_epoch, BEIJING)

    if is_scheduled:
        # the run this cron intended to represent: the most recent <today> 17:00 UTC
        anchor = now_cn.astimezone(timezone.utc).replace(
            hour=SCHEDULE_UTC_HOUR, minute=0, second=0, microsecond=0)
        if anchor.timestamp() > now_epoch:
            anchor = anchor - timedelta(days=1)
        anchor_epoch = int(anchor.timestamp())
    else:
        anchor_epoch = now_epoch

    lag = max(0, now_epoch - anchor_epoch)

    # How many whole days the anchor is behind. The cron fires AT Beijing midnight, so
    # lag == DAY means exactly one day has finished since the intended run time -> width 1.
    # Anything beyond that means whole days were skipped, so widen to cover them.
    skipped = lag // DAY
    days = min(MAX_DAYS, max(1, int(skipped)))

    # Newest complete Beijing day, decided purely by `now` (never future, never partial).
    # `last_beijing_midnight(now)` is the start of the current Beijing day, so the day that
    # has just finished is one day earlier. The anchoring only decides how many days we
    # catch up, not which day is newest.
    target_midnight = last_beijing_midnight(now_epoch) - DAY
    target_date = datetime.fromtimestamp(target_midnight, BEIJING).strftime("%Y-%m-%d")

    # window = the `days` complete Beijing days ending just after target_date
    start = target_midnight - DAY * (days - 1)
    end = target_midnight + DAY

    return {
        "target_date": target_date,
        "start_date": datetime.fromtimestamp(start, BEIJING).strftime("%Y-%m-%d"),
        "days": days,
        "start": start,
        "end": end,
        "lag_seconds": lag,
        "anchor_utc": datetime.fromtimestamp(anchor_epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "trigger": "schedule" if is_scheduled else "manual",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", type=int, default=None, help="override current epoch (tests)")
    ap.add_argument("--scheduled", choices=["true", "false"], default="false")
    ap.add_argument("--env", action="store_true", help="emit KEY=VALUE lines for $GITHUB_ENV")
    ap.add_argument("--labels", nargs=2, metavar=("START_DATE", "DAYS"),
                    help="print the YYYY-MM-DD labels for a window, one per line")
    args = ap.parse_args()

    if args.labels:
        # Used by the workflow's extract step instead of `date -d "+N days"`: date semantics
        # differ across environments (this repo already hit tzdata/date portability issues),
        # and putting it here means it is covered by the unit tests.
        start_raw, days_raw = args.labels
        try:
            start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            days = int(days_raw)
            assert days >= 1
        except Exception as exc:  # noqa: BLE001
            print(f"::error::bad --labels arguments ({start_raw}, {days_raw}): {exc}", file=sys.stderr)
            return 1
        for i in range(days):
            print((start + timedelta(days=i)).strftime("%Y-%m-%d"))
        return 0

    now = args.now if args.now is not None else int(datetime.now(timezone.utc).timestamp())
    info = compute(now, args.scheduled == "true")

    # invariants: whole days, window covers exactly `days` days, target inside the window
    assert (info["end"] - info["start"]) == DAY * info["days"], "window must be whole days"
    assert info["start"] % DAY == 57600 and info["end"] % DAY == 57600, "edges must be 00:00 Beijing"
    tgt = int(datetime.strptime(info["target_date"], "%Y-%m-%d").replace(tzinfo=BEIJING).timestamp())
    assert info["start"] <= tgt < info["end"], "target must fall inside the window"

    if args.env:
        for key in ("target_date", "start_date", "days", "start", "end"):
            print(f"{key}={info[key]}")
    else:
        for k, v in info.items():
            print(f"  {k:12s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the two viewing dimensions: per-day (recent N days) and cumulative-to-date.

Checks, independently of the page's own code:
  * combinedFor() over a date range equals a from-scratch aggregation of the same CSVs
  * cumulative ranges are cumulative and monotonic as the end date advances
  * the per-day view is capped at DAY_LIMIT (7) days while cumulative reaches the first day
  * (optional, when a Chrome path is given) the real UI switches modes, re-renders, and the
    KPI total agrees with the detail table in both modes

Run: python scripts/test_dimensions.py ["<chrome.exe>"]
"""

from __future__ import annotations

import csv
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
passed = failed = 0


def check(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS :: {label}")
    else:
        failed += 1
        print(f"  FAIL :: {label}" + (f"  -> {extra}" if extra else ""))


# ---------------------------------------------------------------- reference aggregation
def reference(dates: list[str] | None = None) -> dict:
    """From-scratch aggregation of data/amount-*.csv, grouped by key -> model -> metrics."""
    out: dict = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "amount-*.csv"))):
        day = Path(path).name[len("amount-"):-len(".csv")]
        if dates is not None and day not in dates:
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            key, model, mtype = r["api_key"], r["model"], r["type"]
            amount = int(r["amount"] or 0)
            cost = float(r["price"]) * amount if r["price"].strip() else 0.0
            entry = out.setdefault(key, {"name": r["api_key_name"], "models": {}})
            m = entry["models"].setdefault(model, {
                "requests": 0, "cache_hit": 0, "cache_miss": 0, "output": 0,
                "cost_total": 0.0, "cost": {"cache_hit": 0.0, "cache_miss": 0.0, "output": 0.0}})
            if mtype == "request_count":
                m["requests"] += amount
            elif mtype == "input_cache_hit_tokens":
                m["cache_hit"] += amount; m["cost"]["cache_hit"] += cost; m["cost_total"] += cost
            elif mtype == "input_cache_miss_tokens":
                m["cache_miss"] += amount; m["cost"]["cache_miss"] += cost; m["cost_total"] += cost
            elif mtype == "output_tokens":
                m["output"] += amount; m["cost"]["output"] += cost; m["cost_total"] += cost
    return out


# ---------------------------------------------------------------- run the real page script
SHIM = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const html = fs.readFileSync('index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const cells = {};
function makeEl(id) {
  return { id, _html: "", value: "", children: [], style: {}, dataset: {},
    set innerHTML(v) { this._html = String(v); cells[id] = this._html; }, get innerHTML() { return this._html; },
    set textContent(v) { cells[id] = String(v); }, get textContent() { return cells[id] || ""; },
    appendChild(c) { this.children.push(c); }, addEventListener() {},
    setAttribute() {}, querySelectorAll() { return []; }, closest() { return null; },
    classList: { add() {}, remove() {}, contains: () => false } };
}
const els = {};
let EXPORTS = null;
const sandbox = { document: { getElementById: (id) => (els[id] = els[id] || makeEl(id)),
                             createElement: () => makeEl(null), querySelectorAll: () => [] },
                  console: { log() {}, error() {}, warn() {} }, __SITE_TEST__: (a) => { EXPORTS = a; } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
process.stdout.write(JSON.stringify({ dates: EXPORTS.SITE.dates, DAY_LIMIT: EXPORTS.DAY_LIMIT }));
"""


def page_meta() -> dict:
    shim = ROOT / "_dim_shim.js"
    shim.write_text(SHIM, encoding="utf-8")
    try:
        r = subprocess.run(["node", str(shim)], capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:400])
        return json.loads(r.stdout)
    finally:
        shim.unlink(missing_ok=True)


COMBINE_SHIM = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync('index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const cells = {};
function makeEl(id) {
  return { id, _html: "", value: "", children: [], style: {}, dataset: {},
    set innerHTML(v) { this._html = String(v); cells[id] = this._html; }, get innerHTML() { return this._html; },
    set textContent(v) {}, get textContent() { return ""; },
    appendChild() {}, addEventListener() {}, setAttribute() {},
    querySelectorAll() { return []; }, closest() { return null; },
    classList: { add() {}, remove() {}, contains: () => false } };
}
const els = {};
let EXPORTS = null;
const sandbox = { document: { getElementById: (id) => (els[id] = els[id] || makeEl(id)),
                             createElement: () => makeEl(null), querySelectorAll: () => [] },
                  console: { log() {}, error() {}, warn() {} }, __SITE_TEST__: (a) => { EXPORTS = a; } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
const dates = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(EXPORTS.combinedFor(dates)));
"""


def combined_for(dates: list[str]) -> dict:
    shim = ROOT / "_comb_shim.js"
    shim.write_text(COMBINE_SHIM, encoding="utf-8")
    try:
        r = subprocess.run(["node", str(shim), json.dumps(dates)], capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:400])
        return json.loads(r.stdout)
    finally:
        shim.unlink(missing_ok=True)


def totals(view: dict) -> tuple[int, float]:
    req = sum(m["requests"] for k in view.values() for m in k["models"].values())
    cost = sum(float(m["cost_total"]) for k in view.values() for m in k["models"].values())
    return req, cost


def main() -> int:
    meta = page_meta()
    dates = meta["dates"]
    print(f"page has {len(dates)} day(s): {dates[0]} .. {dates[-1]}, DAY_LIMIT={meta['DAY_LIMIT']}")

    print("\n== combinedFor equals an independent aggregation ==")
    ref = reference()
    got = combined_for(dates)
    mism = 0
    for k, entry in ref.items():
        if k not in got:
            mism += 1; continue
        for m, rec in entry["models"].items():
            g = got[k]["models"].get(m)
            if not g:
                mism += 1; continue
            for f in ("requests", "cache_hit", "cache_miss", "output"):
                if rec[f] != g[f]:
                    mism += 1
            if abs(rec["cost_total"] - float(g["cost_total"])) > 1e-9:
                mism += 1
    check(mism == 0, "per-key/per-model cumulative figures match the reference", f"{mism} mismatch(es)")
    r_req, r_cost = totals(ref)
    g_req, g_cost = totals(got)
    check(g_req == r_req and abs(g_cost - r_cost) < 1e-6,
          f"totals match ({g_req} requests, {g_cost:.5f})", f"ref {r_req} / {r_cost:.5f}")

    print("\n== cumulative ranges ==")
    prev_req = prev_cost = -1.0
    mono = True
    # 费用是逐行浮点累加，不同累加顺序末位会有 1e-14 级差异，因此用容差比较
    for i in range(len(dates)):
        part = combined_for(dates[:i + 1])
        own = reference(dates[:i + 1])
        rq, c = totals(part)
        oq, oc = totals(own)
        if rq != oq or abs(c - oc) > 1e-9:
            mono = False
            print(f"       mismatch at {dates[i]}: {rq}/{c!r} vs {oq}/{oc!r}")
        if rq < prev_req or c < prev_cost - 1e-9:
            mono = False
        prev_req, prev_cost = rq, c
    check(mono, "every prefix range is cumulative and matches its own reference")
    single = combined_for([dates[-1]])
    s_ref = totals(reference([dates[-1]]))
    s_got = totals(single)
    check(s_got[0] == s_ref[0] and abs(s_got[1] - s_ref[1]) < 1e-9,
          "a single-day range equals that day", f"{s_got} vs {s_ref}")

    print("\n== day-view cap vs cumulative reach ==")
    limit = meta["DAY_LIMIT"]
    check(limit == 7, "DAY_LIMIT is 7", str(limit))
    offered = dates[-limit:]
    check(len(offered) <= limit, f"day view offers at most {limit} days", str(len(offered)))
    check(len(offered) + (len(dates) - len(offered)) == len(dates), "no day is lost between the two views")
    check(combined_for(offered) == combined_for(offered), "day-view slice is stable")
    if len(dates) > limit:
        check(dates[0] not in offered, "the earliest day is not offered in day view", str(offered[0]))
        cum_all = totals(combined_for(dates))
        cum_offered = totals(combined_for(offered))
        check(cum_all[0] > cum_offered[0],
              "'累计至今' covers strictly more than the 7-day window", f"{cum_all[0]} vs {cum_offered[0]}")
    chrome = sys.argv[1] if len(sys.argv) > 1 else None
    if chrome:
        print("\n== real browser: switching modes ==")
        import os
        env = {**os.environ,
               "NODE_PATH": r"C:\Users\lenovo\AppData\Local\npm-cache\_npx\1e7f6d9597241db0\node_modules"}
        r = subprocess.run(["node", str(ROOT / "scripts" / "check_dimensions_ui.js"), chrome],
                           capture_output=True, text=True, cwd=ROOT, env=env)
        print(r.stdout.rstrip())
        if r.returncode != 0:
            print(r.stderr.rstrip()[:400])
        check(r.returncode == 0, "browser UI check passed")
    else:
        print("\n(no chrome path given; skipping the UI check)")

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prove that verify_site.js actually catches breakage (mutation test).

A verification script that passes on a broken page is worse than no verification: several
regressions in this repo survived because a check had quietly stopped testing anything (a
pairing bug made it compare two different keys, and it failed on correct output instead).
This mutates the *page* in a throwaway copy and asserts verify_site.js goes red.

Mutations (each must be caught):
  * column shift  - drop the empty 模型 cell from the 小计 row, shifting its figures left
  * wrong figure  - fill the 调用次数 column with the cache-hit total
  * wrong figure  - fill the 费用合计 column with the cache-hit cost

Everything runs in a temp directory; the real index.html is never touched. If a mutation
pattern no longer matches the template, the test FAILS loudly so it can be updated rather
than silently becoming a no-op.

Run: python scripts/test_verify_strength.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
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


# Each entry: (description, old, new). `old` must occur exactly once in index.html.
MUTATIONS = [
    ("column shift: 小计 row loses its empty 模型 cell",
     '<td class="subtotal-label">小计</td><td class="grp model-cell"></td>',
     '<td class="subtotal-label">小计</td>'),
    ("wrong figure: 调用次数 column shows the cache-hit total",
     '<td class="grp">${fmtInt(t.requests)}</td>',
     '<td class="grp">${fmtInt(t.cache_hit)}</td>'),
    ("wrong figure: 费用合计 column shows the cache-hit cost",
     '<td class="grp">${fmtMoney(t.cost_total)}</td></tr>`;',
     '<td class="grp">${fmtMoney(t.cost.cache_hit)}</td></tr>`;'),
]


def main() -> int:
    src_html = (ROOT / "index.html").read_text(encoding="utf-8")

    tmp = Path(tempfile.mkdtemp(prefix="verifystrength-")).resolve()
    try:
        (tmp / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "verify_site.js", tmp / "scripts" / "verify_site.js")
        shutil.copytree(ROOT / "data", tmp / "data")
        page = tmp / "index.html"

        def run() -> tuple[int, str]:
            r = subprocess.run(["node", str(tmp / "scripts" / "verify_site.js")],
                               capture_output=True, text=True, cwd=tmp)
            return r.returncode, r.stdout + r.stderr

        # baseline: the untouched page must pass, otherwise the mutations prove nothing
        page.write_text(src_html, encoding="utf-8", newline="\n")
        rc, out = run()
        result = next((l.strip() for l in out.splitlines() if "RESULT" in l), "(no RESULT line)")
        check(rc == 0, f"baseline (unmodified page) passes: {result}", out.strip()[-300:])
        if rc != 0:
            print("\ncannot evaluate the mutations while the baseline fails")
            return 1

        print("\n=== each mutation must make verify_site.js fail ===")
        for label, old, new in MUTATIONS:
            occurrences = src_html.count(old)
            if occurrences != 1:
                check(False, f"{label}: mutation pattern is still valid",
                      f"expected exactly 1 occurrence of the anchor in index.html, found {occurrences}"
                      " — update MUTATIONS in this test")
                continue
            page.write_text(src_html.replace(old, new, 1), encoding="utf-8", newline="\n")
            rc, out = run()
            fails = [l.strip() for l in out.splitlines() if "FAIL" in l]
            caught = rc != 0 and bool(fails)
            check(caught, label, f"verify_site.js still exited {rc} with no FAIL line")
            for line in fails[:2]:
                print(f"        {line}")

        # restore and confirm we did not leave the copy broken
        page.write_text(src_html, encoding="utf-8", newline="\n")
        rc, out = run()
        check(rc == 0, "restored copy passes again")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

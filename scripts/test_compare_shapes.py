#!/usr/bin/env python3
"""Exercise buildCompareHtml across ALL data shapes, especially "every key multi-model".

The 小计 row and the rowspan key cell only render when a key has >1 model, so that code
path is invisible on days where every key uses a single model. This checks every shape and
verifies the rendered column geometry in a real browser.

Run: python scripts/test_compare_shapes.py ["<chrome.exe>"]
A browser is optional; without it only the HTML-structure assertions run.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------- shapes under test
def metrics(requests, tokens=1000):
    return {
        "requests": requests, "cache_hit": tokens, "cache_miss": tokens, "output": tokens,
        "cost_total": "1.5",
        "cost": {"cache_hit": "0.1", "cache_miss": "0.2", "output": "1.2"},
    }


def key(name, models: dict):
    return {"name": name, "masked": f"sk-{name}***", "models": models}


SHAPES = {
    "every key single-model": {
        "A": key("A", {"m-flash": metrics(1)}),
        "B": key("B", {"m-flash": metrics(2)}),
    },
    "one key multi-model": {
        "A": key("A", {"m-flash": metrics(1), "m-pro": metrics(2)}),
        "B": key("B", {"m-flash": metrics(3)}),
    },
    "EVERY key multi-model": {
        "A": key("A", {"m-flash": metrics(1), "m-pro": metrics(2)}),
        "B": key("B", {"m-flash": metrics(3), "m-pro": metrics(4)}),
        "C": key("C", {"m-flash": metrics(5), "m-pro": metrics(6), "m-lite": metrics(7)}),
    },
    "single key, multi-model": {
        "A": key("A", {"m-flash": metrics(1), "m-pro": metrics(2)}),
    },
    "single key, single model": {
        "A": key("A", {"m-flash": metrics(1)}),
    },
    "3 models on every key": {
        "A": key("A", {"a": metrics(1), "b": metrics(2), "c": metrics(3)}),
        "B": key("B", {"a": metrics(4), "b": metrics(5), "c": metrics(6)}),
    },
}


def render_compare(day: dict, table_id: str) -> str:
    """Render buildCompareHtml from the real page script inside a Node DOM shim."""
    js = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync('index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const cells = {};
function makeEl(id) {
  return { id, _html: "", value: "",
    set innerHTML(v) { this._html = String(v); cells[id] = this._html; },
    get innerHTML() { return this._html; },
    set textContent(v) { cells[id] = String(v); }, get textContent() { return cells[id] || ""; },
    appendChild() {}, addEventListener() {},
    classList: { add() {}, remove() {}, contains: () => false }, options: [] };
}
const els = {};
let EXPORTS = null;
const sandbox = {
  document: { getElementById: (id) => (els[id] = els[id] || makeEl(id)),
              createElement: () => makeEl(null), querySelectorAll: () => [] },
  console: { log() {}, error() {}, warn() {} },
  __SITE_TEST__: (api) => { EXPORTS = api; },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
if (!EXPORTS) { console.error('no test exports'); process.exit(2); }
const day = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({ html: EXPORTS.buildCompareHtml(day) }));
"""
    tmp = ROOT / "_cmp_shim.js"
    tmp.write_text(js, encoding="utf-8")
    try:
        r = subprocess.run(["node", str(tmp), json.dumps(day)],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:400])
        return json.loads(r.stdout)["html"]
    finally:
        tmp.unlink(missing_ok=True)


def rows_detail(html: str) -> list[dict]:
    """Per row: how many <td> it has, and its first used visual column."""
    rows = [m[1] for m in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", html)]
    covered: list[dict] = [{} for _ in rows]
    for ri, row in enumerate(rows):
        c = 0
        for cell in re.finditer(r"<t[dh]([^>]*)>", row):
            while covered[ri].get(c):
                c += 1
            span = int((re.search(r'rowspan="(\d+)"', cell[1]) or [None, 1])[1])
            for k in range(1, span):
                if ri + k < len(rows):
                    covered[ri + k][c] = True
            c += 1
    out = []
    for ri, row in enumerate(rows):
        cells = len(re.findall(r"<t[dh][^>]*>", row))
        c = 0
        for _ in range(cells):
            while covered[ri].get(c):
                c += 1
            c += 1
        out.append({"cells": cells, "span": c, "first_col": next(
            (i for i in range(c) if not covered[ri].get(i)), 0)})
    return out


def main() -> int:
    chrome = sys.argv[1] if len(sys.argv) > 1 else None
    print("=== structure: every shape renders uniform column counts ===")
    for label, day in SHAPES.items():
        html = render_compare(day, "compareTable")
        keys = list(day)
        multi = sum(1 for k in keys if len(day[k]["models"]) > 1)
        total_models = sum(len(day[k]["models"]) for k in keys)
        subtotals = len(re.findall(r">小计</td>", html))
        data_rows = total_models
        # A rowspan continuation row exists for every model beyond the first of its key.
        carry_rows = sum(len(day[k]["models"]) - 1 for k in keys)
        # header + one row per model + one 小计 row per multi-model key + grand total
        expected_rows = 1 + data_rows + subtotals + 1

        widths = [d["span"] for d in rows_detail(html)]
        detail_rows = rows_detail(html)
        check(subtotals == multi,
              f"[{label}] 小计 rows == multi-model keys ({multi})", f"got {subtotals}")
        check(len(widths) == expected_rows,
              f"[{label}] row count = {expected_rows} (header + {data_rows} model + "
              f"{subtotals} 小计 + grand)", f"got {len(widths)}")
        check(all((w == 9 or w == 10) for w in widths),
              f"[{label}] every row spans 9 or 10 visual columns", f"widths={widths}")
        # A rowspan continuation row carries NO key cell, so it has exactly 9 <td>; every
        # other body row has 10. That is the shape that keeps columns aligned.
        body = detail_rows[1:]
        nines = [d for d in body if d["cells"] == 9]
        tens = [d for d in body if d["cells"] == 10]
        check(len(nines) == carry_rows,
              f"[{label}] exactly {carry_rows} continuation row(s) with 9 cells in the body",
              f"cell counts={[d['cells'] for d in body]}")
        check(len(tens) == len(body) - carry_rows,
              f"[{label}] all other body rows have 10 cells",
              f"cell counts={[d['cells'] for d in body]}")
        # Continuation rows must start at column 1 (column 0 carried by the key cell)
        check(all(d["first_col"] == 1 for d in nines),
              f"[{label}] continuation rows start at column 1", str([d['first_col'] for d in nines]))
        # every 小计 row must carry the label then an EMPTY model cell
        subs = re.findall(r'<tr class="total">([\s\S]*?)</tr>', html)
        subs = [s for s in subs if "小计" in s]
        ok_shape = all(re.match(r'\s*<td class="subtotal-label">小计</td><td class="grp model-cell"></td>', s)
                       for s in subs)
        check(ok_shape, f"[{label}] each 小计 row = label + empty model cell")

    print("\n=== numbers: each 小计 row equals the sum of its own models ===")
    day = SHAPES["EVERY key multi-model"]
    html = render_compare(day, "compareTable")
    subs = [s for s in re.findall(r'<tr class="total">([\s\S]*?)</tr>', html) if "小计" in s]
    grand = [s for s in re.findall(r'<tr class="total grand">([\s\S]*?)</tr>', html)]
    check(len(subs) == 3, "three 小计 rows", str(len(subs)))
    # sorted by cost desc -> all equal cost so order is by key; each key: 1+2, 3+4, 5+6+7
    expected_reqs = {"3", "7", "18"}
    got = []
    for s in subs:
        tds = [re.sub(r"<[^>]*>", "", t).strip() for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", s)]
        got.append(tds[2])
    check(set(got) == expected_reqs, "小计 调用次数 = sum of that key's models", f"{got} vs {expected_reqs}")
    check(len(grand) == 1, "one grand total row")
    gtds = [re.sub(r"<[^>]*>", "", t).strip() for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", grand[0])]
    check(gtds[0] == "全部合计" and gtds[2] == "28", "grand total sums all models", str(gtds[:3]))

    if not chrome:
        print("\n(no chrome path given; skipping rendered-geometry check)")
    else:
        print("\n=== rendered geometry in a real browser (every key multi-model) ===")
        # write a standalone page containing just this table + the real CSS
        real = (ROOT / "index.html").read_text(encoding="utf-8")
        css = re.search(r"<style>([\s\S]*?)</style>", real).group(1)
        page = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{css}</style></head>"
                f"<body><div class='card'><table id='compareTable'>{html}</table></div></body></html>")
        tmp_page = ROOT / "_shape_probe.html"
        tmp_page.write_text(page, encoding="utf-8")
        try:
            probe = ROOT / "_shape_probe.js"
            probe.write_text(PROBE_JS, encoding="utf-8")
            r = subprocess.run(["node", str(probe), chrome, str(tmp_page)],
                               capture_output=True, text=True, cwd=ROOT,
                               env={**__import__("os").environ,
                                    "NODE_PATH": r"C:\Users\lenovo\AppData\Local\npm-cache\_npx\1e7f6d9597241db0\node_modules"})
            print(r.stdout.rstrip())
            if r.returncode != 0:
                print(r.stderr.rstrip()[:500])
            check(r.returncode == 0, "rendered columns are geometrically consistent")
        finally:
            tmp_page.unlink(missing_ok=True)
            (ROOT / "_shape_probe.js").unlink(missing_ok=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


PROBE_JS = r"""
const http = require('http'), { spawn } = require('child_process');
const CHROME = process.argv[2], PAGE = 'file:///' + process.argv[3].replace(/\\/g, '/');
const PORT = 9351;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const getJSON = p => new Promise((res, rej) => {
  http.get({host:'127.0.0.1', port:PORT, path:p}, r => { let d=''; r.on('data',c=>d+=c); r.on('end',()=>res(JSON.parse(d))); }).on('error', rej);
});
(async () => {
  const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run',
    '--remote-debugging-port='+PORT, '--user-data-dir='+process.env.TEMP+'\\cdp-shape-'+Date.now(),
    '--window-size=1400,2000','about:blank'], {stdio:'ignore'});
  let t=null;
  for (let i=0;i<60&&!t;i++){ await sleep(500); try{ t=(await getJSON('/json/list')).find(x=>x.type==='page'); }catch{} }
  const WebSocket = require('ws');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.on('open', r));
  let id=0; const pend=new Map();
  ws.on('message', m => { const j=JSON.parse(m); if(j.id&&pend.has(j.id)){pend.get(j.id)(j);pend.delete(j.id);} });
  const send=(method,params)=>new Promise(res=>{const i=++id;pend.set(i,res);ws.send(JSON.stringify({id:i,method,params}));});
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate',{url:PAGE});
  for (let i=0;i<40;i++){ const r=await send('Runtime.evaluate',{expression:"!!document.querySelector('#compareTable tbody tr')",returnByValue:true}); if(r.result.result.value) break; await sleep(300); }
  const r = await send('Runtime.evaluate', { expression: `(() => {
    const table = document.getElementById('compareTable');
    const headers = [...table.querySelectorAll('thead th')];
    const rows = [...table.querySelectorAll('tbody tr')];
    const covered = rows.map(() => ({}));
    rows.forEach((tr, ri) => { let c = 0;
      for (const td of tr.children) {
        while (covered[ri][c]) c++;
        const rs = Number(td.getAttribute('rowspan') || 1);
        for (let k = 1; k < rs && ri + k < rows.length; k++) covered[ri + k][c] = true;
        c++;
      }
    });
    const per = headers.map(() => []);
    rows.forEach((tr, ri) => { let c = 0;
      for (const td of tr.children) {
        while (covered[ri][c]) c++;
        if (c >= headers.length) break;
        const b = td.getBoundingClientRect();
        per[c].push({ right: Math.round(b.right * 100) / 100, align: getComputedStyle(td).textAlign,
                      label: td.textContent.trim().slice(0, 10) || '(empty)', row: ri });
        c++;
      }
    });
    return headers.map((h, i) => ({ header: h.textContent.trim(), right: [...new Set(per[i].map(x => x.right))],
                                    align: [...new Set(per[i].map(x => x.align))], cells: per[i] }));
  })()`, returnByValue: true });
  const cols = r.result.result.value;
  let bad = 0;
  for (const c of cols) {
    const ok = c.right.length <= 1 && c.align.length <= 1;
    if (!ok) bad++;
    console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${c.header.padEnd(14)} rightEdges=${JSON.stringify(c.right)} align=${JSON.stringify(c.align)}`);
    if (!ok) c.cells.forEach(x => console.log(`        row${x.row} "${x.label}" align=${x.align} right=${x.right}`));
  }
  console.log(bad ? `  ${bad} inconsistent column(s)` : '  all 10 columns aligned across every row (incl. every 小计 row)');
  ws.close(); chrome.kill(); process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(3); });
"""


if __name__ == "__main__":
    raise SystemExit(main())

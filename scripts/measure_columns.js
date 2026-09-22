/*
 * Layout guard: verify that every column in the rendered tables has consistent edges.
 *
 * This exists because two alignment bugs slipped through unit-level checks:
 *   - model names were left/right aligned inconsistently with their header
 *   - the subtotal row was missing a cell, shifting every value one column right
 * Cell-count checks catch the second but not the first; this measures the actual
 * rendered geometry in a browser, which is the only thing that catches both.
 *
 * Requires a Chrome/Chromium binary and the `ws` package. Local-only tool.
 * Run: node scripts/measure_columns.js "<chrome.exe>" "$PWD/index.html"
 */
const http = require("http");
const { spawn } = require("child_process");

const CHROME = process.argv[2];
const PAGE = "file:///" + process.argv[3].replace(/\\/g, "/");
const PORT = 9342;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}\\cdp-mc-${Date.now()}`,
    "--window-size=1400,2400", "about:blank"], { stdio: "ignore" });
  let t = null;
  for (let i = 0; i < 60 && !t; i++) { await sleep(500); try { t = (await getJSON("/json/list")).find((x) => x.type === "page"); } catch {} }
  const WebSocket = require("ws");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.result && r.result.exceptionDetails) {
      throw new Error("page eval failed: " + (r.result.exceptionDetails.exception?.description || ""));
    }
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: PAGE });
  await sleep(2500);

  const measure = (tableId) => `(() => {
    const table = document.getElementById('${tableId}');
    if (!table) return null;
    const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
    const rows = [...table.querySelectorAll('tbody tr')];
    // A column covered by a rowspan from an earlier row simply has no cell in this row,
    // so pre-compute which (row, column) slots are covered before walking the cells.
    const covered = rows.map(() => ({}));
    rows.forEach((tr, ri) => {
      let c = 0;
      for (const td of tr.children) {
        while (covered[ri][c]) c++;
        const rs = Number(td.getAttribute('rowspan') || 1);
        for (let k = 1; k < rs && ri + k < rows.length; k++) covered[ri + k][c] = true;
        c += 1;
      }
    });
    const perColumn = headers.map(() => []);
    rows.forEach((tr, ri) => {
      let c = 0;
      for (const td of tr.children) {
        while (covered[ri][c]) c++;
        if (c >= headers.length) break;
        const r = td.getBoundingClientRect();
        perColumn[c].push({
          row: ri,
          left: Math.round(r.left * 100) / 100,
          right: Math.round(r.right * 100) / 100,
          align: getComputedStyle(td).textAlign,
          label: (td.textContent.trim().slice(0, 12) || '(empty)'),
        });
        c += 1;
      }
    });
    return headers.map((h, i) => ({
      header: h, i,
      rightEdges: [...new Set(perColumn[i].map(x => x.right))],
      aligns: [...new Set(perColumn[i].map(x => x.align))],
      cells: perColumn[i],
    }));
  })()`;

  let bad = 0;
  for (const tableId of ["detailTable", "compareTable"]) {
    const cols = await ev(measure(tableId));
    console.log(`\n=== ${tableId} ===`);
    if (!cols) { console.log("  MISSING TABLE"); bad++; continue; }
    cols.forEach((c) => {
      const edgesOk = c.rightEdges.length <= 1;
      const alignOk = c.aligns.length <= 1;
      const ok = edgesOk && alignOk;
      if (!ok) bad++;
      console.log(`  [${String(c.i).padStart(2)}] ${c.header.padEnd(14)} ` +
                  `rightEdges=${JSON.stringify(c.rightEdges)} align=${JSON.stringify(c.aligns)} ${ok ? "OK" : "<-- INCONSISTENT"}`);
      if (!ok) c.cells.forEach((x) => console.log(`        row${x.row} "${x.label}" align=${x.align} left=${x.left} right=${x.right}`));
    });
  }
  console.log(bad ? `\nRESULT: ${bad} inconsistent column(s)` : "\nRESULT: every column is geometrically consistent");
  ws.close(); chrome.kill();
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(3); });

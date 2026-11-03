/*
 * Local-only: do the two tables fit inside their card, or is the last column cut off?
 *
 * Adding the 工具调用次数 / 工具调用费用 columns took the compare table to 12 columns, which
 * silently pushed 费用合计 outside the card — the screenshot showed the most important column
 * half-cut. Nothing caught it: the column-alignment tool only checks shared right edges, and
 * the unit tests only count cells. This checks the actual scrollWidth vs clientWidth.
 *
 * Run: node scripts/check_layout_fit.js "<chrome.exe>"
 */
const http = require("http");
const path = require("path");
const { spawn } = require("child_process");

const CHROME = process.argv[2];
const TARGET = process.argv[3] || "file:///" + path.resolve("index.html").replace(/\\/g, "/");
const PORT = 9451;
// wide enough for a desktop browser: both tables must fit without horizontal scrolling
const WIDE = [1500, 1366];
// narrow: scrolling inside .tablewrap is the designed fallback, so only assert it is scrollable
const NARROW = [1024];

let fail = 0, n = 0;
const ok = (c, label, extra = "") => {
  n++;
  console.log(`  ${c ? "PASS" : "FAIL"} :: ${label}${!c && extra ? "  -> " + extra : ""}`);
  if (!c) fail++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJSON(port, p) {
  return new Promise((res, rej) => {
    http.get({ host: "127.0.0.1", port, path: p }, (r) => {
      let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
    }).on("error", rej);
  });
}

async function measure(chrome, width) {
  let tab = null;
  for (let i = 0; i < 60 && !tab; i++) {
    await sleep(400);
    try { tab = (await getJSON(PORT, "/json/list")).find((x) => x.type === "page"); } catch {}
  }
  if (!tab) throw new Error("could not attach to Chrome");
  const WebSocket = require("ws");
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (e) => {
    const r = await send("Runtime.evaluate", { expression: e, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval");
    return r.result.result.value;
  };

  await send("Emulation.setDeviceMetricsOverride", {
    width, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: TARGET });
  for (let i = 0; i < 40; i++) {
    if (await ev("!!document.querySelector('#compareTable tbody tr')")) break;
    await sleep(300);
  }
  const res = await ev(`(() => {
    const out = {};
    for (const id of ['compareTable', 'detailTable']) {
      const box = document.getElementById(id).parentElement;
      out[id] = { sw: Math.round(box.scrollWidth), cw: Math.round(box.clientWidth),
                  cols: document.getElementById(id).querySelectorAll('thead th').length,
                  overflowX: getComputedStyle(box).overflowX };
    }
    return out;
  })()`);
  ws.close();
  return res;
}

(async () => {
  console.log("target:", TARGET);
  let chrome = null;
  async function launch() {
    chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
      "--remote-debugging-port=" + PORT,
      "--user-data-dir=" + process.env.TEMP + "\\cdp-fit-" + Date.now(), "about:blank"], { stdio: "ignore" });
    for (let i = 0; i < 60; i++) {
      await sleep(400);
      try { if ((await getJSON(PORT, "/json/version"))) return; } catch {}
    }
    throw new Error("chrome did not start");
  }

  await launch();
  for (const w of WIDE) {
    const m = await measure(chrome, w);
    for (const id of ["compareTable", "detailTable"]) {
      const r = m[id];
      ok(r.sw <= r.cw, `${w}px: ${id} fits its card (${r.cols} columns)`,
         `needs ${r.sw}px, has ${r.cw}px (overflow ${r.sw - r.cw}px)`);
    }
  }
  chrome.kill();
  await sleep(500);

  await launch();
  for (const w of NARROW) {
    const m = await measure(chrome, w);
    for (const id of ["compareTable", "detailTable"]) {
      const r = m[id];
      ok(r.overflowX === "auto" || r.overflowX === "scroll",
         `${w}px: ${id} scrolls horizontally instead of clipping`, r.overflowX);
    }
  }
  chrome.kill();

  console.log(`\n  RESULT: ${n - fail}/${n} checks passed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("  " + e.message); process.exit(3); });

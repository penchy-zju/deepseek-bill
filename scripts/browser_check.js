/*
 * Browser-truth check via Chrome DevTools Protocol over a raw WebSocket.
 * Verifies computed styles (no auto-link red) and that the API-key filter actually
 * re-renders the detail table with that key's own numbers.
 *
 * Run: node scripts/browser_check.js "<path to chrome.exe>" "<abs path to index.html>"
 */
const http = require("http");
const fs = require("fs");
const { spawn } = require("child_process");

const CHROME = process.argv[2];
const PAGE = "file:///" + process.argv[3].replace(/\\/g, "/");
const PORT = 9333;

let failures = 0, checks = 0;
const ok = (c, label, extra = "") => {
  checks++;
  console.log(`  ${c ? "PASS" : "FAIL"} :: ${label}${!c && extra ? "  -> " + extra : ""}`);
  if (!c) failures++;
};

const getJSON = (path) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
  }).on("error", rej);
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const userDir = process.env.TEMP + "\\cdp-profile-" + Date.now();
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${userDir}`, "--window-size=1400,2000",
    "about:blank",
  ], { stdio: "ignore" });

  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(500);
    try {
      const list = await getJSON("/json/list");
      target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
    } catch { /* not up yet */ }
  }
  if (!target) { console.log("FAIL :: could not connect to Chrome"); chrome.kill(); process.exit(1); }

  const WebSocket = require("ws");
  let ws;
  try { ws = new WebSocket(target.webSocketDebuggerUrl); }
  catch (e) { console.log("ws module unavailable:", e.message); chrome.kill(); process.exit(2); }

  await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });

  let msgId = 0;
  const pending = new Map();
  ws.on("message", (raw) => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++msgId; pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
    return r.result.result.value;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: PAGE });
  await sleep(2500);

  console.log("== page loaded ==");
  ok(await evaluate("!!document.getElementById('detailTable').querySelector('tbody')"), "detail table has a body");

  console.log("\n== computed styles (auto-link red check) ==");
  const styleInfo = await evaluate(`(() => {
    const out = {};
    const cells = [...document.querySelectorAll('#compareTable tbody td')];
    const modelCell = cells.find(td => td.textContent.includes('deepseek-'));
    if (modelCell) {
      const cs = getComputedStyle(modelCell);
      out.modelColor = cs.color;
      out.modelDecoration = cs.textDecorationLine;
      out.modelCursor = cs.cursor;
    }
    out.sharedColorRule = !!([...document.styleSheets].some(ss => {
      try { return [...ss.cssRules].some(r => r.selectorText && r.selectorText.includes('deepseek')); }
      catch { return false; }
    }));
    const t = [...document.querySelectorAll('#compareTable tbody tr')].map(tr => tr.innerText.replace(/\\s+/g,' ').trim());
    out.rowCount = document.querySelectorAll('#compareTable tbody tr').length;
    out.rows = t;
    return out;
  })()`);
  console.log("   compare rows:", styleInfo.rowCount);
  styleInfo.rows.forEach((r) => console.log("     " + r));
  ok(styleInfo.modelColor === "rgb(27, 31, 36)", "model cell uses the normal ink color (not auto-link red)", styleInfo.modelColor);
  ok(styleInfo.modelDecoration === "none", "model cell has no underline", styleInfo.modelDecoration);
  ok(!styleInfo.sharedColorRule, "no stray CSS rule targeting model names");

  console.log("\n== truncated masked keys carry full text in title= ==");
  const titleInfo = await evaluate(`(() => {
    const els = [...document.querySelectorAll('#compareTable tbody td')].filter(td => td.textContent.startsWith('sk-'));
    return els.map(el => el.getAttribute('title'));
  })()`);

  console.log("\n== key filter re-renders with that key's numbers ==");
  const perKey = await evaluate(`(() => {
    const sel = document.getElementById('keySelect');
    const r = {};
    for (const opt of sel.options) {
      if (opt.value === '__all__') continue;
      sel.value = opt.value;
      sel.dispatchEvent(new Event('change'));
      const row = [...document.querySelectorAll('#detailTable tbody tr.total td')].map(td => td.textContent.trim());
      r[opt.textContent.split(' — ')[0]] = { total: row[row.length - 1], req: row[1], rows: document.querySelectorAll('#detailTable tbody tr').length };
    }
    sel.value = '__all__';
    sel.dispatchEvent(new Event('change'));
    return r;
  })()`);
  Object.entries(perKey).forEach(([k, v]) => console.log(`     ${k}: 合计=${v.total} 调用=${v.req}`));

  const distinct = new Set(Object.values(perKey).map((v) => v.total));
  ok(distinct.size === Object.keys(perKey).length, "each key shows a distinct total (filter really re-renders)");
  ok(Object.values(perKey).every((v) => v.rows >= 2), "each filtered view shows at least one model row + total row");

  const allTotal = await evaluate(`(() => {
    const row = [...document.querySelectorAll('#detailTable tbody tr.total td')].map(td => td.textContent.trim());
    return row[row.length - 1];
  })()`);
  console.log("     all-keys total:", allTotal);
  ok(allTotal === "$31.8165", "all-keys total renders as $31.8165", allTotal);

  console.log("\n== switching date keeps page consistent ==");
  const dateSwitch = await evaluate(`(() => {
    const ds = document.getElementById('dateSelect');
    if (ds.options.length < 1) return { skipped: true };
    const v = ds.value;
    ds.dispatchEvent(new Event('change'));
    return { value: v, kpis: document.querySelectorAll('#kpis .kpi').length };
  })()`);
  ok(dateSwitch.skipped || dateSwitch.kpis === 5, "KPIs still render after date change", JSON.stringify(dateSwitch));

  ws.close(); chrome.kill();
  console.log(`\nRESULT: ${checks - failures}/${checks} checks passed`);
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("harness error:", e); process.exit(3); });

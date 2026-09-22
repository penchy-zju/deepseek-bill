/*
 * Local-only: confirm the currency symbol renders correctly in a real browser and that
 * no dollar sign appears anywhere in the visible page text.
 * Run: node scripts/check_currency.js "<chrome.exe>" "<abs index.html>"
 */
const http = require("http");
const { spawn } = require("child_process");
const CHROME = process.argv[2];
const PAGE = "file:///" + process.argv[3].replace(/\\/g, "/");
const PORT = 9336;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}\\cdp3-${Date.now()}`,
    "--window-size=1400,2200", "about:blank"], { stdio: "ignore" });
  let t = null;
  for (let i = 0; i < 60 && !t; i++) { await sleep(500); try { t = (await getJSON("/json/list")).find((x) => x.type === "page"); } catch {} }
  const WebSocket = require("ws");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (e) => (await send("Runtime.evaluate", { expression: e, returnByValue: true })).result.result.value;

  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: PAGE });
  await sleep(2500);

  const info = await ev(`(() => {
    const text = document.body.innerText;
    const yen = (text.match(/\\u00A5/g) || []).length;
    const dollar = (text.match(/\\$/g) || []).length;
    // money-looking strings actually shown to the user
    const moneyCells = [...document.querySelectorAll('#detailTable td, #compareTable td, #kpis .value')]
      .map(td => td.textContent.trim())
      .filter(s => /[\\u00A5$]/.test(s));
    return {
      yen, dollar,
      moneySample: moneyCells.slice(0, 10),
      anyDollarMoney: moneyCells.filter(s => s.includes('$')).slice(0, 5),
      yenMoney: moneyCells.filter(s => s.includes('\\u00A5')).length,
    };
  })()`);

  console.log("  visible ¥ count      :", info.yen);
  console.log("  visible $ count      :", info.dollar);
  console.log("  money values with ¥  :", info.yenMoney);
  console.log("  sample money values  :", info.moneySample.join("  |  "));
  if (info.anyDollarMoney.length) console.log("  DOLLAR VALUES FOUND  :", info.anyDollarMoney);

  const pass = info.dollar === 0 && info.yen > 0 && info.anyDollarMoney.length === 0;
  console.log(pass ? "\n  PASS :: page shows ¥ only, no $ anywhere" : "\n  FAIL :: currency symbol problem");
  ws.close(); chrome.kill(); process.exit(pass ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(3); });

/*
 * Focused check: phone-number auto-detection rendering and key tooltips.
 * Run: node scripts/check_phone.js "<chrome.exe>" "<abs index.html>"
 */
const http = require("http");
const { spawn } = require("child_process");
const CHROME = process.argv[2];
const PAGE = "file:///" + process.argv[3].replace(/\\/g, "/");
const PORT = 9334;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}\\cdp2-${Date.now()}`,
    "--window-size=1400,2000", "about:blank"], { stdio: "ignore" });
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
    const keyEl = document.querySelector('#compareTable .key');
    return {
      isPhoneNumber: typeof window.isPhoneNumber === 'function' ? window.isPhoneNumber(keyEl.textContent) : 'no-api',
      keyText: keyEl.textContent,
      title: keyEl.getAttribute('title'),
      color: getComputedStyle(keyEl).color,
      meta: !!document.querySelector('meta[name="format-detection"]'),
      allKeysHaveTitle: [...document.querySelectorAll('.key')].every(e => e.getAttribute('title') === e.textContent),
      keyCount: document.querySelectorAll('.key').length,
    };
  })()`);
  console.log("  phone-detection API says text is a phone number:", info.isPhoneNumber);
  console.log("  key text :", info.keyText);
  console.log("  title    :", info.title);
  console.log("  color    :", info.color);
  console.log("  meta present:", info.meta, "| all .key have title=text:", info.allKeysHaveTitle, "| count:", info.keyCount);

  const pass = info.isPhoneNumber !== true && info.allKeysHaveTitle && info.meta;
  console.log(pass ? "\n  PASS :: keys are not phone-detected and carry tooltips" : "\n  FAIL :: phone detection or tooltip issue");
  ws.close(); chrome.kill(); process.exit(pass ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(3); });

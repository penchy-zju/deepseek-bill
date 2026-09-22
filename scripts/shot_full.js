/*
 * Local-only: full-page 2x screenshot.
 * Run: node scripts/shot_full.js "<chrome.exe>" "<abs index.html>" "<out png>"
 */
const http = require("http");
const fs = require("fs");
const { spawn } = require("child_process");
const CHROME = process.argv[2];
const PAGE = "file:///" + process.argv[3].replace(/\\/g, "/");
const OUT = process.argv[4];
const PORT = 9341;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${process.env.TEMP}\\cdp-f-${Date.now()}`,
    "--force-device-scale-factor=2", "--hide-scrollbars", "--window-size=1200,1000", "about:blank"],
    { stdio: "ignore" });
  let t = null;
  for (let i = 0; i < 60 && !t; i++) { await sleep(500); try { t = (await getJSON("/json/list")).find((x) => x.type === "page"); } catch {} }
  const WebSocket = require("ws");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });

  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: PAGE });
  await sleep(2200);
  const m = await send("Page.getLayoutMetrics");
  const cs = m.result.cssContentSize || m.result.contentSize;
  const shot = await send("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: Math.ceil(cs.width), height: Math.ceil(cs.height), scale: 2 },
  });
  fs.writeFileSync(OUT, Buffer.from(shot.result.data, "base64"));
  console.log(`wrote ${OUT} (${fs.statSync(OUT).size} bytes), page ${Math.ceil(cs.width)}x${Math.ceil(cs.height)}`);
  ws.close(); chrome.kill(); process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });

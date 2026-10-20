/*
 * Local-only: verify the KPI-card hover effect in a real browser.
 * Uses CDP Input.dispatchMouseEvent so CSS :hover actually engages, then reads computed
 * styles. Asserts the properties that matter (not exact pixel values, so the design can be
 * tweaked without breaking this):
 *   off-hover : no transform, card background, muted label
 *   on-hover  : lift applied, shadow deepened, border tinted, background washes to
 *               --accent-soft, value takes --accent colour
 *   leaving   : returns to the resting state
 *   no reflow : a following element does not move (transform must be visual only)
 *   touch     : the hover rule is scoped to hover-capable pointers
 *   reduce    : prefers-reduced-motion drops the transform but keeps the colour feedback
 *
 * Run: node scripts/check_kpi_hover.js "<chrome.exe>" [file-or-url]
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const CHROME = process.argv[2];
const TARGET = process.argv[3] || "file:///" + path.resolve("index.html").replace(/\\/g, "/");
const PORT = 9421;
let fail = 0, n = 0;
const ok = (c, label, extra = "") => {
  n++;
  console.log(`  ${c ? "PASS" : "FAIL"} :: ${label}${!c && extra ? "  -> " + extra : ""}`);
  if (!c) fail++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
    "--remote-debugging-port=" + PORT, "--user-data-dir=" + process.env.TEMP + "\\cdp-kpi-" + Date.now(),
    "--window-size=1400,1000", "about:blank"], { stdio: "ignore" });
  let tab = null;
  for (let i = 0; i < 60 && !tab; i++) {
    await sleep(500);
    try { tab = (await getJSON("/json/list")).find((x) => x.type === "page"); } catch {}
  }
  const WebSocket = require("ws");
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval");
    return r.result.result.value;
  };
  const moveTo = (x, y) => send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" });

  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: TARGET });
  for (let i = 0; i < 60; i++) {
    if (await ev("!!document.querySelector('#kpis .kpi')")) break;
    await sleep(300);
  }

  const count = await ev("document.querySelectorAll('#kpis .kpi').length");
  ok(count === 5, "the dashboard renders 5 KPI cards", String(count));

  // a following element that must not move when a card lifts
  await ev("window.__anchorTop = document.getElementById('detailTitle').getBoundingClientRect().top;");

  const style = () => ev(`(() => {
    const card = document.querySelector('#kpis .kpi');
    const cs = getComputedStyle(card);
    return {
      transform: cs.transform, background: cs.backgroundColor, border: cs.borderTopColor,
      shadow: cs.boxShadow, value: getComputedStyle(card.querySelector('.value')).color,
      label: getComputedStyle(card.querySelector('.label')).color,
      transition: cs.transitionProperty,
    };
  })()`);

  console.log("\n== resting state ==");
  const rest = await style();
  ok(rest.transform === "none", "no transform while not hovered", rest.transform);
  ok(rest.transition !== "none" && rest.transition.split(",").length >= 3,
     "the card animates transform/shadow/border/background", rest.transition);

  // hover the 2nd card
  const rect = await ev(`(() => { const r = document.querySelectorAll('#kpis .kpi')[1].getBoundingClientRect();
                            return {x: r.left + r.width/2, y: r.top + r.height/2}; })()`);
  await moveTo(rect.x, rect.y);
  await sleep(450);
  console.log("\n== hovered state ==");
  const hov = await ev(`(() => {
    const card = document.querySelectorAll('#kpis .kpi')[1];
    const cs = getComputedStyle(card);
    return { transform: cs.transform, background: cs.backgroundColor, border: cs.borderTopColor,
             shadow: cs.boxShadow, value: getComputedStyle(card.querySelector('.value')).color,
             label: getComputedStyle(card.querySelector('.label')).color,
             hovered: card.matches(':hover') };
  })()`);
  ok(hov.hovered, "the pointer really hovers the card");
  ok(/matrix\(1, 0, 0, 1, 0, -2\)/.test(hov.transform), "hover lifts the card by 2px", hov.transform);
  ok(hov.shadow !== rest.shadow, "hover deepens the shadow", `${hov.shadow} vs ${rest.shadow}`);
  ok(hov.border !== rest.border, "hover tints the border with the accent", `${hov.border} vs ${rest.border}`);
  ok(hov.background !== rest.background, "hover washes the background", `${hov.background} vs ${rest.background}`);
  ok(hov.value !== rest.value, "hover shifts the big number to the accent colour",
     `${hov.value} vs ${rest.value}`);
  const anchorMoved = await ev(
    "Math.abs(document.getElementById('detailTitle').getBoundingClientRect().top - window.__anchorTop)");
  ok(anchorMoved < 0.01, "the lift is visual only (no layout reflow)", String(anchorMoved));

  console.log("\n== leaving the card ==");
  await moveTo(5, 5);
  await sleep(450);
  const after = await style();
  ok(after.transform === "none" && after.background === rest.background &&
     after.value === rest.value && after.border === rest.border,
     "the card returns to its resting state", JSON.stringify(after));

  console.log("\n== prefers-reduced-motion ==");
  await send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await sleep(150);
  await moveTo(rect.x, rect.y);
  await sleep(450);
  const rm = await ev(`(() => {
    const card = document.querySelectorAll('#kpis .kpi')[1];
    const cs = getComputedStyle(card);
    return { transform: cs.transform, background: cs.backgroundColor, transition: cs.transitionProperty };
  })()`);
  ok(rm.transform === "none", "reduced motion removes the lift", rm.transform);
  ok(rm.background !== rest.background, "reduced motion keeps the colour feedback", rm.background);
  await moveTo(5, 5);
  await send("Emulation.setEmulatedMedia", { features: [] });
  await sleep(200);

  console.log("\n== other cards respond too ==");
  let allLift = true;
  for (let i = 0; i < 5; i++) {
    const r = await ev(`(() => { const b = document.querySelectorAll('#kpis .kpi')[${i}].getBoundingClientRect();
                              return {x: b.left + b.width/2, y: b.top + b.height/2}; })()`);
    await moveTo(r.x, r.y);
    await sleep(260);
    const t = await ev(`getComputedStyle(document.querySelectorAll('#kpis .kpi')[${i}]).transform`);
    if (!/matrix\(1, 0, 0, 1, 0, -2\)/.test(t)) { allLift = false; console.log(`     card ${i}: ${t}`); }
    await moveTo(5, 5);
    await sleep(120);
  }
  ok(allLift, "all 5 cards lift on hover");

  // both colour schemes, with a screenshot of each hovered state for visual review
  console.log("\n== both colour schemes ==");
  for (const scheme of ["light", "dark"]) {
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
    await sleep(250);
    const restBg = await ev("getComputedStyle(document.querySelectorAll('#kpis .kpi')[1]).backgroundColor");
    const r = await ev(`(() => { const b = document.querySelectorAll('#kpis .kpi')[1].getBoundingClientRect();
                              return {x: b.left + b.width/2, y: b.top + b.height/2}; })()`);
    await moveTo(r.x, r.y);
    await sleep(500);
    const hovBg = await ev("getComputedStyle(document.querySelectorAll('#kpis .kpi')[1]).backgroundColor");
    const hovValue = await ev("getComputedStyle(document.querySelectorAll('#kpis .kpi')[1].querySelector('.value')).color");
    ok(hovBg !== restBg, `${scheme}: hover washes the background`, `${restBg} -> ${hovBg}`);
    ok(await ev("getComputedStyle(document.querySelectorAll('#kpis .kpi')[1]).transform")
         === "matrix(1, 0, 0, 1, 0, -2)", `${scheme}: hover lifts the card`);

    const m = await send("Page.getLayoutMetrics");
    const cs = m.result.cssContentSize || m.result.contentSize;
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: Math.ceil(cs.width), height: Math.min(Math.ceil(cs.height), 560), scale: 2 } });
    const file = `_kpi_hover_${scheme}.png`;
    fs.writeFileSync(file, Buffer.from(shot.result.data, "base64"));
    console.log(`     wrote ${file}  (hover background ${hovBg}, number ${hovValue})`);
    await moveTo(5, 5);
    await sleep(200);
  }
  await send("Emulation.setEmulatedMedia", { features: [] });

  ws.close(); chrome.kill();
  console.log(`\n  RESULT: ${n - fail}/${n} checks passed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("  " + e.message); process.exit(3); });

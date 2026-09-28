/*
 * Browser UI check for the two viewing dimensions (按天 / 累计至今).
 * Started by scripts/test_dimensions.py; not intended to be run directly in CI (no Chrome).
 *
 * Run: node scripts/check_dimensions_ui.js "<chrome.exe>"
 */
const http = require("http");
const { spawn } = require("child_process");

const CHROME = process.argv[2];
const PORT = 9371;
let fail = 0, n = 0;
const ok = (c, label, extra = "") => {
  n++;
  if (c) console.log(`  PASS :: ${label}`);
  else { fail++; console.log(`  FAIL :: ${label}${extra ? "  -> " + extra : ""}`); }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (p) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: PORT, path: p }, (r) => {
    let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d)));
  }).on("error", rej);
});

(async () => {
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
    "--remote-debugging-port=" + PORT,
    "--user-data-dir=" + process.env.TEMP + "\\cdp-dimui-" + Date.now(),
    "--force-device-scale-factor=2", "--window-size=1400,1200", "about:blank"], { stdio: "ignore" });
  let tab = null;
  for (let i = 0; i < 60 && !tab; i++) {
    await sleep(500);
    try { tab = (await getJSON("/json/list")).find((x) => x.type === "page"); } catch {}
  }
  if (!tab) { console.log("  FAIL :: could not attach to Chrome"); process.exit(3); }
  const WebSocket = require("ws");
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0; const pend = new Map();
  ws.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend.has(j.id)) { pend.get(j.id)(j); pend.delete(j.id); } });
  const send = (method, params) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.result.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.exception && r.result.exceptionDetails.exception.description || "eval failed");
    }
    return r.result.result.value;
  };

  await send("Page.enable"); await send("Runtime.enable");
  const url = "file:///" + process.cwd().replace(/\\/g, "/") + "/index.html";
  await send("Page.navigate", { url });
  for (let i = 0; i < 40; i++) {
    if (await ev("!!document.querySelector('#compareTable tbody tr')")) break;
    await sleep(300);
  }

  const kpi = () => ev("document.querySelector('#kpis .kpi .value').textContent");
  const detailTotal = () => ev(
    "(() => { const r = [...document.querySelectorAll('#detailTable tbody tr.total td')]; return r[r.length-1].textContent; })()");
  const clickMode = (m) => ev(
    "[...document.querySelectorAll('#modeSeg button')].find(b => b.dataset.mode === '" + m + "').click()");

  console.log("== day view ==");
  const opts = await ev("[...document.getElementById('dateSelect').options].map(o => o.value)");
  const oldest = opts[opts.length - 1];   // day-view options are newest-first
  ok(opts.length <= 7, "date dropdown offers at most 7 days", String(opts.length));
  ok(opts[0] > opts[opts.length - 1], "options are newest-first", opts.join(","));
  const labels = await ev("[...document.getElementById('dateSelect').options].map(o => o.textContent)");
  ok(!labels.some((t) => t.includes("截至")), "day-view options are not labelled 截至", labels.join("|"));
  let k = await kpi(), d = await detailTotal();
  ok(k === d, "KPI total equals the detail-table total", k + " vs " + d);
  const dayKpi = k;

  console.log("== switch to 累计至今 ==");
  await clickMode("cumulative");
  await sleep(400);
  k = await kpi(); d = await detailTotal();
  const hint = await ev("document.querySelector('#kpis .kpi .hint').textContent");
  const note = await ev("document.getElementById('rangeNote').textContent");
  const label = await ev("document.getElementById('dateLabel').textContent");
  const cumLabels = await ev("[...document.getElementById('dateSelect').options].map(o => o.textContent)");
  const pressed = await ev(
    "[...document.querySelectorAll('#modeSeg button')].map(b => b.dataset.mode + ':' + b.getAttribute('aria-pressed')).join(',')");
  ok(k === d, "KPI total equals the detail-table total in cumulative view", k + " vs " + d);
  ok(k !== dayKpi, "cumulative total differs from the single-day total", k + " vs " + dayKpi);
  ok(pressed === "day:false,cumulative:true", "segmented control reflects the mode", pressed);
  ok(label === "截止日期", "date select is relabelled 截止日期", label);
  ok(hint.includes("天累计"), "hint states the accumulated day count", hint);
  ok(note.includes("累计至今"), "range note explains the cumulative range", note.slice(0, 60));
  ok(cumLabels.every((t) => t.includes("截至")), "cumulative options are labelled 截至", cumLabels.join("|"));
  // the cumulative note must name the first day the page knows about (its oldest date option)
  const firstDay = await ev("document.getElementById('rangeNote').textContent.match(/\\d{4}-\\d{2}-\\d{2}/)[0]");
  ok(firstDay === oldest, "cumulative range starts at the page's first day", `${firstDay} vs ${oldest}`);
  const cumStartVal = await ev("document.getElementById('dateSelect').value");
  ok(cumLabels.findIndex((t) => t.includes(cumStartVal)) >= 0, "the cumulative end date is one of the options");

  console.log("== cumulative honours a shorter end date ==");
  const allCum = k;
  await ev("(() => { const s = document.getElementById('dateSelect');"
    + " const i = 0; s.value = [...s.options].map(o=>o.value)[1];"
    + " s.dispatchEvent(new Event('change')); })()");
  await sleep(400);
  k = await kpi();
  ok(k !== allCum, "choosing an earlier end date changes the cumulative total", k + " vs " + allCum);

  console.log("== back to day view ==");
  await clickMode("day");
  await sleep(300);
  const back = await kpi();
  const dayOpts = await ev("[...document.getElementById('dateSelect').options].map(o => o.value)");
  const selVal = await ev("document.getElementById('dateSelect').value");
  ok(dayOpts.length <= 7, "day view still capped at 7 days", String(dayOpts.length));
  ok(back === await detailTotal(), "day view is consistent again");
  ok(dayOpts.includes(selVal), "the selected date is one of the offered options", selVal);

  ws.close(); chrome.kill();
  console.log(`\n  RESULT: ${n - fail}/${n} UI checks passed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("  " + e.message); process.exit(3); });

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

  // 已归档天数（页面副标题里就写着），用来验证「按天最多 7 天、累计可到全部」
  const archived = (await ev("document.getElementById('subtitle').textContent"
    + ".match(/共 (\\d+) 天/)[1]")) * 1;
  console.log(`  (archived days = ${archived})`);

  const kpi = () => ev("document.querySelector('#kpis .kpi .value').textContent");
  const detailTotal = () => ev(
    "(() => { const r = [...document.querySelectorAll('#detailTable tbody tr.total td')]; return r[r.length-1].textContent; })()");
  const clickMode = (m) => ev(
    "[...document.querySelectorAll('#modeSeg button')].find(b => b.dataset.mode === '" + m + "').click()");

  console.log("== day view ==");
  const opts = await ev("[...document.getElementById('dateSelect').options].map(o => o.value)");
  const oldest = opts[opts.length - 1];   // day-view options are newest-first
  ok(opts.length === Math.min(7, archived), "按天 offers min(7, archived) days",
     `${opts.length} vs ${Math.min(7, archived)}`);
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
  const pressed = await ev(
    "[...document.querySelectorAll('#modeSeg button')].map(b => b.dataset.mode + ':' + b.getAttribute('aria-pressed')).join(',')");
  const dateFieldHidden = await ev("document.getElementById('dateField').style.display === 'none'");
  ok(k === d, "KPI total equals the detail-table total in cumulative view", k + " vs " + d);
  ok(k !== dayKpi, "cumulative total differs from the single-day total", k + " vs " + dayKpi);
  ok(pressed === "day:false,cumulative:true", "segmented control reflects the mode", pressed);
  ok(dateFieldHidden, "the date picker is hidden in 累计至今 (no end date to choose)");
  ok(hint.includes("天累计"), "hint states the accumulated day count", hint);
  ok(note.includes("累计至今"), "range note explains the cumulative range", note.slice(0, 60));
  // the cumulative note must name both ends: the first day and the latest day
  const noteDates = await ev("document.getElementById('rangeNote').textContent.match(/\\d{4}-\\d{2}-\\d{2}/g)");
  const latest = opts[0];
  ok(noteDates[0] === oldest, "cumulative range starts at the page's first day", `${noteDates[0]} vs ${oldest}`);
  ok(noteDates.includes(latest), "cumulative range ends at the latest day", `${noteDates.join(",")} vs ${latest}`);

  console.log("== cumulative is unaffected by the (hidden) date picker ==");
  const allCum = k;
  const cumAgain = await ev("document.getElementById('rangeNote').textContent");
  await clickMode("day");
  await sleep(300);
  await clickMode("cumulative");
  await sleep(300);
  ok((await kpi()) === allCum, "re-entering 累计至今 gives the same total", `${await kpi()} vs ${allCum}`);
  ok((await ev("document.getElementById('rangeNote').textContent")) === cumAgain,
     "the cumulative range is stable across mode switches");
  ok(await ev("document.getElementById('dateField').style.display === 'none'"),
     "the date picker stays hidden in 累计至今");
  const detailTitle = await ev("document.getElementById('detailTitle').textContent");
  ok(detailTitle.includes(oldest) && detailTitle.includes(latest),
     "the detail heading names the full cumulative range", detailTitle);

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

  // ---------------------------------------------------------------- >7 days fixture
  // The live data may have exactly 7 days, in which case the cap is never exercised.
  // Re-render the same page with a synthetic 10-day history to prove it.
  console.log("\n== synthetic 10-day history (cap actually engaged) ==");
  const out = await runSynthetic(CHROME, 10);
  ok(out.dayCount === 7, "按天 capped at 7 of 10 days", String(out.dayCount));
  ok(out.cumFieldHidden, "累计至今 hides the date picker even with 10 archived days");
  ok(out.cumStart === "2030-01-01", "累计至今 starts at the first synthetic day", out.cumStart);
  ok(out.cumEnd === out.latest, "累计至今 ends at the latest synthetic day", `${out.cumEnd} vs ${out.latest}`);
  ok(out.cumSum > out.sevenDaySum, "cumulative total exceeds the 7-day window total",
     `${out.cumSum} vs ${out.sevenDaySum}`);
  ok(out.dayKpi === out.sevenDayOldest, "by-day shows the selected day's own total",
     `${out.dayKpi} vs ${out.sevenDayOldest}`);

  console.log(`\n  RESULT: ${n - fail}/${n} UI checks passed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error("  " + e.message); process.exit(3); });

// Render a page whose embedded SITE has `days` synthetic days, then inspect the controls.
async function runSynthetic(chromePath, days) {
  const fs = require("fs");
  const os = require("os");
  const path = require("path");
  const base = fs.readFileSync("index.html", "utf8");
  const dates = [];
  for (let i = 0; i < days; i++) {
    const d = new Date(Date.UTC(2030, 0, 1 + i)).toISOString().slice(0, 10);
    dates.push(d);
  }
  const data = {};
  dates.forEach((d, i) => {
    data[d] = {
      "sk-synth***01": {
        name: "S", masked: "sk-synth***01",
        models: { m1: { requests: i + 1, cache_hit: 10, cache_miss: 10, output: 10,
                        cost_total: (i + 1).toFixed(4),
                        cost: { cache_hit: "0.01", cache_miss: "0.02", output: "0.03" } } },
      },
    };
  });
  const fake = { dates, default_date: dates[dates.length - 1], data_version: "synthetic",
                 warnings: [], source_files: [], data };
  const injected = base.replace(/const SITE = \{[\s\S]*?\};/, "const SITE = " + JSON.stringify(fake) + ";");
  const tmp = path.join(os.tmpdir(), `dimsynth-${Date.now()}.html`);
  fs.writeFileSync(tmp, injected, "utf8");

  const port = PORT + 1;
  const proc = spawn(chromePath, ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
    "--remote-debugging-port=" + port, "--user-data-dir=" + os.tmpdir() + "\\cdp-syn-" + Date.now(),
    "--window-size=1400,1200", "about:blank"], { stdio: "ignore" });
  let tab = null;
  for (let i = 0; i < 60 && !tab; i++) {
    await sleep(500);
    try {
      tab = await new Promise((res, rej) => {
        http.get({ host: "127.0.0.1", port, path: "/json/list" }, (r) => {
          let d = ""; r.on("data", (c) => (d += c)); r.on("end", () => res(JSON.parse(d).find((x) => x.type === "page")));
        }).on("error", rej);
      });
    } catch {}
  }
  const WebSocket = require("ws");
  const ws2 = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((r) => ws2.on("open", r));
  let id2 = 0; const pend2 = new Map();
  ws2.on("message", (m) => { const j = JSON.parse(m); if (j.id && pend2.has(j.id)) { pend2.get(j.id)(j); pend2.delete(j.id); } });
  const s2 = (method, params) => new Promise((res) => { const i = ++id2; pend2.set(i, res); ws2.send(JSON.stringify({ id: i, method, params })); });
  const e2 = async (expr) => {
    const r = await s2("Runtime.evaluate", { expression: expr, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval");
    return r.result.result.value;
  };
  await s2("Page.enable"); await s2("Runtime.enable");
  await s2("Page.navigate", { url: "file:///" + tmp.replace(/\\/g, "/") });
  for (let i = 0; i < 40; i++) {
    if (await e2("!!document.querySelector('#compareTable tbody tr')")) break;
    await sleep(300);
  }
  const kpi = () => e2("document.querySelector('#kpis .kpi .value').textContent");
  const optCount = () => e2("document.getElementById('dateSelect').options.length");

  const dayCount = await optCount();
  // the oldest day the by-day view can show (options are newest-first)
  await e2("(() => { const s = document.getElementById('dateSelect');"
    + " s.value = [...s.options].map(o => o.value).pop(); s.dispatchEvent(new Event('change')); })()");
  await sleep(300);
  const sevenDayOldest = await kpi();
  await e2("(() => { const s = document.getElementById('dateSelect');"
    + " s.value = [...s.options].map(o => o.value)[0]; s.dispatchEvent(new Event('change')); })()");
  await sleep(300);
  const sevenDaySum = await kpi();

  await e2("[...document.querySelectorAll('#modeSeg button')].find(b => b.dataset.mode === 'cumulative').click()");
  await sleep(400);
  const cumFieldHidden = await e2("document.getElementById('dateField').style.display === 'none'");
  const noteDates = await e2("document.getElementById('rangeNote').textContent.match(/\\d{4}-\\d{2}-\\d{2}/g)");
  const cumStart = noteDates[0];
  const cumEnd = noteDates[noteDates.length - 1];
  const cumSum = await kpi();
  const latest = dates[dates.length - 1];   // the newest synthetic day
  const dayKpi = sevenDayOldest;

  ws2.close(); proc.kill();
  try { fs.unlinkSync(tmp); } catch {}
  return { dayCount, cumFieldHidden, cumStart, cumEnd, cumSum, sevenDaySum, dayKpi, sevenDayOldest, latest };
}

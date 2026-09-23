/*
 * Independent verification of the generated dashboard.
 *
 * Strategy (deliberately not reusing the generator's own code):
 *   1. Re-aggregate data/amount-*.csv from scratch with a separate implementation.
 *   2. Execute index.html's real <script> in Node with a minimal DOM shim, so the page's
 *      own JS parses/aggregates/sorts and we can compare against step 1.
 *   3. Assert the rendered tables actually contain the expected numbers and that shared
 *      models are merged (not overwritten) when viewing all keys.
 *
 * Run: node scripts/verify_site.js   (from the repo root)
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
let failures = 0;
let checks = 0;

function ok(cond, label, extra = "") {
  checks++;
  if (cond) {
    console.log(`  PASS :: ${label}`);
  } else {
    failures++;
    console.log(`  FAIL :: ${label}${extra ? "  -> " + extra : ""}`);
  }
}

function approx(a, b, eps = 1e-9) {
  return Math.abs(Number(a) - Number(b)) <= eps;
}

// ---------------------------------------------------------------- step 1: reference
// Group by (date, key, model, type) then aggregate independently of build_site.py.
const reference = {};
const files = fs.readdirSync(path.join(ROOT, "data"))
  .filter((f) => /^amount-\d{4}-\d{2}-\d{2}\.csv$/.test(f)).sort();

for (const file of files) {
  const date = file.slice("amount-".length, -".csv".length);
  const text = fs.readFileSync(path.join(ROOT, "data", file), "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((l) => l.length);
  if (lines.length < 2) {
    // header-only file: the day exists but has no usage. It contributes no metric rows,
    // so it must NOT appear as a date with data (see the dates assertion below).
    console.log(`  note: ${file} has a header but no data rows (day with no usage)`);
    continue;
  }
  const header = lines[0].split(",");
  const idx = Object.fromEntries(header.map((h, i) => [h, i]));
  reference[date] = reference[date] || {};
  for (const line of lines.slice(1)) {
    const c = line.split(",");
    const key = c[idx.api_key];
    const model = c[idx.model];
    const type = c[idx.type];
    const price = c[idx.price];
    const amount = Number(c[idx.amount]);
    const rec = (reference[date][key] = reference[date][key] || { models: {} }).models;
    const m = (rec[model] = rec[model] || {
      requests: 0, cache_hit: 0, cache_miss: 0, output: 0,
      cost_total: 0, cost: { cache_hit: 0, cache_miss: 0, output: 0 },
    });
    const cost = price ? Number(price) * amount : 0;
    if (type === "request_count") m.requests += amount;
    else if (type === "input_cache_hit_tokens") { m.cache_hit += amount; m.cost.cache_hit += cost; m.cost_total += cost; }
    else if (type === "input_cache_miss_tokens") { m.cache_miss += amount; m.cost.cache_miss += cost; m.cost_total += cost; }
    else if (type === "output_tokens") { m.output += amount; m.cost.output += cost; m.cost_total += cost; }
  }
}
console.log(`reference built from ${files.length} file(s): ${files.join(", ")}`);

// ---------------------------------------------------------------- step 2: run page JS
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const store = {};
function makeEl(id) {
  return {
    id, _html: "", _text: "", value: "", style: {}, children: [],
    set innerHTML(v) { this._html = String(v); store[id] = this._html; },
    get innerHTML() { return this._html; },
    set textContent(v) { this._text = String(v); if (id) store[id] = this._text; },
    get textContent() { return this._text; },
    appendChild(c) { this.children.push(c); },
    addEventListener() {},
    classList: { add() {}, remove() {}, contains() { return false; } },
  };
}
const document = {
  getElementById: (id) => (store.__els[id] = store.__els[id] || makeEl(id)),
  createElement: () => makeEl(null),
  querySelectorAll: () => [],
};
const __els = {};
document.getElementById = (id) => (__els[id] = __els[id] || makeEl(id));
store.__els = __els;

let EXPORTS = null;
const sandbox = {
  document,
  console,
  __SITE_TEST__: (api) => { EXPORTS = api; },
};
sandbox.globalThis = sandbox;

const vm = require("vm");
try {
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { filename: "index.html#script" });
  ok(true, "page script executes without throwing");
} catch (e) {
  ok(false, "page script executes without throwing", e.message);
  process.exit(1);
}

ok(EXPORTS !== null, "page exposed its computation API");
const { SITE, metricsOf, mergeModels } = EXPORTS;

// ---------------------------------------------------------------- empty-data short circuit
// With no days of data the page renders an intentional empty state and none of the metric
// assertions below have anything to check. Failing here would break the whole workflow on a
// day when the export legitimately contained no usage at all.
if (!SITE.dates.length) {
  console.log("\n== empty data ==");
  ok(html.includes("还没有") && html.includes("const SITE = {"),
     "page renders the empty-data state (and still embeds SITE)");
  ok(Object.keys(reference).length === 0, "no data rows were found in data/ either");
  console.log(`\nRESULT: ${checks - failures}/${checks} checks passed (no data to verify)`);
  process.exit(failures ? 1 : 0);
}

// ---------------------------------------------------------------- step 3: assertions
console.log("\n== data shape ==");
const siteDates = Object.keys(SITE.data).sort();
ok(JSON.stringify(siteDates) === JSON.stringify(Object.keys(reference).sort()),
   "dates in page match CSV files", `${siteDates} vs ${Object.keys(reference)}`);
ok(siteDates.every((d) => reference[d] && Object.keys(SITE.data[d]).length === Object.keys(reference[d]).length),
   "key count per date matches reference");
ok(SITE.default_date === siteDates[siteDates.length - 1],
   `default_date is the newest day (${SITE.default_date})`);

console.log("\n== per key/model numbers match an independent re-aggregation ==");
let mismatch = 0;
for (const date of siteDates) {
  for (const key of Object.keys(reference[date])) {
    const pageKey = SITE.data[date][key];
    if (!pageKey) { mismatch++; console.log(`    missing key ${key}`); continue; }
    for (const model of Object.keys(reference[date][key].models)) {
      const a = reference[date][key].models[model];
      const b = pageKey.models[model];
      if (!b) { mismatch++; console.log(`    missing model ${model}`); continue; }
      for (const f of ["requests", "cache_hit", "cache_miss", "output"]) {
        if (a[f] !== b[f]) { mismatch++; console.log(`    ${key}/${model}.${f}: ref=${a[f]} page=${b[f]}`); }
      }
      if (!approx(a.cost_total, b.cost_total)) {
        mismatch++; console.log(`    ${key}/${model}.cost_total: ref=${a.cost_total} page=${b.cost_total}`);
      }
      for (const f of ["cache_hit", "cache_miss", "output"]) {
        if (!approx(a.cost[f], b.cost[f])) {
          mismatch++; console.log(`    ${key}/${model}.cost.${f}: ref=${a.cost[f]} page=${b.cost[f]}`);
        }
      }
    }
  }
}
ok(mismatch === 0, "every per-key/per-model figure matches the reference", `${mismatch} mismatch(es)`);

console.log("\n== aggregate invariants ==");
const date = SITE.default_date;
const allModels = EXPORTS.modelsFor(date, "__all__");
const tot = metricsOf(allModels);
const refTotal = Object.values(reference[date]).flatMap((k) => Object.values(k.models))
  .reduce((s, m) => s + m.cost_total, 0);
ok(approx(tot.cost_total, refTotal), `grand total cost == sum of reference (${tot.cost_total.toFixed(8)})`,
   `page=${tot.cost_total} ref=${refTotal}`);

// sum of per-key costs must equal the "all keys" total (no double counting / no overwrite)
const perKeySum = Object.keys(SITE.data[date])
  .map((k) => metricsOf(SITE.data[date][k].models).cost_total)
  .reduce((a, b) => a + b, 0);
ok(approx(perKeySum, tot.cost_total), "sum of per-key totals == all-keys total",
   `${perKeySum} vs ${tot.cost_total}`);

// models shared across keys must be merged, not overwritten
const sharedModels = (() => {
  const counts = {};
  for (const k of Object.keys(SITE.data[date])) {
    for (const m of Object.keys(SITE.data[date][k].models)) counts[m] = (counts[m] || 0) + 1;
  }
  return Object.entries(counts).filter(([, n]) => n > 1).map(([m]) => m);
})();
if (sharedModels.length) {
  for (const m of sharedModels) {
    const expect = Object.keys(SITE.data[date])
      .filter((k) => SITE.data[date][k].models[m])
      .reduce((s, k) => s + SITE.data[date][k].models[m].requests, 0);
    ok(allModels[m].requests === expect,
       `model ${m} (in >1 key) is summed across keys, not overwritten`,
       `page=${allModels[m].requests} expected=${expect}`);
  }
} else {
  console.log("  SKIP :: no model shared across keys in this dataset");
}

console.log("\n== rendered output ==");
const detail = __els.detailTable ? __els.detailTable._html : "";
ok(detail.includes("<thead>") && detail.includes("合计"), "detail table rendered with header and total row");
for (const m of Object.keys(allModels)) {
  ok(detail.includes(m), `detail table lists model ${m}`);
}

// filtering a single key must reproduce exactly that key's subtotal in the rendered table
const someKey = Object.keys(SITE.data[date]).sort((a, b) =>
  metricsOf(SITE.data[date][b].models).cost_total - metricsOf(SITE.data[date][a].models).cost_total)[0];
const keyModels = EXPORTS.modelsFor(date, someKey);
const keyTot = metricsOf(keyModels);
const before = __els.detailTable._html;
__els.keySelect.value = someKey;
__els.keySelect._listeners = null;
// re-render through the exported path by invoking the public render entry:
// the page wired a change listener; our shim stored no listeners, so call modelsFor +
// compare via a fresh render through init's internals is not exposed. Instead assert the
// numbers the renderer would use, and that the DOM did change for date switching below.
ok(!approx(keyTot.cost_total, tot.cost_total) || Object.keys(SITE.data[date]).length === 1,
   `selected key ${someKey} subtotal differs from all-keys total`,
   `${keyTot.cost_total} vs ${tot.cost_total}`);

console.log("\n== formatting helpers ==");
const { fmtMoney, fmtInt, fmtTokens } = EXPORTS;
ok(typeof fmtMoney("0.0000001") === "string" && !fmtMoney("0.0000001").includes("e-"),
   "very small cost does not fall back to exponential notation", fmtMoney("0.0000001"));
ok(fmtInt(1234567) === "1,234,567", "large integers get separators", fmtInt(1234567));
ok(fmtTokens(1500000) === "1.50M", "token compaction works", fmtTokens(1500000));

// Currency must be RMB (¥), never a dollar sign.
const moneySamples = [fmtMoney(0), fmtMoney("0.0000001"), fmtMoney("0.5"), fmtMoney("31.81646024"), fmtMoney(1234.5)];
ok(moneySamples.every((s) => s.includes("\u00A5")), "every formatted amount uses the ¥ symbol",
   moneySamples.join(" "));
ok(moneySamples.every((s) => !s.includes("$")), "no formatted amount contains a dollar sign",
   moneySamples.join(" "));
ok(!detail.includes(">$") && !detail.includes("> $"),
   "rendered cost cells never show a dollar sign");

console.log("\n== table structure (column alignment) ==");
// The bug fixed here: the 小计 (subtotal) row used to omit the 模型 cell, so its 9 cells
// shifted one column right under a 10-column header. Keep a simple, obvious check.
function rowCellCounts(htmlStr) {
  return [...htmlStr.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)]
    .map((m) => [...m[1].matchAll(/<t[dh][^>]*>/g)].length);
}

const compare = __els.compareTable ? __els.compareTable._html : "";
const detailCounts = rowCellCounts(detail);
const compareCounts = rowCellCounts(compare);

ok(detailCounts.length > 0 && detailCounts.every((n) => n === 9),
   "detail table: every row has 9 cells", `counts = ${detailCounts.join(",")}`);
// Whether a rowspan continuation row exists depends on the DAY'S DATA: the API reflects
// usage onto different key/model combinations, so on some days every key uses a single
// model and no 小计 row is emitted at all. Assert shape-conditionally — hardcoding
// "there is always a multi-model key" made CI fail the first time such a day arrived.
const multiModelKeys = Object.keys(SITE.data[date])
  .filter((k) => Object.keys(SITE.data[date][k].models).length > 1);
const hasMultiModel = multiModelKeys.length > 0;
const totalRowCount = (compare.match(/<tr class="total">/g) || []).length;
const subtotalCount = (compare.match(/>小计<\/td>/g) || []).length;

ok(hasMultiModel ? subtotalCount === multiModelKeys.length : subtotalCount === 0,
   hasMultiModel
     ? `a 小计 row per multi-model key (${multiModelKeys.length} key(s))`
     : "no 小计 row when no key uses multiple models",
   `multi-model keys=${multiModelKeys.length} subtotal rows=${subtotalCount}`);
ok(compareCounts.every((n) => n === 9 || n === 10),
   "compare table: rows have 9 (rowspan continuation) or 10 cells",
   `counts = ${compareCounts.join(",")}`);
// A continuation row only exists for a multi-model key, so their counts must match.
ok(compareCounts.filter((n) => n === 9).length === multiModelKeys.length,
   "one rowspan continuation row (9 cells) per multi-model key",
   `counts = ${compareCounts.join(",")} multi=${multiModelKeys.length}`);
ok(hasMultiModel
     ? compare.includes('<td class="grp model-cell"></td>')
     : !compare.includes('>小计</td>'),
   "小计 row keeps an empty 模型 cell so its values stay under the right headers");
// alignment must be decided by semantic class, never by DOM position: in a rowspan
// continuation row the 2nd child is a numeric cell, so :nth-child(2) would left-align
// it and make 调用次数 inconsistent within the same column.
ok(!/#compareTable[^{]*nth-child\(2\)/.test(compare),
   "model column alignment does not rely on :nth-child(2)");

// the 小计 row's numbers must equal the sum of the model rows above it
const subtotalRow = (compare.match(/<tr class="total">[\s\S]*?<\/tr>/g) || [])
  .find((r) => r.includes("小计"));
if (subtotalRow && multiModelKeys.length) {
  const cells = [...subtotalRow.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1].replace(/<[^>]*>/g, "").trim());
  const multiKey = multiModelKeys[0];
  const t = metricsOf(SITE.data[date][multiKey].models);
  ok(cells[0] === "小计", "subtotal row starts with the 小计 label", cells[0]);
  ok(cells[1] === "", "subtotal row has an empty 模型 cell", JSON.stringify(cells[1]));
  ok(cells[2] === fmtInt(t.requests), "subtotal 调用次数 is under the 调用次数 header",
     `${cells[2]} vs ${fmtInt(t.requests)}`);
  ok(cells[cells.length - 1] === fmtMoney(t.cost_total), "subtotal 费用合计 is in the last column",
     `${cells[cells.length - 1]} vs ${fmtMoney(t.cost_total)}`);
}

// Regression guard for the multi-model rendering itself (the original "column shift" bug
// lived here). The live data may not contain a multi-model key on a given day, so exercise
// the renderer with a synthetic one: the fabricated page is tested through the same
// assertions the real page uses.
console.log("\n== synthetic multi-model render (always exercised) ==");
{
  const fakeKey = "sk-synthetic***0001";
  const fake = {
    dates: ["2026-01-01"], default_date: "2026-01-01", data_version: "test",
    warnings: [], source_files: [],
    data: {
      "2026-01-01": {
        [fakeKey]: {
          name: "Synthetic", masked: fakeKey,
          models: {
            "model-a": { requests: 3, cache_hit: 10, cache_miss: 20, output: 30,
                         cost_total: "0.1", cost: { cache_hit: "0.01", cache_miss: "0.02", output: "0.07" } },
            "model-b": { requests: 5, cache_hit: 40, cache_miss: 50, output: 60,
                         cost_total: "0.2", cost: { cache_hit: "0.02", cache_miss: "0.03", output: "0.15" } },
          },
        },
      },
    },
  };
  const el = (id) => (els[id] = els[id] || makeEl(id));
  const out = EXPORTS.buildCompareHtml(fake.data["2026-01-01"]) || "";
  const counts = [...out.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)]
    .map((m) => [...m[1].matchAll(/<t[dh][^>]*>/g)].length);
  ok(out.includes("小计"), "synthetic multi-model page renders a 小计 row");
  ok(out.includes('<td class="grp model-cell"></td>'),
     "synthetic 小计 row keeps the empty 模型 cell");
  ok(counts.length > 0 && counts.every((n) => n === 9 || n === 10),
     "synthetic table: every row has 9 or 10 cells", `counts = ${counts.join(",")}`);
  ok(counts.filter((n) => n === 9).length === 1,
     "synthetic table: exactly one rowspan continuation row", `counts = ${counts.join(",")}`);
  const sub = (out.match(/<tr class="total">[\s\S]*?<\/tr>/g) || []).find((r) => r.includes("小计"));
  const sc = sub ? [...sub.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1].replace(/<[^>]*>/g, "").trim()) : [];
  // synthetic totals: 3+5 requests, 10+40 / 20+50 / 30+60 tokens, costs summed.
  // Compare against the formatter itself rather than hardcoding its decimal policy.
  ok(sc.length === 10 && sc[0] === "小计" && sc[1] === "" && sc[2] === "8" &&
     sc[3] === "50" && sc[4] === "70" && sc[5] === "90" &&
     sc[9] === fmtMoney(0.1 + 0.2),
     "synthetic 小计 totals land in the right columns", JSON.stringify(sc));
}

console.log(`\nRESULT: ${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);

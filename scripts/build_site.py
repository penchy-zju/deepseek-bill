#!/usr/bin/env python3
"""Build the static usage dashboard from data/amount-*.csv.

Outputs (repo root, served by GitHub Pages):
  index.html      - self-contained dashboard with data embedded (no fetch, no deps)
  site-data.json  - the same aggregated data for external consumers

Only the Python standard library is used so this runs on a bare ubuntu-latest runner.

Cost model: every data row carries `price` (already accounting for the platform's
off-peak discount) and `amount`, so cost = price * amount. Money is accumulated with
Decimal to avoid binary-float drift.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

DATA_GLOB = "data/amount-*.csv"
OUT_HTML = "index.html"
OUT_JSON = "site-data.json"
BEIJING = timezone(timedelta(hours=8))

# type -> (metric key, cost key)
METRICS = {
    "request_count": ("requests", None),
    "input_cache_hit_tokens": ("cache_hit", "cache_hit"),
    "input_cache_miss_tokens": ("cache_miss", "cache_miss"),
    "output_tokens": ("output", "output"),
}


def empty_metrics() -> dict:
    return {"requests": 0, "cache_hit": 0, "cache_miss": 0, "output": 0,
            "cost_total": Decimal(0),
            "cost": {"cache_hit": Decimal(0), "cache_miss": Decimal(0), "output": Decimal(0)}}


def date_from_path(path: str) -> str:
    base = os.path.basename(path)
    return base[len("amount-"):-len(".csv")]


def parse_amount_csv(path: str, warnings: list[str]) -> dict:
    """Return {api_key: {name, masked, models: {model: metrics}}} for one daily file."""
    by_key: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"model", "api_key", "api_key_name", "type", "price", "amount"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            warnings.append(f"{os.path.basename(path)}: missing columns {sorted(missing)}; skipped")
            return {}
        for lineno, row in enumerate(reader, start=2):
            mtype = (row.get("type") or "").strip()
            spec = METRICS.get(mtype)
            if spec is None:
                if mtype:
                    warnings.append(f"{os.path.basename(path)}:{lineno}: unknown type {mtype!r}; skipped")
                continue
            metric_key, cost_key = spec
            key = (row.get("api_key") or "").strip()
            model = (row.get("model") or "").strip()
            if not key or not model:
                warnings.append(f"{os.path.basename(path)}:{lineno}: empty api_key/model; skipped")
                continue
            try:
                amount = int(Decimal((row.get("amount") or "0").strip() or "0"))
            except Exception:
                warnings.append(f"{os.path.basename(path)}:{lineno}: bad amount {row.get('amount')!r}; skipped")
                continue
            price_raw = (row.get("price") or "").strip()
            cost = Decimal(0)
            if price_raw and cost_key is not None:
                try:
                    cost = Decimal(price_raw) * amount
                except Exception:
                    warnings.append(f"{os.path.basename(path)}:{lineno}: bad price {price_raw!r}; cost=0")

            entry = by_key.setdefault(key, {
                "name": (row.get("api_key_name") or "").strip() or "(未命名)",
                "masked": key,
                "models": defaultdict(empty_metrics),
            })
            rec = entry["models"][model]
            rec[metric_key] += amount
            if cost_key is not None:
                rec["cost"][cost_key] += cost
                rec["cost_total"] += cost
    # defaultdict -> plain dict for JSON
    for entry in by_key.values():
        entry["models"] = {m: rec for m, rec in entry["models"].items()}
    return by_key


def default_date(available: list[str]) -> str | None:
    """Yesterday in Beijing time, falling back to the newest date that has data."""
    if not available:
        return None
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    earlier = [d for d in available if d < today]
    return max(earlier) if earlier else max(available)


def build() -> dict:
    warnings: list[str] = []
    files = sorted(glob.glob(DATA_GLOB))
    dates: dict = {}
    digest = hashlib.sha256()
    for path in files:
        date = date_from_path(path)
        # 把输入内容纳入指纹：数据不变 -> 指纹不变 -> 生成结果逐字节相同，
        # 这样“无变化时不产生空提交”的判定才成立（绝不能嵌入构建时间戳，
        # 那会让每次构建都产生差异，从而每次都提交一次假更新）。
        with open(path, "rb") as fh:
            digest.update(fh.read())
        parsed = parse_amount_csv(path, warnings)
        if parsed:
            dates[date] = parsed

    available = sorted(dates)
    return {
        "data_version": digest.hexdigest()[:12],
        "default_date": default_date(available),
        "dates": available,
        "data": dates,
        "warnings": warnings,
        "source_files": [os.path.basename(p) for p in files],
    }


def to_jsonable(payload: dict) -> dict:
    """Decimal -> str, keep full precision (JS will parse to Number)."""
    out = {"data_version": payload["data_version"], "default_date": payload["default_date"],
           "dates": payload["dates"], "warnings": payload["warnings"],
           "source_files": payload["source_files"], "data": {}}
    for date, keys in payload["data"].items():
        out["data"][date] = {}
        for key, entry in keys.items():
            models = {}
            for model, rec in entry["models"].items():
                models[model] = {
                    "requests": rec["requests"],
                    "cache_hit": rec["cache_hit"],
                    "cache_miss": rec["cache_miss"],
                    "output": rec["output"],
                    "cost_total": str(rec["cost_total"]),
                    "cost": {k: str(v) for k, v in rec["cost"].items()},
                }
            out["data"][date][key] = {"name": entry["name"], "masked": entry["masked"], "models": models}
    return out


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="format-detection" content="telephone=no,address=no,email=no,date=no">
<title>DeepSeek 用量看板</title>
<style>
  :root {
    --bg:#f6f7f9; --card:#fff; --ink:#1b1f24; --muted:#6b7280; --line:#e5e7eb;
    --accent:#4f46e5; --accent-soft:#eef2ff; --good:#047857; --warn:#b45309;
    --shadow:0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.1);
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --card:#171a21; --ink:#e6e8eb; --muted:#9aa4b2; --line:#262b35;
            --accent:#8b8cf7; --accent-soft:#1e2130; --good:#34d399; --warn:#fbbf24;
            --shadow:0 1px 2px rgba(0,0,0,.4); }
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; }
  header h1 { margin:0 0 4px; font-size:22px; letter-spacing:.2px; }
  .sub { color:var(--muted); font-size:13px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:18px; margin-top:16px; box-shadow:var(--shadow); }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; box-shadow:var(--shadow); }
  .kpi .label { color:var(--muted); font-size:12px; }
  .kpi .value { font-size:22px; font-weight:650; margin-top:4px; font-variant-numeric:tabular-nums; }
  .kpi .hint { color:var(--muted); font-size:11px; margin-top:2px; }
  .controls { display:flex; flex-wrap:wrap; gap:16px; align-items:flex-end; }
  label { display:block; font-size:12px; color:var(--muted); margin-bottom:5px; }
  select { appearance:none; background:var(--card); color:var(--ink); border:1px solid var(--line);
           border-radius:9px; padding:9px 34px 9px 11px; font-size:14px; min-width:230px;
           background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
           background-position:calc(100% - 17px) 50%,calc(100% - 12px) 50%;
           background-size:5px 5px,5px 5px; background-repeat:no-repeat; }
  select:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .seg { display:inline-flex; border:1px solid var(--line); border-radius:9px; overflow:hidden; }
  .seg button { background:var(--card); color:var(--muted); border:0; padding:9px 14px; font-size:13px; cursor:pointer; }
  .seg button + button { border-left:1px solid var(--line); }
  .seg button[aria-pressed="true"] { background:var(--accent-soft); color:var(--accent); font-weight:600; }
  h2 { font-size:15px; margin:0 0 12px; }
  .tablewrap { overflow-x:auto; -webkit-overflow-scrolling:touch; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; font-variant-numeric:tabular-nums; }
  th, td { padding:9px 11px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
  th { color:var(--muted); font-weight:600; font-size:12px; text-align:right; background:transparent; }
  th:first-child, td:first-child { text-align:left; }
  tbody tr:hover { background:var(--accent-soft); }
  tr.total td { font-weight:650; border-top:2px solid var(--line); border-bottom:0; }
  .grp { border-left:1px solid var(--line); }
  .model { display:inline-flex; align-items:center; gap:7px; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--accent); flex:none; }
  .muted { color:var(--muted); }
  .key { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:11px;
         word-break:break-all; -webkit-text-size-adjust:100%; }
  /* 明确定义颜色/样式，避免浏览器把 sk-xxx 之类的字符串当成电话号码/链接自动着色 */
  table td, table th { color:inherit; text-decoration:none; }
  #compareTable td:first-child, #detailTable td:first-child { min-width:170px; }
  .note { color:var(--muted); font-size:12px; margin-top:10px; }
  .banner { background:var(--accent-soft); border:1px solid var(--line); color:var(--ink);
            border-radius:10px; padding:11px 14px; font-size:13px; margin-top:16px; }
  details summary { cursor:pointer; color:var(--muted); font-size:12.5px; }
  .rate { color:var(--muted); font-size:11.5px; }
  footer { color:var(--muted); font-size:12px; margin-top:22px; text-align:center; }
  .empty { text-align:center; color:var(--muted); padding:36px 0; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>DeepSeek 用量看板</h1>
    <div class="sub" id="subtitle">加载中…</div>
  </header>

  <div class="card controls">
    <div>
      <label for="keySelect">API Key</label>
      <select id="keySelect"></select>
    </div>
    <div>
      <label for="dateSelect">日期</label>
      <select id="dateSelect"></select>
    </div>
  </div>

  <div class="cards" id="kpis" style="margin-top:16px"></div>

  <div class="card">
    <h2 id="detailTitle">按模型明细</h2>
    <div class="tablewrap"><table id="detailTable"></table></div>
    <div class="note" id="detailNote"></div>
  </div>

  <div class="card">
    <h2>全部 API Key 对比</h2>
    <div class="tablewrap"><table id="compareTable"></table></div>
    <div class="note">无 <span class="muted">模型</span> 列时表示该 Key 当日仅有一个模型；费用按接口返回的 <code>price × amount</code> 实算，已包含优惠时段折扣。</div>
  </div>

  <div class="card">
    <details>
      <summary>数据说明与已知限制</summary>
      <div class="note" id="limits"></div>
    </details>
  </div>

  <footer id="footer"></footer>
</div>

<script>
const SITE = __SITE_DATA__;

const fmtInt = n => (n || 0).toLocaleString('en-US');
const fmtTokens = n => {                       // 1.23M / 456.7K
  n = n || 0;
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
};
const fmtMoney = v => {                        // 自适应精度，避免小费用被显示成 $0.0000
  const n = Number(v) || 0;
  if (n === 0) return '$0';
  if (n < 0.0001) return '$' + n.toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
  if (n < 1) return '$' + n.toFixed(6);
  return '$' + n.toFixed(4);
};
const sum = (a, b) => (Number(a) || 0) + (Number(b) || 0);

// 把多组 {model: metrics} 合并成一组（同名模型逐项相加，绝不能直接 Object.assign 覆盖）
function mergeModels(groups) {
  const out = {};
  for (const models of groups) {
    for (const [m, rec] of Object.entries(models || {})) {
      if (!out[m]) out[m] = {requests:0,cache_hit:0,cache_miss:0,output:0,cost_total:0,cost:{cache_hit:0,cache_miss:0,output:0}};
      const t = out[m];
      t.requests += rec.requests; t.cache_hit += rec.cache_hit;
      t.cache_miss += rec.cache_miss; t.output += rec.output;
      t.cost_total = sum(t.cost_total, rec.cost_total);
      for (const k of Object.keys(t.cost)) t.cost[k] = sum(t.cost[k], rec.cost[k]);
    }
  }
  return out;
}

function metricsOf(models) {
  const t = { requests:0, cache_hit:0, cache_miss:0, output:0, cost_total:0,
              cost:{cache_hit:0, cache_miss:0, output:0} };
  for (const m of Object.values(models)) {
    t.requests += m.requests; t.cache_hit += m.cache_hit;
    t.cache_miss += m.cache_miss; t.output += m.output;
    t.cost_total = sum(t.cost_total, m.cost_total);
    for (const k of Object.keys(t.cost)) t.cost[k] = sum(t.cost[k], m.cost[k]);
  }
  return t;
}

function init() {
  const dateSel = document.getElementById('dateSelect');
  const keySel = document.getElementById('keySelect');

  if (!SITE.dates.length) {
    document.getElementById('subtitle').textContent = '暂无数据';
    document.querySelectorAll('.card').forEach((el, i) => { if (i > 0) el.style.display = 'none'; });
    document.getElementById('kpis').innerHTML = '<div class="card empty">data/ 目录下还没有 amount-*.csv 数据文件。</div>';
    return;
  }

  SITE.dates.slice().reverse().forEach(d => {
    const o = document.createElement('option');
    o.value = d; o.textContent = d + (d === SITE.default_date ? '（最新）' : '');
    dateSel.appendChild(o);
  });
  dateSel.value = SITE.default_date || SITE.dates[SITE.dates.length - 1];

  function keysFor(date) {
    const entry = SITE.data[date] || {};
    return Object.keys(entry).map(k => {
      const t = metricsOf(entry[k].models);
      return { key:k, name:entry[k].name, masked:entry[k].masked, total:t.cost_total };
    }).sort((a, b) => b.total - a.total);
  }

  function renderKeys() {
    const date = dateSel.value;
    const keys = keysFor(date);
    const all = metricsOf(mergeModels(keys.map(k => SITE.data[date][k.key].models)));
    keySel.innerHTML = '';
    const optAll = document.createElement('option');
    optAll.value = '__all__';
    optAll.textContent = `全部 API Key（${keys.length} 个）— 合计 ${fmtMoney(all.cost_total)}`;
    keySel.appendChild(optAll);
    keys.forEach(k => {
      const o = document.createElement('option');
      o.value = k.key;
      o.textContent = `${k.name} — ${fmtMoney(k.total)}`;
      keySel.appendChild(o);
    });
    keySel.value = '__all__';
  }

  function renderDetail() {
    const date = dateSel.value;
    const sel = keySel.value;
    const day = SITE.data[date] || {};
    const isAll = sel === '__all__';

    const models = {};
    if (isAll) {
      Object.assign(models, mergeModels(Object.keys(day).map(k => day[k].models)));
    } else {
      Object.assign(models, (day[sel] || {models:{}}).models);
    }

    const label = isAll ? `全部 API Key（${Object.keys(day).length} 个）` :
      `${(day[sel]||{}).name || ''} <span class="muted">${sel}</span>`;
    document.getElementById('detailTitle').innerHTML = `按模型明细 · ${date} · ${label}`;

    const total = metricsOf(models);
    const rows = Object.entries(models).sort((a, b) => Number(b[1].cost_total) - Number(a[1].cost_total));

    if (!rows.length) {
      document.getElementById('detailTable').innerHTML = '<tbody><tr><td class="empty">该日期无数据</td></tr></tbody>';
      document.getElementById('detailNote').textContent = '';
      return;
    }

    const head = `<thead><tr>
      <th>模型</th>
      <th class="grp">调用次数</th>
      <th class="grp">缓存命中 tokens</th><th>命中费用</th>
      <th class="grp">缓存未命中 tokens</th><th>未命中费用</th>
      <th class="grp">输出 tokens</th><th>输出费用</th>
      <th class="grp">费用合计</th>
    </tr></thead>`;
    const body = rows.map(([m, r]) => `<tr>
      <td><span class="model"><span class="dot"></span>${m}</span></td>
      <td class="grp">${fmtInt(r.requests)}</td>
      <td class="grp">${fmtInt(r.cache_hit)}</td><td>${fmtMoney(r.cost.cache_hit)}</td>
      <td class="grp">${fmtInt(r.cache_miss)}</td><td>${fmtMoney(r.cost.cache_miss)}</td>
      <td class="grp">${fmtInt(r.output)}</td><td>${fmtMoney(r.cost.output)}</td>
      <td class="grp">${fmtMoney(r.cost_total)}</td>
    </tr>`).join('');
    const foot = `<tr class="total">
      <td>合计</td>
      <td class="grp">${fmtInt(total.requests)}</td>
      <td class="grp">${fmtInt(total.cache_hit)}</td><td>${fmtMoney(total.cost.cache_hit)}</td>
      <td class="grp">${fmtInt(total.cache_miss)}</td><td>${fmtMoney(total.cost.cache_miss)}</td>
      <td class="grp">${fmtInt(total.output)}</td><td>${fmtMoney(total.cost.output)}</td>
      <td class="grp">${fmtMoney(total.cost_total)}</td>
    </tr>`;
    document.getElementById('detailTable').innerHTML = head + '<tbody>' + body + foot + '</tbody>';

    const totTokens = total.cache_hit + total.cache_miss + total.output;
    const hitRate = (total.cache_hit + total.cache_miss) > 0
      ? (total.cache_hit / (total.cache_hit + total.cache_miss) * 100).toFixed(1) : '—';
    document.getElementById('detailNote').innerHTML =
      `总 tokens ${fmtTokens(totTokens)}（输入 ${fmtTokens(total.cache_hit + total.cache_miss)} / 输出 ${fmtTokens(total.output)}）· ` +
      `输入缓存命中率 ${hitRate}% · 费用合计 ${fmtMoney(total.cost_total)}。鼠标悬停任意数字可查看精确值。`;
  }

  function renderKpis() {
    const date = dateSel.value;
    const sel = keySel.value;
    const day = SITE.data[date] || {};
    const isAll = sel === '__all__';
    const models = isAll
      ? mergeModels(Object.keys(day).map(k => day[k].models))
      : (day[sel] || {models:{}}).models;
    const t = metricsOf(models);
    const totTokens = t.cache_hit + t.cache_miss + t.output;
    const hitRate = (t.cache_hit + t.cache_miss) > 0
      ? (t.cache_hit / (t.cache_hit + t.cache_miss) * 100).toFixed(1) + '%' : '—';
    const kpis = [
      { label:'总费用', value: fmtMoney(t.cost_total), hint: date + (isAll ? ' · 全部 Key' : '') },
      { label:'调用次数', value: fmtInt(t.requests), hint: '接口请求数' },
      { label:'总 tokens', value: fmtTokens(totTokens), hint: fmtInt(totTokens) },
      { label:'输入缓存命中率', value: hitRate, hint: `命中 ${fmtTokens(t.cache_hit)} / 未命中 ${fmtTokens(t.cache_miss)}` },
      { label:'模型数', value: Object.keys(models).length, hint: '当日有调用的模型' },
    ];
    document.getElementById('kpis').innerHTML = kpis.map(k =>
      `<div class="kpi"><div class="label">${k.label}</div><div class="value">${k.value}</div><div class="hint">${k.hint}</div></div>`
    ).join('');
  }

  function renderCompare() {
    const date = dateSel.value;
    const day = SITE.data[date] || {};
    const keys = keysFor(date);
    if (!keys.length) { document.getElementById('compareTable').innerHTML = ''; return; }
    const head = `<thead><tr>
      <th>API Key</th><th class="grp">模型</th>
      <th class="grp">调用次数</th><th>命中 tokens</th><th>未命中 tokens</th><th>输出 tokens</th>
      <th class="grp">命中费用</th><th>未命中费用</th><th>输出费用</th><th class="grp">费用合计</th>
    </tr></thead>`;
    let body = '';
    const grand = metricsOf(mergeModels(keys.map(k => day[k.key].models)));
    for (const k of keys) {
      const entry = day[k.key];
      const modelNames = Object.keys(entry.models).sort();
      const t = metricsOf(entry.models);
      modelNames.forEach((m, i) => {
        const r = entry.models[m];
        body += '<tr>';
        if (i === 0) {
          body += `<td rowspan="${modelNames.length}"><strong>${entry.name}</strong><br><span class="muted key" title="${k.key}">${k.key}</span></td>`;
        }
        body += `<td class="grp">${m}</td>
          <td class="grp">${fmtInt(r.requests)}</td>
          <td>${fmtInt(r.cache_hit)}</td><td>${fmtInt(r.cache_miss)}</td><td>${fmtInt(r.output)}</td>
          <td class="grp">${fmtMoney(r.cost.cache_hit)}</td><td>${fmtMoney(r.cost.cache_miss)}</td><td>${fmtMoney(r.cost.output)}</td>
          <td class="grp">${fmtMoney(r.cost_total)}</td></tr>`;
        if (i === modelNames.length - 1 && modelNames.length > 1) {
          body += `<tr class="total"><td class="grp">小计</td>
            <td class="grp">${fmtInt(t.requests)}</td><td>${fmtInt(t.cache_hit)}</td><td>${fmtInt(t.cache_miss)}</td><td>${fmtInt(t.output)}</td>
            <td class="grp">${fmtMoney(t.cost.cache_hit)}</td><td>${fmtMoney(t.cost.cache_miss)}</td><td>${fmtMoney(t.cost.output)}</td>
            <td class="grp">${fmtMoney(t.cost_total)}</td></tr>`;
        }
      });
    }
    body += `<tr class="total"><td>全部合计</td><td class="grp">${keys.length} 个 Key</td>
      <td class="grp">${fmtInt(grand.requests)}</td><td>${fmtInt(grand.cache_hit)}</td><td>${fmtInt(grand.cache_miss)}</td><td>${fmtInt(grand.output)}</td>
      <td class="grp">${fmtMoney(grand.cost.cache_hit)}</td><td>${fmtMoney(grand.cost.cache_miss)}</td><td>${fmtMoney(grand.cost.output)}</td>
      <td class="grp">${fmtMoney(grand.cost_total)}</td></tr>`;
    document.getElementById('compareTable').innerHTML = head + '<tbody>' + body + '</tbody>';
  }

  function renderAll() { renderKpis(); renderDetail(); renderCompare(); }

  dateSel.addEventListener('change', () => { renderKeys(); renderAll(); });
  keySel.addEventListener('change', renderAll);

  document.getElementById('subtitle').innerHTML =
    `数据日期：<strong>${SITE.default_date}</strong> · 共 ${SITE.dates.length} 天（${SITE.dates[0]} ~ ${SITE.dates[SITE.dates.length-1]}） · ` +
    `数据指纹 <code>${SITE.data_version}</code>`;
  document.getElementById('limits').innerHTML = `
    <ul style="margin:6px 0 0;padding-left:18px">
      <li>数据来自 DeepSeek 平台内部导出接口，<strong>只包含已归档的 <code>amount</code> 明细</strong>；接口未返回的小时不会出现在数据中。</li>
      <li><code>api_key</code> 在接口返回中已被平台<strong>掩码</strong>（形如 <code>sk-63b33***c1e8</code>），因此以“名称 + 掩码”共同标识一个 Key，掩码相同即视为同一个 Key。</li>
      <li>费用 = 每行 <code>price × amount</code> 累加，<code>price</code> 已包含平台优惠时段折扣（同一模型同一天可能出现两档单价），因此无需再手工打折。</li>
      <li>“调用次数”为接口 <code>request_count</code> 求和。</li>
      ${SITE.warnings.length ? `<li>解析警告 ${SITE.warnings.length} 条：<code>${SITE.warnings.slice(0,5).join(' / ')}</code>${SITE.warnings.length>5?' …':''}</li>` : ''}
    </ul>`;
  document.getElementById('footer').innerHTML =
    `源数据：<a href="data/amount-${SITE.default_date}.csv">data/amount-*.csv</a> · ` +
    `<a href="site-data.json">site-data.json</a> · 由 GitHub Actions 每日自动更新`;

  renderKeys();
  renderAll();
}
init();

// 测试钩子：仅在设置了全局 __SITE_TEST__ 时暴露内部函数与数据，
// 供本地/CI 的 Node 逻辑测试直接断言计算结果（浏览器中该变量不存在，无副作用）。
if (typeof __SITE_TEST__ !== 'undefined' && __SITE_TEST__) {
  __SITE_TEST__({
    SITE, metricsOf, mergeModels, fmtInt, fmtMoney, fmtTokens,
    keysFor: date => {
      const entry = SITE.data[date] || {};
      return Object.keys(entry).map(k => {
        const t = metricsOf(entry[k].models);
        return { key:k, name:entry[k].name, masked:entry[k].masked, total:t.cost_total };
      }).sort((a, b) => b.total - a.total);
    },
    modelsFor: (date, sel) => {
      const day = SITE.data[date] || {};
      return sel === '__all__'
        ? mergeModels(Object.keys(day).map(k => day[k].models))
        : ((day[sel] || {models:{}}).models);
    },
  });
}
</script>
</body>
</html>
"""


def write_outputs(payload: dict) -> None:
    jsonable = to_jsonable(payload)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(jsonable, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    embedded = json.dumps(jsonable, ensure_ascii=False, separators=(",", ":"))
    # Guard against a stray "</script>" inside data (not possible with CSV-derived
    # numbers/names, but cheap insurance).
    embedded = embedded.replace("</", "<\\/")
    html = TEMPLATE.replace("__SITE_DATA__", embedded)
    with open(OUT_HTML, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)


def main() -> int:
    payload = build()
    write_outputs(payload)
    n_days = len(payload["dates"])
    n_keys = sum(len(v) for v in payload["data"].values())
    print(f"built {OUT_HTML} and {OUT_JSON}: {n_days} day(s), {n_keys} key-day record(s)")
    if payload["default_date"]:
        print(f"default date: {payload['default_date']}")
    if not payload["dates"]:
        print("WARNING: no data/amount-*.csv found; page shows an empty state")
    for w in payload["warnings"][:10]:
        print(f"WARNING: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

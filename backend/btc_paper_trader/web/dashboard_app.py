from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, Response, jsonify, request

from ..config import load_config, package_root

# ダッシュボードから編集を許可する設定項目（それ以外のキーは拒否する）
_EDITABLE_PARAMS: list[dict[str, Any]] = [
    {"path": "combine.entry_threshold", "label": "エントリー閾値", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "help": "小さいほど取引が増える"},
    {"path": "combine.weight_model", "label": "モデル比重", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.05, "help": "パターン比重は自動で 1−この値"},
    {"path": "filters.min_confidence", "label": "最低確信度", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "help": "大きいほど厳選"},
    {"path": "risk.tp_atr_mult", "label": "利確幅 (ATR倍)", "type": "float",
     "min": 0.1, "max": 10.0, "step": 0.1, "help": ""},
    {"path": "risk.sl_atr_mult", "label": "損切り幅 (ATR倍)", "type": "float",
     "min": 0.1, "max": 10.0, "step": 0.1, "help": ""},
    {"path": "risk.max_hold_bars", "label": "最大保有バー数", "type": "int",
     "min": 1, "max": 500, "step": 1, "help": "15分足の本数"},
    {"path": "risk.position_fraction", "label": "投入資金割合", "type": "float",
     "min": 0.01, "max": 1.0, "step": 0.01, "help": ""},
    {"path": "risk.max_daily_loss_pct", "label": "日次最大損失率", "type": "float",
     "min": 0.005, "max": 1.0, "step": 0.005, "help": "超えると当日の新規停止"},
    {"path": "advisor.mode", "label": "Claudeアドバイザー", "type": "choice",
     "choices": ["advise", "gate", "off"], "help": "gate=vetoでエントリー中止"},
]

_DASH_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BTC Paper Trader — Dashboard</title>
  <script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e7ecf3;
      --muted: #8b98a8;
      --accent: #3d8fd1;
      --whatif: #bd8733;
      --pos: #3ecf8e;
      --neg: #e06c75;
      --border: #2a3545;
    }
    * { box-sizing: border-box; }
    body {
      font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 1rem 1.25rem 2rem;
      line-height: 1.5;
    }
    h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 0.25rem; }
    .sub { color: var(--muted); font-size: 0.875rem; margin-bottom: 1rem; }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.1rem;
      margin-top: 1rem;
    }
    section h2 {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin: 0 0 0.75rem;
      font-weight: 600;
    }
    .toolbar { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
    .toolbar a { color: var(--accent); text-decoration: none; font-size: 0.875rem; }
    .toolbar a:hover { text-decoration: underline; }
    #status { font-size: 0.8rem; color: var(--muted); }
    .range-btns { display: flex; gap: 0.25rem; }
    .range-btns button, .btn {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 6px;
      padding: 0.25rem 0.7rem;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .range-btns button.active {
      background: rgba(61, 143, 209, 0.18);
      border-color: var(--accent);
      color: var(--text);
    }
    .btn.primary { background: rgba(61, 143, 209, 0.18); border-color: var(--accent); color: var(--text); }
    .btn.warn { background: rgba(189, 135, 51, 0.18); border-color: var(--whatif); color: var(--text); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .tiles {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      margin-top: 1rem;
    }
    .tile {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.7rem 0.9rem;
    }
    .tile .k { font-size: 0.72rem; color: var(--muted); letter-spacing: 0.04em; }
    .tile .v { font-size: 1.15rem; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 0.15rem; }
    .tile .s { font-size: 0.72rem; color: var(--muted); margin-top: 0.1rem; }
    .pos { color: var(--pos); }
    .neg { color: var(--neg); }
    .chart-wrap { position: relative; width: 100%; }
    #price-chart { width: 100%; height: 400px; }
    #equity-chart { width: 100%; height: 170px; }
    .chart-note { font-size: 0.75rem; color: var(--muted); margin-top: 0.5rem; }
    .chart-note .mk { font-weight: 700; }
    .legend { display: flex; gap: 1rem; font-size: 0.75rem; color: var(--muted); margin-top: 0.4rem; }
    .legend .sw { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 0.3rem; }
    #chart-tooltip {
      position: absolute;
      display: none;
      z-index: 10;
      pointer-events: none;
      background: rgba(15, 20, 25, 0.95);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.4rem 0.6rem;
      font-size: 0.72rem;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
    th, td {
      text-align: right;
      padding: 0.45rem 0.5rem;
      border-bottom: 1px solid var(--border);
      font-variant-numeric: tabular-nums;
    }
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
    th { color: var(--muted); font-weight: 500; }
    tr:hover td { background: rgba(255,255,255,0.03); }
    .pill {
      display: inline-block;
      padding: 0.1rem 0.5rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    .pill.long { background: rgba(62, 207, 142, 0.15); color: var(--pos); }
    .pill.short { background: rgba(224, 108, 117, 0.15); color: var(--neg); }
    .pill.flat { background: rgba(139, 152, 168, 0.2); color: var(--muted); }
    .mono { font-family: ui-monospace, monospace; font-size: 0.78rem; }
    dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; }
    dt { color: var(--muted); }
    dd { margin: 0; font-variant-numeric: tabular-nums; }
    .grid2 { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
    .grid2 section { margin-top: 0; }
    .scroll-x { overflow-x: auto; }
    .form-grid { display: grid; grid-template-columns: auto 1fr; gap: 0.45rem 0.8rem; align-items: center; font-size: 0.85rem; }
    .form-grid label { color: var(--muted); }
    .form-grid .hint { grid-column: 2; font-size: 0.7rem; color: var(--muted); margin-top: -0.35rem; }
    .form-grid input, .form-grid select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 0.25rem 0.5rem;
      font-size: 0.85rem;
      width: 100%;
      max-width: 160px;
      font-variant-numeric: tabular-nums;
    }
    .form-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.9rem; align-items: center; }
    #settings-status, #whatif-status { font-size: 0.78rem; color: var(--muted); }
    .cmp-table td.better { color: var(--pos); font-weight: 600; }
    .marker-toggle { display: flex; gap: 0.25rem; align-items: center; font-size: 0.78rem; color: var(--muted); }
    @media (max-width: 640px) {
      #price-chart { height: 300px; }
      dl { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <h1>BTC Paper Trader</h1>
  <p class="sub">時刻は日本時間 (JST) · 実績表示は60秒ごとに自動更新</p>

  <div class="toolbar">
    <div class="range-btns" id="range-btns">
      <button data-hours="24" class="active">1日</button>
      <button data-hours="72">3日</button>
      <button data-hours="168">7日</button>
    </div>
    <div class="marker-toggle" id="marker-toggle" style="display:none">
      <span>チャートのマーカー:</span>
      <button class="btn active-src" data-src="actual">実績</button>
      <button class="btn" data-src="whatif">What-if</button>
    </div>
    <span id="status">読み込み中…</span>
    <a href="/api/state" target="_blank" rel="noopener">state</a>
    <a href="/api/trades?hours=24" target="_blank" rel="noopener">trades</a>
  </div>

  <div class="tiles" id="tiles"></div>

  <section>
    <h2 id="price-title">値動きと売買ポイント</h2>
    <div class="chart-wrap">
      <div id="price-chart"></div>
      <div id="chart-tooltip"></div>
    </div>
    <p class="chart-note">
      <span class="mk pos">▲</span> ロング建玉
      <span class="mk neg">▼</span> ショート建玉
      <span class="mk">●</span> 決済（緑=利益 / 赤=損失、数字は損益）
    </p>
  </section>

  <section>
    <h2>資産推移（評価額）</h2>
    <div id="equity-chart"></div>
    <div class="legend" id="equity-legend" style="display:none">
      <span><span class="sw" style="background:var(--accent)"></span>実績</span>
      <span><span class="sw" style="background:var(--whatif)"></span>What-if（初期資金から再計算）</span>
    </div>
  </section>

  <div class="grid2" style="margin-top: 1rem;">
    <section>
      <h2>設定（保存で本番反映 / What-ifで過去を試算）</h2>
      <div class="form-grid" id="settings-form"></div>
      <div class="form-actions" id="token-row" style="display:none">
        <label style="font-size:0.78rem;color:var(--muted)">トークン:
          <input id="token-input" type="password" style="background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:0.2rem 0.5rem" />
        </label>
      </div>
      <div class="form-actions">
        <button class="btn primary" id="btn-save">保存して本番へ反映</button>
        <button class="btn warn" id="btn-whatif">この設定で過去をシミュレート</button>
        <span id="settings-status"></span>
      </div>
      <p class="chart-note" id="settings-note"></p>
    </section>
    <section>
      <h2>口座・ポジション</h2>
      <dl id="state-dl"></dl>
    </section>
  </div>

  <section id="whatif-section" style="display:none">
    <h2 id="whatif-title">What-if シミュレーション結果</h2>
    <p id="whatif-status"></p>
    <div class="scroll-x" id="whatif-compare"></div>
    <h2 style="margin-top:1rem">What-if の取引</h2>
    <div class="scroll-x">
      <table>
        <thead>
          <tr>
            <th>エントリー (JST)</th>
            <th>決済 (JST)</th>
            <th>方向</th>
            <th>建値</th>
            <th>決済値</th>
            <th>損益 (USDT)</th>
            <th>理由</th>
          </tr>
        </thead>
        <tbody id="whatif-trades-body"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>取引履歴（実績）</h2>
    <div class="scroll-x">
      <table>
        <thead>
          <tr>
            <th>エントリー (JST)</th>
            <th>決済 (JST)</th>
            <th>方向</th>
            <th>建値</th>
            <th>決済値</th>
            <th>損益 (USDT)</th>
            <th>理由</th>
          </tr>
        </thead>
        <tbody id="trades-body"></tbody>
      </table>
    </div>
  </section>

  <script>
    const POS = "#3ecf8e", NEG = "#e06c75", MUTED = "#8b98a8", ACCENT = "#3d8fd1", WHATIF = "#bd8733";
    const JST_OFFSET = 9 * 3600;
    let hours = 24;
    let priceChart = null, candleSeries = null, equityChart = null, equitySeries = null, whatifSeries = null;
    let actualTrades = [], whatifTrades = [], markerSource = "actual";
    let settingsSpec = [];
    let whatifTimer = null;

    const REASON_JA = { sl: "損切り", tp: "利確", time: "時間切れ", partial_tp: "部分利確" };

    function toChartTime(ms) { return Math.floor(ms / 1000) + JST_OFFSET; }
    function fmtJst(ms) {
      if (ms == null) return "—";
      const d = new Date(ms + 9 * 3600 * 1000);
      return d.toISOString().slice(5, 16).replace("T", " ");
    }
    function fmtNum(v, digits) {
      if (v == null || isNaN(v)) return "—";
      return Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }
    function pnlHtml(v, digits) {
      if (v == null) return "—";
      const cls = v >= 0 ? "pos" : "neg";
      return '<span class="' + cls + '">' + (v >= 0 ? "+" : "") + fmtNum(v, digits == null ? 2 : digits) + "</span>";
    }
    function sideLabel(s) {
      if (s === 1) return '<span class="pill long">LONG</span>';
      if (s === -1) return '<span class="pill short">SHORT</span>';
      return '<span class="pill flat">FLAT</span>';
    }
    function tokenHeaders() {
      const t = localStorage.getItem("dash_token") || "";
      return t ? { "Content-Type": "application/json", "X-Dashboard-Token": t } : { "Content-Type": "application/json" };
    }

    function initCharts() {
      if (!window.LightweightCharts) return false;
      const base = {
        layout: { background: { color: "transparent" }, textColor: MUTED, fontSize: 11 },
        grid: {
          vertLines: { color: "rgba(139,152,168,0.08)" },
          horzLines: { color: "rgba(139,152,168,0.08)" },
        },
        rightPriceScale: { borderColor: "#2a3545" },
        timeScale: { borderColor: "#2a3545", timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
        localization: { locale: "ja-JP" },
        autoSize: true,
      };
      priceChart = LightweightCharts.createChart(document.getElementById("price-chart"), base);
      candleSeries = priceChart.addCandlestickSeries({
        upColor: POS, downColor: NEG,
        borderUpColor: POS, borderDownColor: NEG,
        wickUpColor: POS, wickDownColor: NEG,
      });
      equityChart = LightweightCharts.createChart(document.getElementById("equity-chart"), base);
      equitySeries = equityChart.addLineSeries({
        color: ACCENT, lineWidth: 2,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      });

      const tooltip = document.getElementById("chart-tooltip");
      priceChart.subscribeCrosshairMove(function (param) {
        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }
        const d = param.seriesData.get(candleSeries);
        if (!d) { tooltip.style.display = "none"; return; }
        const ms = (param.time - JST_OFFSET) * 1000;
        tooltip.innerHTML =
          '<div style="color:' + MUTED + '">' + fmtJst(ms) + " JST</div>" +
          "<div>始 " + fmtNum(d.open, 1) + "　高 " + fmtNum(d.high, 1) + "</div>" +
          "<div>安 " + fmtNum(d.low, 1) + "　終 " + fmtNum(d.close, 1) + "</div>";
        tooltip.style.display = "block";
        const wrap = document.getElementById("price-chart");
        let x = param.point.x + 16, y = param.point.y + 16;
        if (x + tooltip.offsetWidth > wrap.clientWidth) x = param.point.x - tooltip.offsetWidth - 16;
        if (y + tooltip.offsetHeight > wrap.clientHeight) y = param.point.y - tooltip.offsetHeight - 16;
        tooltip.style.left = Math.max(0, x) + "px";
        tooltip.style.top = Math.max(0, y) + "px";
      });
      return true;
    }

    function computeStats(trades) {
      const closed = trades.filter(function (t) { return !t.partial && t.pnl != null; });
      const wins = closed.filter(function (t) { return t.pnl > 0; }).length;
      return {
        pnl: trades.reduce(function (a, t) { return a + (t.pnl || 0); }, 0),
        n: closed.length,
        wins: wins,
        winRate: closed.length ? wins / closed.length * 100 : null,
      };
    }

    function renderTiles(state, trades) {
      const s = computeStats(trades);
      const rangeLabel = hours === 24 ? "直近1日" : hours === 72 ? "直近3日" : "直近7日";
      const posLine = state.side === 0 || state.side == null
        ? '<span class="pill flat">FLAT</span>'
        : sideLabel(state.side) + ' <span class="s">建値 ' + fmtNum(state.entry_px, 1) + "</span>";
      const tiles = [
        { k: "評価額 (USDT)", v: fmtNum(state.quote, 2), s: "" },
        { k: rangeLabel + "の実現損益", v: pnlHtml(s.pnl), s: "取引 " + s.n + " 件" },
        { k: "勝率 (" + rangeLabel + ")", v: s.winRate == null ? "—" : fmtNum(s.winRate, 0) + "%", s: s.n ? s.wins + "勝" + (s.n - s.wins) + "敗" : "取引なし" },
        { k: "本日の損益", v: pnlHtml(state.daily_pnl), s: state.halt_new_entries ? "⚠ 新規停止中" : "" },
        { k: "現在ポジション", v: posLine, s: "" },
      ];
      document.getElementById("tiles").innerHTML = tiles.map(function (t) {
        return '<div class="tile"><div class="k">' + t.k + '</div><div class="v">' + t.v + '</div>' +
          (t.s ? '<div class="s">' + t.s + "</div>" : "") + "</div>";
      }).join("");
    }

    function buildMarkers(trades, minT) {
      const markers = [];
      trades.forEach(function (t) {
        if (t.entry_time != null && !t.partial) {
          const tt = toChartTime(t.entry_time);
          if (tt >= minT) markers.push({
            time: tt,
            position: t.side === 1 ? "belowBar" : "aboveBar",
            color: t.side === 1 ? POS : NEG,
            shape: t.side === 1 ? "arrowUp" : "arrowDown",
            text: (t.side === 1 ? "L " : "S ") + (t.entry_px != null ? Math.round(t.entry_px) : ""),
          });
        }
        if (t.exit_time != null) {
          const tt = toChartTime(t.exit_time);
          if (tt >= minT) markers.push({
            time: tt,
            position: t.side === 1 ? "aboveBar" : "belowBar",
            color: (t.pnl || 0) >= 0 ? POS : NEG,
            shape: "circle",
            text: t.pnl != null ? ((t.pnl >= 0 ? "+" : "") + t.pnl.toFixed(1)) : "",
          });
        }
      });
      markers.sort(function (a, b) { return a.time - b.time; });
      return markers;
    }

    let chartMinT = 0;
    function applyMarkers() {
      if (!candleSeries) return;
      const src = markerSource === "whatif" ? whatifTrades : actualTrades;
      candleSeries.setMarkers(buildMarkers(src, chartMinT));
    }

    function renderChart(klines) {
      if (!candleSeries) return;
      const candles = klines.map(function (k) {
        return { time: toChartTime(k.t), open: k.o, high: k.h, low: k.l, close: k.c };
      });
      candleSeries.setData(candles);
      chartMinT = candles.length ? candles[0].time : 0;
      applyMarkers();
      priceChart.timeScale().fitContent();
    }

    function dedupePoints(equity) {
      const seen = {};
      const pts = [];
      (equity || []).forEach(function (p) {
        const tt = toChartTime(p.t);
        if (!seen[tt]) { seen[tt] = 1; pts.push({ time: tt, value: p.q }); }
      });
      return pts;
    }

    function renderEquity(equity) {
      if (!equitySeries) return;
      equitySeries.setData(dedupePoints(equity));
      equityChart.timeScale().fitContent();
    }

    function tradesRowsHtml(trades) {
      if (!trades.length) {
        return '<tr><td colspan="7" style="text-align:center;color:var(--muted)">この期間の取引はありません</td></tr>';
      }
      return trades.slice().reverse().map(function (t) {
        const reason = REASON_JA[t.reason] || t.reason || "—";
        return "<tr>" +
          '<td class="mono">' + fmtJst(t.entry_time) + "</td>" +
          '<td class="mono">' + (t.open ? '<span class="pill flat">保有中</span>' : fmtJst(t.exit_time)) + "</td>" +
          "<td>" + sideLabel(t.side) + (t.partial ? ' <span style="color:var(--muted);font-size:0.7rem">½</span>' : "") + "</td>" +
          "<td>" + fmtNum(t.entry_px, 1) + "</td>" +
          "<td>" + fmtNum(t.exit_px, 1) + "</td>" +
          "<td>" + pnlHtml(t.pnl) + "</td>" +
          "<td>" + (t.open ? "—" : reason) + "</td>" +
          "</tr>";
      }).join("");
    }

    function renderStatePanel(state) {
      const sdl = document.getElementById("state-dl");
      const rows = [
        ["評価額 (quote)", fmtNum(state.quote, 2)],
        ["方向", sideLabel(state.side)],
        ["建値", state.side !== 0 ? fmtNum(state.entry_px, 1) : "—"],
        ["利確ライン (TP)", state.side !== 0 ? fmtNum(state.tp, 1) : "—"],
        ["損切りライン (SL)", state.side !== 0 ? fmtNum(state.sl, 1) : "—"],
        ["日次損益", pnlHtml(state.daily_pnl)],
        ["新規停止", state.halt_new_entries ? "はい" : "いいえ"],
      ];
      sdl.innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("");
    }

    // ---- 設定フォーム ----
    async function loadSettings() {
      const res = await fetch("/api/settings").then(function (r) { return r.json(); });
      settingsSpec = res.params || [];
      const form = document.getElementById("settings-form");
      form.innerHTML = settingsSpec.map(function (p, idx) {
        let input;
        if (p.type === "choice") {
          input = '<select data-idx="' + idx + '">' + p.choices.map(function (c) {
            return '<option value="' + c + '"' + (c === p.value ? " selected" : "") + ">" + c + "</option>";
          }).join("") + "</select>";
        } else {
          input = '<input type="number" data-idx="' + idx + '" value="' + (p.value != null ? p.value : "") +
            '" min="' + p.min + '" max="' + p.max + '" step="' + p.step + '" />';
        }
        const hint = p.help ? '<div class="hint">' + p.help + "</div>" : "";
        return "<label>" + p.label + "</label>" + input + hint;
      }).join("");
      document.getElementById("token-row").style.display = res.token_required ? "flex" : "none";
      if (res.token_required) {
        document.getElementById("token-input").value = localStorage.getItem("dash_token") || "";
      }
      document.getElementById("settings-note").textContent =
        "保存すると config.local.yaml に書き込まれ、稼働中のpaperループに最大" +
        Math.round((res.reload_seconds || 300) / 60) + "分で自動反映されます（再起動不要）。保存値は自動チューニングより優先されます。";
    }

    function collectValues() {
      const values = {};
      const inputs = document.querySelectorAll("#settings-form [data-idx]");
      for (let i = 0; i < inputs.length; i++) {
        const el = inputs[i];
        const spec = settingsSpec[parseInt(el.dataset.idx, 10)];
        if (!spec) continue;
        if (spec.type === "choice") {
          values[spec.path] = el.value;
        } else {
          const v = parseFloat(el.value);
          if (isNaN(v)) return { error: spec.label + " が数値ではありません" };
          if (v < spec.min || v > spec.max) return { error: spec.label + " は " + spec.min + "〜" + spec.max + " の範囲で指定してください" };
          values[spec.path] = spec.type === "int" ? Math.round(v) : v;
        }
      }
      return { values: values };
    }

    async function saveSettings() {
      const st = document.getElementById("settings-status");
      const c = collectValues();
      if (c.error) { st.textContent = "✗ " + c.error; return; }
      const tokenEl = document.getElementById("token-input");
      if (tokenEl && tokenEl.value) localStorage.setItem("dash_token", tokenEl.value);
      st.textContent = "保存中…";
      try {
        const res = await fetch("/api/settings", {
          method: "POST",
          headers: tokenHeaders(),
          body: JSON.stringify({ values: c.values }),
        });
        const body = await res.json();
        if (!res.ok) { st.textContent = "✗ " + (body.error || res.status); return; }
        st.textContent = "✓ 保存しました（" + new Date().toLocaleTimeString("ja-JP") + "）";
      } catch (e) {
        st.textContent = "✗ " + e;
      }
    }

    // ---- What-if ----
    async function startWhatIf() {
      const st = document.getElementById("settings-status");
      const c = collectValues();
      if (c.error) { st.textContent = "✗ " + c.error; return; }
      const tokenEl = document.getElementById("token-input");
      if (tokenEl && tokenEl.value) localStorage.setItem("dash_token", tokenEl.value);
      st.textContent = "";
      try {
        const res = await fetch("/api/whatif", {
          method: "POST",
          headers: tokenHeaders(),
          body: JSON.stringify({ values: c.values, hours: hours }),
        });
        const body = await res.json();
        if (!res.ok) {
          st.textContent = "✗ " + (body.error || res.status);
          return;
        }
        showWhatifRunning();
        pollWhatIf();
      } catch (e) {
        st.textContent = "✗ " + e;
      }
    }

    function showWhatifRunning() {
      const sec = document.getElementById("whatif-section");
      sec.style.display = "block";
      document.getElementById("whatif-status").textContent =
        "計算中… モデル学習とシミュレーションに数十秒〜数分かかることがあります。";
      document.getElementById("btn-whatif").disabled = true;
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function pollWhatIf() {
      if (whatifTimer) clearTimeout(whatifTimer);
      whatifTimer = setTimeout(async function () {
        try {
          const res = await fetch("/api/whatif").then(function (r) { return r.json(); });
          if (res.status === "running") {
            document.getElementById("whatif-status").textContent =
              "計算中… （" + Math.round(res.elapsed_seconds || 0) + "秒経過）";
            pollWhatIf();
            return;
          }
          document.getElementById("btn-whatif").disabled = false;
          if (res.status === "error") {
            document.getElementById("whatif-status").textContent = "✗ 失敗: " + res.error;
            return;
          }
          if (res.status === "done" && res.result) renderWhatIf(res.result);
        } catch (e) {
          document.getElementById("btn-whatif").disabled = false;
          document.getElementById("whatif-status").textContent = "✗ " + e;
        }
      }, 2500);
    }

    function renderWhatIf(result) {
      const sec = document.getElementById("whatif-section");
      sec.style.display = "block";
      whatifTrades = result.trades || [];
      const wf = result.summary || {};
      const act = computeStats(actualTrades);
      const days = Math.round((result.hours || hours) / 24);
      document.getElementById("whatif-title").textContent =
        "What-if シミュレーション結果（直近" + days + "日 / " + (result.n_bars || "—") + "バー）";
      document.getElementById("whatif-status").innerHTML =
        "完了: " + new Date().toLocaleTimeString("ja-JP") +
        " · 適用した設定は保存するまで本番に影響しません。";

      const rows = [
        ["実現損益 (USDT)", pnlHtml(act.pnl), pnlHtml(wf.total_pnl)],
        ["取引数", act.n, wf.n_trades != null ? wf.n_trades : "—"],
        ["勝率", act.winRate == null ? "—" : fmtNum(act.winRate, 0) + "%", wf.win_rate != null ? fmtNum(wf.win_rate * 100, 0) + "%" : "—"],
        ["プロフィットファクター", "—", wf.profit_factor != null ? fmtNum(wf.profit_factor, 2) : "—"],
        ["最大ドローダウン", "—", wf.max_drawdown != null ? fmtNum(wf.max_drawdown * 100, 2) + "%" : "—"],
      ];
      document.getElementById("whatif-compare").innerHTML =
        '<table class="cmp-table"><thead><tr><th></th><th>実績（同期間）</th><th>What-if</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return "<tr><td style='text-align:left'>" + r[0] + "</td><td>" + r[1] + "</td><td>" + r[2] + "</td></tr>";
        }).join("") + "</tbody></table>";

      document.getElementById("whatif-trades-body").innerHTML = tradesRowsHtml(whatifTrades);

      if (equityChart) {
        if (!whatifSeries) {
          whatifSeries = equityChart.addLineSeries({
            color: WHATIF, lineWidth: 2,
            priceFormat: { type: "price", precision: 2, minMove: 0.01 },
          });
        }
        whatifSeries.setData(dedupePoints(result.equity));
        document.getElementById("equity-legend").style.display = "flex";
        equityChart.timeScale().fitContent();
      }

      document.getElementById("marker-toggle").style.display = "flex";
      markerSource = "whatif";
      updateMarkerToggle();
      applyMarkers();
    }

    function updateMarkerToggle() {
      document.querySelectorAll("#marker-toggle button").forEach(function (b) {
        if (b.dataset.src === markerSource) b.classList.add("active-src", "primary");
        else b.classList.remove("active-src", "primary");
      });
    }

    // ---- メイン読み込み ----
    async function load() {
      const st = document.getElementById("status");
      try {
        const [state, kRes, tRes] = await Promise.all([
          fetch("/api/state").then(function (r) { return r.json(); }),
          fetch("/api/klines?hours=" + hours).then(function (r) { return r.json(); }),
          fetch("/api/trades?hours=" + hours).then(function (r) { return r.json(); }),
        ]);
        actualTrades = tRes.trades || [];
        document.getElementById("price-title").textContent =
          (kRes.symbol || "BTCUSDT") + " " + (kRes.interval || "15m") + " — 値動きと売買ポイント";
        renderTiles(state, actualTrades);
        renderChart(kRes.klines || []);
        renderEquity(tRes.equity || []);
        document.getElementById("trades-body").innerHTML = tradesRowsHtml(actualTrades);
        renderStatePanel(state);
        st.textContent = "最終更新: " + new Date().toLocaleTimeString("ja-JP") + " JST";
      } catch (e) {
        st.textContent = "読み込み失敗: " + e;
      }
    }

    document.getElementById("range-btns").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (!b) return;
      hours = parseInt(b.dataset.hours, 10);
      document.querySelectorAll("#range-btns button").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      load();
    });
    document.getElementById("marker-toggle").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (!b) return;
      markerSource = b.dataset.src;
      updateMarkerToggle();
      applyMarkers();
    });
    document.getElementById("btn-save").addEventListener("click", saveSettings);
    document.getElementById("btn-whatif").addEventListener("click", startWhatIf);

    if (!initCharts()) {
      document.getElementById("price-chart").innerHTML =
        '<p style="color:var(--muted);font-size:0.85rem">チャートライブラリ (unpkg.com) を読み込めませんでした。ネットワーク接続を確認してください。表とサマリは下部に表示されます。</p>';
    }
    load();
    loadSettings();
    setInterval(load, 60000);
  </script>
</body>
</html>
"""


def _utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _tail_jsonl(path: Path, max_lines: int = 200) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    size = path.stat().st_size
    chunk = min(8_000_000, size)
    with open(path, "rb") as f:
        f.seek(-chunk, 2)
        raw = f.read().decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    out: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _build_trades(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """イベントレコード列からエントリー/決済をペアリングして取引一覧を作る。

    records は paper_events.jsonl の行（bar_open_time / quote / events）と同形。
    what-if の結果 (whatif.run_what_if の records) も同じ形なので共用できる。
    決済イベントは旧形式 (type なし・price なし) にも対応する。
    """
    trades: list[dict[str, Any]] = []
    open_tr: dict[str, Any] | None = None
    for rec in records:
        bar_t = rec.get("bar_open_time")
        for e in rec.get("events", []):
            etype = e.get("type")
            if etype == "entry":
                open_tr = {
                    "entry_time": bar_t,
                    "side": e.get("side"),
                    "entry_px": e.get("price"),
                    "qty": e.get("qty"),
                    "tp": e.get("tp"),
                    "sl": e.get("sl"),
                }
            elif etype == "partial_exit":
                trades.append(
                    {
                        "entry_time": open_tr.get("entry_time") if open_tr else None,
                        "entry_px": open_tr.get("entry_px") if open_tr else None,
                        "side": e.get("side"),
                        "exit_time": bar_t,
                        "exit_px": e.get("price"),
                        "pnl": e.get("pnl"),
                        "reason": "partial_tp",
                        "partial": True,
                    }
                )
            elif etype == "exit" or (etype is None and "pnl" in e):
                entry_px = e.get("entry_px")
                if entry_px is None and open_tr:
                    entry_px = open_tr.get("entry_px")
                trades.append(
                    {
                        "entry_time": open_tr.get("entry_time") if open_tr else None,
                        "entry_px": entry_px,
                        "side": e.get("side"),
                        "exit_time": bar_t,
                        "exit_px": e.get("price"),
                        "pnl": e.get("pnl"),
                        "reason": e.get("reason"),
                        "partial": False,
                    }
                )
                open_tr = None
    return trades, open_tr


def _get_by_path(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _values_to_nested(values: dict[str, Any]) -> dict[str, Any]:
    """{"combine.entry_threshold": 0.1} → {"combine": {"entry_threshold": 0.1}}"""
    nested: dict[str, Any] = {}
    for dotted, v in values.items():
        cur = nested
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return nested


def _validate_values(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """編集可能リストに対して型・範囲を検証し、正規化した values を返す。"""
    if not isinstance(raw, dict):
        return None, "values must be an object"
    spec_by_path = {p["path"]: p for p in _EDITABLE_PARAMS}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        spec = spec_by_path.get(k)
        if spec is None:
            return None, f"editable でないキー: {k}"
        if spec["type"] == "choice":
            if v not in spec["choices"]:
                return None, f"{k}: {v} は {spec['choices']} のいずれかを指定"
            out[k] = v
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            return None, f"{k}: 数値ではありません"
        if num < spec["min"] or num > spec["max"]:
            return None, f"{k}: {spec['min']}〜{spec['max']} の範囲で指定"
        out[k] = int(round(num)) if spec["type"] == "int" else num
    # weight_model を変えたら weight_pattern も追従させる
    if "combine.weight_model" in out:
        out["combine.weight_pattern"] = round(1.0 - float(out["combine.weight_model"]), 4)
    return out, None


def create_app(config_path: Path | None = None) -> Flask:
    cfg = load_config(config_path)
    root = package_root()
    state_path = root / cfg.get("paper", {}).get("state_path", "data/paper_state.json")
    log_path = root / cfg.get("logging", {}).get("jsonl_path", "data/paper_events.jsonl")
    db_path = root / cfg.get("data", {}).get("cache_sqlite", "data/btc_klines.sqlite")
    local_cfg_path = root / "config.local.yaml"
    symbol = str(cfg.get("symbol", "BTCUSDT"))
    interval = str(cfg.get("intervals", {}).get("signal", "15m"))

    app = Flask(__name__)

    whatif_lock = threading.Lock()
    whatif_job: dict[str, Any] = {"status": "idle", "result": None, "error": None, "started_ms": 0}

    def _token_ok() -> bool:
        required = os.environ.get("DASHBOARD_TOKEN")
        if not required:
            return True
        return request.headers.get("X-Dashboard-Token", "") == required

    @app.get("/")
    def index() -> str:
        return _DASH_HTML

    @app.get("/api/state")
    def api_state() -> Response:
        if not state_path.exists():
            return jsonify({"error": "state file not found", "path": str(state_path)})
        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return jsonify({"error": str(e), "path": str(state_path)})
        return jsonify(data)

    @app.get("/api/klines")
    def api_klines() -> Response:
        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError:
            hours = 24
        hours = max(1, min(24 * 14, hours))
        since = _utc_ms() - hours * 3600 * 1000
        if not db_path.exists():
            return jsonify({"error": "kline db not found", "path": str(db_path), "klines": []})
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                cur = con.execute(
                    "SELECT open_time, open, high, low, close, volume FROM klines "
                    "WHERE symbol = ? AND interval = ? AND open_time >= ? ORDER BY open_time",
                    (symbol, interval, since),
                )
                rows = [
                    {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                    for r in cur.fetchall()
                ]
            finally:
                con.close()
        except sqlite3.Error as e:
            return jsonify({"error": str(e), "klines": []})
        return jsonify({"symbol": symbol, "interval": interval, "count": len(rows), "klines": rows})

    @app.get("/api/trades")
    def api_trades() -> Response:
        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError:
            hours = 24
        hours = max(1, min(24 * 14, hours))
        since = _utc_ms() - hours * 3600 * 1000
        # 15分足=毎時4本 + 余裕。決済だけが期間内に入る取引も拾えるよう広めに読む
        max_lines = min(16000, hours * 4 + 800)
        records = _tail_jsonl(log_path, max_lines=max_lines)
        trades, open_tr = _build_trades(records)
        visible = [
            t for t in trades
            if (t.get("exit_time") or t.get("entry_time") or 0) >= since
        ]
        if open_tr is not None:
            visible.append({**open_tr, "exit_time": None, "exit_px": None, "pnl": None,
                            "reason": None, "partial": False, "open": True})
        equity = [
            {"t": r["bar_open_time"], "q": r["quote"]}
            for r in records
            if r.get("bar_open_time") is not None and r.get("quote") is not None
            and r["bar_open_time"] >= since
        ]
        closed = [t for t in visible if not t.get("partial") and t.get("pnl") is not None]
        wins = sum(1 for t in closed if t["pnl"] > 0)
        summary = {
            "n_trades": len(closed),
            "n_wins": wins,
            "total_pnl": sum(t.get("pnl") or 0 for t in visible),
        }
        return jsonify({"hours": hours, "summary": summary, "trades": visible, "equity": equity})

    @app.get("/api/settings")
    def api_settings_get() -> Response:
        fresh = load_config(config_path)
        params = []
        for spec in _EDITABLE_PARAMS:
            p = dict(spec)
            p["value"] = _get_by_path(fresh, spec["path"])
            params.append(p)
        local_overrides: dict[str, Any] = {}
        if local_cfg_path.exists():
            try:
                with open(local_cfg_path, encoding="utf-8") as f:
                    local_overrides = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                local_overrides = {}
        return jsonify(
            {
                "params": params,
                "token_required": bool(os.environ.get("DASHBOARD_TOKEN")),
                "reload_seconds": fresh.get("paper", {}).get("reload_runtime_params_seconds", 300),
                "local_overrides": local_overrides,
                "local_path": str(local_cfg_path),
            }
        )

    @app.post("/api/settings")
    def api_settings_post() -> tuple[Response, int] | Response:
        if not _token_ok():
            return jsonify({"error": "invalid or missing X-Dashboard-Token"}), 401
        body = request.get_json(silent=True) or {}
        values, err = _validate_values(body.get("values"))
        if err:
            return jsonify({"error": err}), 400
        assert values is not None
        existing: dict[str, Any] = {}
        if local_cfg_path.exists():
            try:
                with open(local_cfg_path, encoding="utf-8") as f:
                    existing = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                existing = {}
        from ..config import _deep_merge

        merged = _deep_merge(existing, _values_to_nested(values))
        tmp = local_cfg_path.with_suffix(".yaml.tmp")
        header = (
            "# このファイルはダッシュボードの設定変更で自動更新されます。\n"
            "# config.yaml と runtime_params.json より優先されます。\n"
        )
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(header)
            yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, local_cfg_path)
        return jsonify({"ok": True, "written": str(local_cfg_path), "values": values})

    @app.post("/api/whatif")
    def api_whatif_start() -> tuple[Response, int] | Response:
        if not _token_ok():
            return jsonify({"error": "invalid or missing X-Dashboard-Token"}), 401
        body = request.get_json(silent=True) or {}
        values, err = _validate_values(body.get("values"))
        if err:
            return jsonify({"error": err}), 400
        assert values is not None
        try:
            hours = int(body.get("hours", 24))
        except (TypeError, ValueError):
            hours = 24
        hours = max(6, min(24 * 7, hours))

        with whatif_lock:
            if whatif_job["status"] == "running":
                return jsonify({"error": "already running"}), 409
            whatif_job.update({"status": "running", "result": None, "error": None, "started_ms": _utc_ms()})

        overrides = _values_to_nested(values)

        def _run() -> None:
            try:
                from ..backtest.whatif import run_what_if

                fresh = load_config(config_path)
                res = run_what_if(fresh, overrides, eval_bars=hours * 4)
                trades, open_tr = _build_trades(res["records"])
                if open_tr is not None:
                    trades.append({**open_tr, "exit_time": None, "exit_px": None, "pnl": None,
                                   "reason": None, "partial": False, "open": True})
                equity = [{"t": r["bar_open_time"], "q": r["quote"]} for r in res["records"]]
                result = {
                    "summary": res["summary"],
                    "trades": trades,
                    "equity": equity,
                    "hours": hours,
                    "n_bars": res["n_bars"],
                    "values": values,
                }
                with whatif_lock:
                    whatif_job.update({"status": "done", "result": result})
            except Exception as e:  # 学習データ不足なども画面に返す
                with whatif_lock:
                    whatif_job.update({"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"started": True, "hours": hours})

    @app.get("/api/whatif")
    def api_whatif_status() -> Response:
        with whatif_lock:
            out = {
                "status": whatif_job["status"],
                "error": whatif_job["error"],
                "result": whatif_job["result"],
            }
            if whatif_job["status"] == "running":
                out["elapsed_seconds"] = (_utc_ms() - whatif_job["started_ms"]) / 1000.0
        return jsonify(out)

    @app.get("/api/config-summary")
    def api_config_summary() -> Response:
        fresh = load_config(config_path)
        c = fresh.get("combine", {})
        f = fresh.get("filters", {})
        r = fresh.get("risk", {})
        return jsonify(
            {
                "symbol": fresh.get("symbol"),
                "entry_threshold": c.get("entry_threshold"),
                "min_confidence": f.get("min_confidence"),
                "max_daily_loss_pct": r.get("max_daily_loss_pct"),
                "cooldown_after_losses": r.get("cooldown_after_losses"),
                "cooldown_bars": r.get("cooldown_bars"),
            }
        )

    return app


def run_dashboard(host: str, port: int, config_path: Path | None = None) -> None:
    app = create_app(config_path)
    app.run(host=host, port=port, threaded=True, use_reloader=False)

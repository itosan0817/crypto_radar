from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from flask import Flask, Response, jsonify, request

from ..config import load_config, package_root
from ..data.binance_futures import bars_per_hour
from ..eval.trade_log import build_trades as _build_trades
from ..eval.trade_log import period_stats as _advice_stats
from ..eval.trade_log import tail_jsonl as _tail_jsonl
from ..settings_spec import EDITABLE_PARAMS as _EDITABLE_PARAMS
from ..settings_spec import get_by_path as _get_by_path
from ..settings_spec import validate_values as _validate_values
from ..settings_spec import values_to_nested as _values_to_nested
from ..settings_spec import write_local_overrides

# チャートで選択できる時間足
_ALLOWED_INTERVALS = ["1m", "15m", "1h", "4h", "1d", "1w"]

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
      --muted: #ffffff;
      --accent: #6cb6e8;
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
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
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
      background: rgba(108, 182, 232, 0.18);
      border-color: var(--accent);
      color: var(--text);
    }
    .btn.primary { background: rgba(108, 182, 232, 0.18); border-color: var(--accent); color: var(--text); }
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
    .pill.rank-s { background: rgba(62, 207, 142, 0.15); color: var(--pos); }
    .pill.rank-a { background: rgba(108, 182, 232, 0.18); color: var(--accent); }
    .pill.rank-b { background: rgba(139, 152, 168, 0.2); color: var(--muted); }
    #aero-body tr { cursor: pointer; }
    #aero-body tr.active td { background: rgba(108, 182, 232, 0.12); }
    .pager { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.75rem; font-size: 0.8rem; flex-wrap: wrap; }
    .pager .count { color: var(--muted); margin-left: auto; }
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
    .inp-cell { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
    .form-grid input, .form-grid select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 0.25rem 0.5rem;
      font-size: 0.85rem;
      width: 130px;
      font-variant-numeric: tabular-nums;
    }
    .adv-chip {
      background: rgba(189, 135, 51, 0.12);
      border: 1px solid var(--whatif);
      color: var(--whatif);
      border-radius: 999px;
      padding: 0.05rem 0.55rem;
      font-size: 0.72rem;
      cursor: pointer;
      white-space: nowrap;
    }
    .adv-chip:hover { background: rgba(189, 135, 51, 0.25); }
    .form-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.9rem; align-items: center; }
    .form-actions select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 0.2rem 0.4rem;
      font-size: 0.78rem;
    }
    #settings-status, #whatif-status { font-size: 0.78rem; color: var(--muted); }
    .ind-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.9rem;
      align-items: center;
      font-size: 0.78rem;
      color: var(--muted);
      margin-bottom: 0.6rem;
    }
    .ind-row label { cursor: pointer; user-select: none; }
    .ind-row .sw { display: inline-block; width: 12px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 0.25rem; }
    .ind-row select {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 0.15rem 0.4rem;
      font-size: 0.78rem;
    }
    #osc-chart { width: 100%; height: 150px; }
    @media (max-width: 640px) {
      #price-chart { height: 300px; }
      dl { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <h1 id="page-h1">BTC Paper Trader</h1>
  <p class="sub" id="page-sub">時刻は日本時間 (JST) · 実績表示は60秒ごとに自動更新</p>

  <div class="range-btns" id="app-toggle">
    <button data-app="btc" class="active">📈 BTC Paper Trader</button>
    <button data-app="aero">🛰️ Aerodrome Radar</button>
  </div>

  <div id="btc-view">
  <div class="toolbar">
    <div class="range-btns" id="range-btns">
      <button data-hours="24" class="active">1日</button>
      <button data-hours="72">3日</button>
      <button data-hours="168">7日</button>
    </div>
    <div class="range-btns" id="iv-btns">
      <button data-iv="1m">1分</button>
      <button data-iv="15m" class="active">15分</button>
      <button data-iv="1h">1時間</button>
      <button data-iv="4h">4時間</button>
      <button data-iv="1d">日足</button>
      <button data-iv="1w">週足</button>
    </div>
    <div class="range-btns" id="cur-btns">
      <button data-cur="USDT" class="active">USDT</button>
      <button data-cur="JPY">円</button>
    </div>
    <span id="status">読み込み中…</span>
    <a href="/api/state" target="_blank" rel="noopener">state</a>
  </div>

  <div class="tiles" id="tiles"></div>

  <section>
    <h2 id="price-title">値動き</h2>
    <div class="ind-row" id="ind-row">
      <label><span class="sw" style="background:#3d8fd1"></span><input type="checkbox" data-ind="sma20" /> SMA20</label>
      <label><span class="sw" style="background:#bd8733"></span><input type="checkbox" data-ind="ema9" /> EMA9</label>
      <label><span class="sw" style="background:#9d7bd8"></span><input type="checkbox" data-ind="ema21" /> EMA21</label>
      <label><span class="sw" style="background:#8b98a8"></span><input type="checkbox" data-ind="bb" /> BB(20,2σ)</label>
      <span>オシレーター:
        <select id="osc-select">
          <option value="none">なし</option>
          <option value="rsi">RSI(14)</option>
          <option value="macd">MACD(12,26,9)</option>
        </select>
      </span>
    </div>
    <div class="chart-wrap">
      <div id="price-chart"></div>
      <div id="chart-tooltip"></div>
    </div>
    <div id="osc-chart" style="display:none"></div>
    <p class="chart-note">
      <a href="/trades" target="_blank" rel="noopener">📋 取引履歴を別ウィンドウで開く →</a>
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
        <label style="font-size:0.78rem;color:var(--muted)">期間:
          <select id="whatif-hours">
            <option value="24">1日</option>
            <option value="72">3日</option>
            <option value="168">7日</option>
            <option value="336">14日</option>
            <option value="720">30日</option>
          </select>
        </label>
        <button class="btn warn" id="btn-whatif">この設定で過去をシミュレート</button>
        <button class="btn" id="btn-advice">Claudeに参考値を聞く</button>
        <button class="btn warn" id="btn-apply-advice" style="display:none">参考値をすべて反映</button>
        <button class="btn" id="btn-reset">元の値に戻す</button>
        <span id="settings-status"></span>
      </div>
      <p class="chart-note" id="advice-comment" style="display:none"></p>
      <p class="chart-note" id="claude-native-note" style="display:none;color:var(--whatif)">
        ⚠ 現在 entry_mode=claude_native です。What-if シミュレーションと Claude自動チューニングは
        回帰ベース戦略のみに対応しており、Claude判断モードの成績は事前検証できません
        （Claude自身の判断を安価に再現する手段が無いため）。実運用の結果で判断してください。
      </p>
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
            <th>損益</th>
            <th>理由</th>
          </tr>
        </thead>
        <tbody id="whatif-trades-body"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Claude 自動チューニング履歴</h2>
    <p class="chart-note">毎日の日次締め（09:00 JST頃）に、Claudeが候補を提案 → バックテストで検証 → 現行設定に勝った場合のみ自動適用します。資金リスク系（投入資金割合・日次最大損失率）は自動調整の対象外です。</p>
    <div class="scroll-x">
      <table>
        <thead>
          <tr>
            <th>日時 (JST)</th>
            <th>結果</th>
            <th>変更内容</th>
            <th>理由 / コメント</th>
            <th>検証成績</th>
          </tr>
        </thead>
        <tbody id="autotune-body"></tbody>
      </table>
    </div>
  </section>
  </div>

  <div id="aero-view" style="display:none">
    <div class="toolbar">
      <div class="range-btns" id="aero-rank-btns">
        <button data-rank="all" class="active">すべて</button>
        <button data-rank="S">S級</button>
        <button data-rank="A">A級</button>
        <button data-rank="B">B級</button>
      </div>
      <span id="aero-status-text">読み込み中…</span>
    </div>

    <div class="tiles" id="aero-tiles"></div>

    <section>
      <h2 id="aero-chart-title">BTC 参考チャート（直近7日）</h2>
      <p class="chart-note">対象トークン自体の価格取得は未実装のため、代わりに市場全体（BTC）の値動きを参考表示します。下の一覧から行をクリックすると、その検知時刻を挟んだ前後の値動きに切り替わります（🔻が検知時刻）。</p>
      <div class="range-btns" id="aero-iv-btns">
        <button data-iv="1m">1分足</button>
        <button data-iv="15m">15分足</button>
        <button data-iv="1h" class="active">1時間足</button>
        <button data-iv="4h">4時間足</button>
        <button data-iv="1d">日足</button>
        <button data-iv="1w">週足</button>
      </div>
      <div class="chart-wrap" style="position:relative;">
        <div id="aero-chart" style="width:100%;height:320px;"></div>
        <div id="aero-chart-empty" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;color:var(--muted);font-size:0.85rem;text-align:center;padding:0 1rem;">
          この足種・期間にはキャッシュされたデータがありません。他の足種を試すか、直近の表示に戻してください。
        </div>
      </div>
      <div class="form-actions">
        <button class="btn" id="aero-chart-reset">直近の表示に戻す</button>
      </div>
    </section>

    <section>
      <h2>タイムロック変更予約 — 検知一覧</h2>
      <p class="chart-note">通知（Discord）はS級/A級のみですが、B級も含め検知した全件をここに表示します。「検知の結論」はS級/A級のみ算出される深層分析の最終判断です。行をクリックすると上のチャートがその時刻に切り替わります。</p>
      <div class="scroll-x">
        <table>
          <thead>
            <tr>
              <th>検知日時 (JST)</th>
              <th>ランク</th>
              <th>スコア</th>
              <th>コントラクト</th>
              <th>検知の結論</th>
              <th>AI要約</th>
            </tr>
          </thead>
          <tbody id="aero-body"></tbody>
        </table>
      </div>
      <div class="pager">
        <button id="aero-btn-prev">« 前へ</button>
        <span id="aero-page-indicator">1 / 1</span>
        <button id="aero-btn-next">次へ »</button>
        <span class="count" id="aero-total-count"></span>
      </div>
    </section>
  </div>

  <script>
    const POS = "#3ecf8e", NEG = "#e06c75", MUTED = "#8b98a8", ACCENT = "#3d8fd1", WHATIF = "#bd8733";
    const JST_OFFSET = 9 * 3600;
    let hours = 24;
    let priceChart = null, candleSeries = null, equityChart = null, equitySeries = null, whatifSeries = null;
    let actualTrades = [], whatifTrades = [];
    let settingsSpec = [];
    let lastAdvice = null;
    let whatifTimer = null, adviceTimer = null;
    let fx = { cur: "USDT", rate: 1 };
    let fxRateJPY = null;
    let lastState = null, lastKlines = [], lastEquity = [], lastWhatif = null;

    const IV_MS = { "1m": 60000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000, "1w": 604800000 };
    const IV_LABEL = { "1m": "1分足", "15m": "15分足", "1h": "1時間足", "4h": "4時間足", "1d": "日足", "1w": "週足" };
    let ivSel = localStorage.getItem("dash_iv") || "15m";
    if (!IV_MS[ivSel]) ivSel = "15m";
    let aeroIv = localStorage.getItem("dash_aero_iv") || "1h";
    if (!IV_MS[aeroIv]) aeroIv = "1h";
    let lastIv = "15m";
    let ind = null;
    try { ind = JSON.parse(localStorage.getItem("dash_ind")); } catch (e) { ind = null; }
    ind = ind || {};
    ind = { sma20: !!ind.sma20, ema9: !!ind.ema9, ema21: !!ind.ema21, bb: !!ind.bb, osc: ind.osc || "none" };
    let convCandles = [];
    let overlaySeries = {};
    let chartBase = null;
    let oscChart = null, oscSeries = {};
    let syncingRange = false;
    const IND_C = { sma20: "#3d8fd1", ema9: "#bd8733", ema21: "#9d7bd8", bb: "rgba(139,152,168,0.75)" };

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
    function jpy() { return fx.cur === "JPY"; }
    function curLabel() { return jpy() ? "円" : "USDT"; }
    function fmtMoney(v, digits) {
      if (v == null || isNaN(v)) return "—";
      if (jpy()) return "¥" + Math.round(v * fx.rate).toLocaleString("ja-JP");
      return fmtNum(v, digits == null ? 2 : digits);
    }
    function pnlMoney(v) {
      if (v == null) return "—";
      const cls = v >= 0 ? "pos" : "neg";
      let s;
      if (jpy()) s = "¥" + Math.abs(Math.round(v * fx.rate)).toLocaleString("ja-JP");
      else s = fmtNum(Math.abs(v), 2);
      return '<span class="' + cls + '">' + (v >= 0 ? "+" : "−") + s + "</span>";
    }
    function fmtPrice(v) {
      if (v == null || isNaN(v)) return "—";
      if (jpy()) return "¥" + Math.round(v * fx.rate).toLocaleString("ja-JP");
      return fmtNum(v, 1);
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
      const base = chartBase = {
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
        const nd = jpy() ? 0 : 1;
        tooltip.innerHTML =
          '<div style="color:' + MUTED + '">' + fmtJst(ms) + " JST</div>" +
          "<div>始 " + fmtNum(d.open, nd) + "　高 " + fmtNum(d.high, nd) + "</div>" +
          "<div>安 " + fmtNum(d.low, nd) + "　終 " + fmtNum(d.close, nd) + "</div>";
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

    function applyPriceFormats() {
      const pf = jpy()
        ? { type: "price", precision: 0, minMove: 1 }
        : { type: "price", precision: 1, minMove: 0.1 };
      const ef = jpy()
        ? { type: "price", precision: 0, minMove: 1 }
        : { type: "price", precision: 2, minMove: 0.01 };
      if (candleSeries) candleSeries.applyOptions({ priceFormat: pf });
      if (equitySeries) equitySeries.applyOptions({ priceFormat: ef });
      if (whatifSeries) whatifSeries.applyOptions({ priceFormat: ef });
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
        : sideLabel(state.side) + ' <span class="s">建値 ' + fmtPrice(state.entry_px) + "</span>";
      const tiles = [
        { k: "評価額 (" + curLabel() + ")", v: fmtMoney(state.quote), s: "" },
        { k: rangeLabel + "の実現損益", v: pnlMoney(s.pnl), s: "取引 " + s.n + " 件" },
        { k: "勝率 (" + rangeLabel + ")", v: s.winRate == null ? "—" : fmtNum(s.winRate, 0) + "%", s: s.n ? s.wins + "勝" + (s.n - s.wins) + "敗" : "取引なし" },
        { k: "本日の損益", v: pnlMoney(state.daily_pnl), s: state.halt_new_entries ? "⚠ 新規停止中" : "" },
        { k: "現在ポジション", v: posLine, s: "" },
      ];
      document.getElementById("tiles").innerHTML = tiles.map(function (t) {
        return '<div class="tile"><div class="k">' + t.k + '</div><div class="v">' + t.v + '</div>' +
          (t.s ? '<div class="s">' + t.s + "</div>" : "") + "</div>";
      }).join("");
    }

    function renderChart(klines) {
      if (!candleSeries) return;
      const candles = klines.map(function (k) {
        return {
          time: toChartTime(k.t),
          open: k.o * fx.rate, high: k.h * fx.rate,
          low: k.l * fx.rate, close: k.c * fx.rate,
        };
      });
      convCandles = candles;
      candleSeries.setData(candles);
      updateIndicators();
      priceChart.timeScale().fitContent();
    }

    // ---- テクニカル指標 ----
    function closesOf(c) { return c.map(function (x) { return x.close; }); }
    function smaPoints(c, n) {
      const out = [];
      let sum = 0;
      for (let i = 0; i < c.length; i++) {
        sum += c[i].close;
        if (i >= n) sum -= c[i - n].close;
        if (i >= n - 1) out.push({ time: c[i].time, value: sum / n });
      }
      return out;
    }
    function emaPoints(c, n) {
      if (c.length < n) return [];
      const k = 2 / (n + 1);
      let seed = 0;
      for (let i = 0; i < n; i++) seed += c[i].close;
      let e = seed / n;
      const out = [{ time: c[n - 1].time, value: e }];
      for (let i = n; i < c.length; i++) {
        e = c[i].close * k + e * (1 - k);
        out.push({ time: c[i].time, value: e });
      }
      return out;
    }
    function bbPoints(c, n, mult) {
      const upper = [], lower = [];
      let sum = 0, sumSq = 0;
      for (let i = 0; i < c.length; i++) {
        const v = c[i].close;
        sum += v; sumSq += v * v;
        if (i >= n) {
          const old = c[i - n].close;
          sum -= old; sumSq -= old * old;
        }
        if (i >= n - 1) {
          const mean = sum / n;
          const sd = Math.sqrt(Math.max(0, sumSq / n - mean * mean));
          upper.push({ time: c[i].time, value: mean + mult * sd });
          lower.push({ time: c[i].time, value: mean - mult * sd });
        }
      }
      return { upper: upper, lower: lower };
    }
    function rsiPoints(c, n) {
      if (c.length <= n) return [];
      let gain = 0, loss = 0;
      for (let i = 1; i <= n; i++) {
        const d = c[i].close - c[i - 1].close;
        if (d >= 0) gain += d; else loss -= d;
      }
      let ag = gain / n, al = loss / n;
      const out = [{ time: c[n].time, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) }];
      for (let i = n + 1; i < c.length; i++) {
        const d = c[i].close - c[i - 1].close;
        ag = (ag * (n - 1) + Math.max(d, 0)) / n;
        al = (al * (n - 1) + Math.max(-d, 0)) / n;
        out.push({ time: c[i].time, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
      }
      return out;
    }
    function macdPoints(c, fast, slow, sig) {
      const ef = emaPoints(c, fast), es = emaPoints(c, slow);
      if (!es.length) return { macd: [], signal: [], hist: [] };
      const byTime = {};
      ef.forEach(function (p) { byTime[p.time] = p.value; });
      const macd = [];
      es.forEach(function (p) {
        if (byTime[p.time] != null) macd.push({ time: p.time, value: byTime[p.time] - p.value });
      });
      if (macd.length < sig) return { macd: macd, signal: [], hist: [] };
      const k = 2 / (sig + 1);
      let seed = 0;
      for (let i = 0; i < sig; i++) seed += macd[i].value;
      let e = seed / sig;
      const signal = [{ time: macd[sig - 1].time, value: e }];
      for (let i = sig; i < macd.length; i++) {
        e = macd[i].value * k + e * (1 - k);
        signal.push({ time: macd[i].time, value: e });
      }
      const sigByTime = {};
      signal.forEach(function (p) { sigByTime[p.time] = p.value; });
      const hist = [];
      macd.forEach(function (p) {
        if (sigByTime[p.time] == null) return;
        const h = p.value - sigByTime[p.time];
        hist.push({ time: p.time, value: h, color: h >= 0 ? "rgba(62,207,142,0.55)" : "rgba(224,108,117,0.55)" });
      });
      return { macd: macd, signal: signal, hist: hist };
    }

    function overlayFor(name, color, width) {
      if (!overlaySeries[name]) {
        overlaySeries[name] = priceChart.addLineSeries({
          color: color, lineWidth: width || 2,
          priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
      }
      return overlaySeries[name];
    }

    function ensureOscChart() {
      if (oscChart || !window.LightweightCharts || !chartBase) return;
      oscChart = LightweightCharts.createChart(document.getElementById("osc-chart"), chartBase);
      oscSeries.rsi = oscChart.addLineSeries({
        color: ACCENT, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      oscSeries.rsi70 = oscSeries.rsi.createPriceLine({ price: 70, color: "rgba(139,152,168,0.5)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
      oscSeries.rsi30 = oscSeries.rsi.createPriceLine({ price: 30, color: "rgba(139,152,168,0.5)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
      oscSeries.hist = oscChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      oscSeries.macd = oscChart.addLineSeries({
        color: ACCENT, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      oscSeries.signal = oscChart.addLineSeries({
        color: "#bd8733", lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      // 価格チャートとズーム/スクロールを同期
      priceChart.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
        if (r && oscChart && !syncingRange) {
          syncingRange = true;
          oscChart.timeScale().setVisibleLogicalRange(r);
          syncingRange = false;
        }
      });
      oscChart.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
        if (r && priceChart && !syncingRange) {
          syncingRange = true;
          priceChart.timeScale().setVisibleLogicalRange(r);
          syncingRange = false;
        }
      });
    }

    function updateIndicators() {
      if (!priceChart) return;
      const c = convCandles;
      overlayFor("sma20", IND_C.sma20, 2).setData(ind.sma20 ? smaPoints(c, 20) : []);
      overlayFor("ema9", IND_C.ema9, 2).setData(ind.ema9 ? emaPoints(c, 9) : []);
      overlayFor("ema21", IND_C.ema21, 2).setData(ind.ema21 ? emaPoints(c, 21) : []);
      const bb = ind.bb ? bbPoints(c, 20, 2) : { upper: [], lower: [] };
      overlayFor("bbU", IND_C.bb, 1).setData(bb.upper);
      overlayFor("bbL", IND_C.bb, 1).setData(bb.lower);

      const osc = ind.osc || "none";
      const el = document.getElementById("osc-chart");
      el.style.display = osc === "none" ? "none" : "block";
      if (osc === "none") return;
      ensureOscChart();
      if (!oscChart) return;
      if (osc === "rsi") {
        oscSeries.rsi.setData(rsiPoints(c, 14));
        oscSeries.macd.setData([]); oscSeries.signal.setData([]); oscSeries.hist.setData([]);
      } else {
        const m = macdPoints(c, 12, 26, 9);
        oscSeries.rsi.setData([]);
        oscSeries.hist.setData(m.hist);
        oscSeries.macd.setData(m.macd);
        oscSeries.signal.setData(m.signal);
      }
      const r = priceChart.timeScale().getVisibleLogicalRange();
      if (r) oscChart.timeScale().setVisibleLogicalRange(r);
    }

    function dedupePoints(equity) {
      const seen = {};
      const pts = [];
      (equity || []).forEach(function (p) {
        const tt = toChartTime(p.t);
        if (!seen[tt]) { seen[tt] = 1; pts.push({ time: tt, value: p.q * fx.rate }); }
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
          "<td>" + fmtPrice(t.entry_px) + "</td>" +
          "<td>" + fmtPrice(t.exit_px) + "</td>" +
          "<td>" + pnlMoney(t.pnl) + "</td>" +
          "<td>" + (t.open ? "—" : reason) + "</td>" +
          "</tr>";
      }).join("");
    }

    function renderStatePanel(state) {
      const sdl = document.getElementById("state-dl");
      const rows = [
        ["評価額", fmtMoney(state.quote)],
        ["方向", sideLabel(state.side)],
        ["建値", state.side !== 0 ? fmtPrice(state.entry_px) : "—"],
        ["利確ライン (TP)", state.side !== 0 ? fmtPrice(state.tp) : "—"],
        ["損切りライン (SL)", state.side !== 0 ? fmtPrice(state.sl) : "—"],
        ["日次損益", pnlMoney(state.daily_pnl)],
        ["新規停止", state.halt_new_entries ? "はい" : "いいえ"],
      ];
      sdl.innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("");
    }

    function renderAll() {
      if (lastState) { renderTiles(lastState, actualTrades); renderStatePanel(lastState); }
      renderChart(lastKlines);
      renderEquity(lastEquity);
      if (lastWhatif) renderWhatIf(lastWhatif, true);
    }

    // ---- 通貨切替 ----
    async function setCurrency(cur) {
      if (cur === "JPY" && fxRateJPY == null) {
        try {
          const r = await fetch("/api/fxrate").then(function (x) { return x.json(); });
          if (!r.rate) throw new Error(r.error || "レート取得不可");
          fxRateJPY = r.rate;
        } catch (e) {
          document.getElementById("status").textContent = "為替レート取得失敗: " + e;
          return;
        }
      }
      fx = cur === "JPY" ? { cur: "JPY", rate: fxRateJPY } : { cur: "USDT", rate: 1 };
      localStorage.setItem("dash_cur", fx.cur);
      document.querySelectorAll("#cur-btns button").forEach(function (b) {
        b.classList.toggle("active", b.dataset.cur === fx.cur);
      });
      applyPriceFormats();
      renderAll();
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
        const chip = '<button class="adv-chip" data-path="' + p.path + '" style="display:none" type="button"></button>';
        const hint = p.help ? '<div class="hint">' + p.help + "</div>" : "";
        return "<label>" + p.label + '</label><div class="inp-cell">' + input + chip + "</div>" + hint;
      }).join("");
      document.getElementById("token-row").style.display = res.token_required ? "flex" : "none";
      if (res.token_required) {
        document.getElementById("token-input").value = localStorage.getItem("dash_token") || "";
      }
      document.getElementById("settings-note").textContent =
        "保存すると config.local.yaml に書き込まれ、稼働中のpaperループに最大" +
        Math.round((res.reload_seconds || 300) / 60) + "分で自動反映されます（再起動不要）。保存値は自動チューニングより優先されます。";
      const entryModeParam = settingsSpec.find(function (p) { return p.path === "entry_mode"; });
      document.getElementById("claude-native-note").style.display =
        entryModeParam && entryModeParam.value === "claude_native" ? "block" : "none";
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

    function rememberToken() {
      const tokenEl = document.getElementById("token-input");
      if (tokenEl && tokenEl.value) localStorage.setItem("dash_token", tokenEl.value);
    }

    async function saveSettings() {
      const st = document.getElementById("settings-status");
      const c = collectValues();
      if (c.error) { st.textContent = "✗ " + c.error; return; }
      rememberToken();
      st.textContent = "保存中…";
      try {
        const res = await fetch("/api/settings", {
          method: "POST",
          headers: tokenHeaders(),
          body: JSON.stringify({ values: c.values }),
        });
        const body = await res.json();
        if (!res.ok) { st.textContent = "✗ " + (body.error || res.status); return; }
        // 保存後は「元の値に戻す」の基準を保存値に更新する
        await loadSettings();
        if (lastAdvice) renderAdvice(lastAdvice);
        st.textContent = "✓ 保存しました（" + new Date().toLocaleTimeString("ja-JP") + "）";
      } catch (e) {
        st.textContent = "✗ " + e;
      }
    }

    // ---- Claude 参考値 ----
    async function startAdvice() {
      const st = document.getElementById("settings-status");
      rememberToken();
      try {
        const res = await fetch("/api/advice", { method: "POST", headers: tokenHeaders(), body: "{}" });
        const body = await res.json();
        if (!res.ok) { st.textContent = "✗ " + (body.error || res.status); return; }
        document.getElementById("btn-advice").disabled = true;
        st.textContent = "Claudeに問い合わせ中…";
        pollAdvice();
      } catch (e) {
        st.textContent = "✗ " + e;
      }
    }

    function pollAdvice() {
      if (adviceTimer) clearTimeout(adviceTimer);
      adviceTimer = setTimeout(async function () {
        const st = document.getElementById("settings-status");
        try {
          const res = await fetch("/api/advice").then(function (r) { return r.json(); });
          if (res.status === "running") {
            st.textContent = "Claudeに問い合わせ中…（" + Math.round(res.elapsed_seconds || 0) + "秒経過）";
            pollAdvice();
            return;
          }
          document.getElementById("btn-advice").disabled = false;
          if (res.status === "error") { st.textContent = "✗ 参考値の取得に失敗: " + res.error; return; }
          if (res.status === "done" && res.result) {
            st.textContent = "✓ 参考値を表示しました（クリックで入力欄へ反映）";
            renderAdvice(res.result);
          }
        } catch (e) {
          document.getElementById("btn-advice").disabled = false;
          st.textContent = "✗ " + e;
        }
      }, 2500);
    }

    function inputForPath(path) {
      for (let i = 0; i < settingsSpec.length; i++) {
        if (settingsSpec[i].path === path) {
          return document.querySelector('#settings-form [data-idx="' + i + '"]');
        }
      }
      return null;
    }

    function renderAdvice(result) {
      lastAdvice = result;
      const sugg = result.suggestions || {};
      document.querySelectorAll("#settings-form .adv-chip").forEach(function (chip) {
        const s = sugg[chip.dataset.path];
        if (!s) { chip.style.display = "none"; return; }
        chip.textContent = "参考 " + s.value;
        chip.title = s.reason || "";
        chip.style.display = "inline-block";
      });
      document.getElementById("btn-apply-advice").style.display =
        Object.keys(sugg).length ? "inline-block" : "none";
      const cm = document.getElementById("advice-comment");
      cm.style.display = "block";
      cm.textContent = "Claude (" + (result.model || "") + "): " + (result.comment || "") +
        " ※チップにマウスを乗せると各項目の理由が見えます";
    }

    function applyAdviceChip(chip) {
      if (!lastAdvice) return;
      const s = (lastAdvice.suggestions || {})[chip.dataset.path];
      const el = inputForPath(chip.dataset.path);
      if (s && el) el.value = s.value;
    }

    function applyAllAdvice() {
      if (!lastAdvice) return;
      const sugg = lastAdvice.suggestions || {};
      let n = 0;
      Object.keys(sugg).forEach(function (path) {
        const el = inputForPath(path);
        if (el) { el.value = sugg[path].value; n++; }
      });
      document.getElementById("settings-status").textContent =
        "✓ 参考値を " + n + " 項目に反映しました（保存するまで本番には影響しません）";
    }

    function resetValues() {
      settingsSpec.forEach(function (spec, i) {
        const el = document.querySelector('#settings-form [data-idx="' + i + '"]');
        if (el && spec.value != null) el.value = spec.value;
      });
      document.getElementById("settings-status").textContent = "✓ 現在の設定値に戻しました";
    }

    // ---- What-if ----
    async function startWhatIf() {
      const st = document.getElementById("settings-status");
      const c = collectValues();
      if (c.error) { st.textContent = "✗ " + c.error; return; }
      rememberToken();
      st.textContent = "";
      const whatifHours = parseInt(document.getElementById("whatif-hours").value, 10) || 24;
      try {
        const res = await fetch("/api/whatif", {
          method: "POST",
          headers: tokenHeaders(),
          body: JSON.stringify({ values: c.values, hours: whatifHours }),
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

    async function renderWhatIf(result, rerenderOnly) {
      const sec = document.getElementById("whatif-section");
      sec.style.display = "block";
      lastWhatif = result;
      whatifTrades = result.trades || [];
      const wf = result.summary || {};
      // 比較の「実績」はWhat-ifと同じ期間で集計する（表示中の期間と違うことがある）
      let actTrades = actualTrades;
      const whHours = result.hours || hours;
      if (whHours !== hours) {
        try {
          const tr = await fetch("/api/trades?hours=" + whHours).then(function (r) { return r.json(); });
          actTrades = tr.trades || [];
        } catch (e) { /* 取得失敗時は表示中期間の実績で代用 */ }
      }
      const act = computeStats(actTrades);
      const days = Math.round(whHours / 24);
      document.getElementById("whatif-title").textContent =
        "What-if シミュレーション結果（直近" + days + "日 / " + (result.n_bars || "—") + "バー）";
      if (!rerenderOnly) {
        document.getElementById("whatif-status").innerHTML =
          "完了: " + new Date().toLocaleTimeString("ja-JP") +
          " · 適用した設定は保存するまで本番に影響しません。";
      }

      const rows = [
        ["実現損益", pnlMoney(act.pnl), pnlMoney(wf.total_pnl)],
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
          applyPriceFormats();
        }
        whatifSeries.setData(dedupePoints(result.equity));
        document.getElementById("equity-legend").style.display = "flex";
        equityChart.timeScale().fitContent();
      }
    }

    // ---- 自動チューニング履歴 ----
    function fmtChanges(values, oldValues) {
      if (!values) return "—";
      return Object.keys(values).map(function (k) {
        const short = k.split(".").pop();
        if (k === "combine.weight_pattern") return null; // weight_model に連動する派生値は省略
        const oldV = oldValues && oldValues[k] != null ? oldValues[k] + "→" : "";
        return short + ": " + oldV + values[k];
      }).filter(Boolean).join(", ") || "—";
    }

    async function loadAutotune() {
      try {
        const res = await fetch("/api/autotune-history?limit=20").then(function (r) { return r.json(); });
        const rows = res.history || [];
        const tbody = document.getElementById("autotune-body");
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted)">まだ実行履歴がありません（毎日 09:00 JST 頃に自動実行されます）</td></tr>';
          return;
        }
        tbody.innerHTML = rows.map(function (r) {
          let result, change, reason, perf = "—";
          if (r.type === "rollback") {
            result = '<span class="pill short">ロールバック</span>';
            change = fmtChanges(r.reverted_to);
            reason = "適用後の実現PnL " + (r.live_pnl_since_change != null ? r.live_pnl_since_change : "—") + " USDT が閾値を下回ったため復元";
          } else {
            result = r.applied ? '<span class="pill long">適用</span>'
              : (r.winner ? '<span class="pill flat">dry-run</span>' : '<span class="pill flat">現状維持</span>');
            change = r.values ? fmtChanges(r.values, r.old_values) : "—";
            let winnerRat = "";
            if (r.winner && r.candidates) {
              const w = r.candidates.filter(function (c) { return c.name === r.winner; })[0];
              if (w) winnerRat = "「" + r.winner + "」" + (w.rationale || "");
            }
            reason = winnerRat || r.comment || r.reason || "—";
            if (r.baseline) {
              perf = "現行 " + Number(r.baseline.total_pnl).toFixed(1) + " (" + r.baseline.n_trades + "件)";
              if (r.winner && r.candidates) {
                const w = r.candidates.filter(function (c) { return c.name === r.winner; })[0];
                if (w && w.summary) perf += " → 採用 " + Number(w.summary.total_pnl).toFixed(1) + " (" + w.summary.n_trades + "件)";
              }
            }
          }
          return "<tr><td class='mono'>" + fmtJst(r.t) + "</td><td>" + result + "</td>" +
            "<td class='mono'>" + change + "</td><td>" + reason + "</td>" +
            "<td class='mono'>" + perf + "</td></tr>";
        }).join("");
      } catch (e) { /* 履歴が無くても他の表示は継続 */ }
    }

    // ---- メイン読み込み ----
    async function load() {
      const st = document.getElementById("status");
      try {
        const [state, kRes, tRes] = await Promise.all([
          fetch("/api/state").then(function (r) { return r.json(); }),
          fetch("/api/klines?hours=" + hours + "&interval=" + ivSel).then(function (r) { return r.json(); }),
          fetch("/api/trades?hours=" + hours).then(function (r) { return r.json(); }),
        ]);
        actualTrades = tRes.trades || [];
        lastState = state;
        lastKlines = kRes.klines || [];
        lastEquity = tRes.equity || [];
        lastIv = kRes.interval || ivSel;
        document.getElementById("price-title").textContent =
          (kRes.symbol || "BTCUSDT") + " " + (IV_LABEL[lastIv] || lastIv);
        renderTiles(state, actualTrades);
        renderChart(lastKlines);
        renderEquity(lastEquity);
        renderStatePanel(state);
        loadAutotune();
        st.textContent = "最終更新: " + new Date().toLocaleTimeString("ja-JP") + " JST" +
          (jpy() ? "（1 USD = ¥" + fmtNum(fx.rate, 2) + "）" : "");
      } catch (e) {
        st.textContent = "読み込み失敗: " + e;
      }
    }

    async function reloadKlines() {
      try {
        const kRes = await fetch("/api/klines?hours=" + hours + "&interval=" + ivSel)
          .then(function (r) { return r.json(); });
        lastKlines = kRes.klines || [];
        lastIv = kRes.interval || ivSel;
        document.getElementById("price-title").textContent =
          (kRes.symbol || "BTCUSDT") + " " + (IV_LABEL[lastIv] || lastIv);
        renderChart(lastKlines);
      } catch (e) {
        document.getElementById("status").textContent = "チャート読み込み失敗: " + e;
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
    document.getElementById("iv-btns").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (!b) return;
      ivSel = b.dataset.iv;
      localStorage.setItem("dash_iv", ivSel);
      document.querySelectorAll("#iv-btns button").forEach(function (x) {
        x.classList.toggle("active", x.dataset.iv === ivSel);
      });
      reloadKlines();
    });
    document.getElementById("ind-row").addEventListener("change", function (ev) {
      const cb = ev.target.closest("input[data-ind]");
      if (cb) ind[cb.dataset.ind] = cb.checked;
      const sel = ev.target.closest("#osc-select");
      if (sel) ind.osc = sel.value;
      localStorage.setItem("dash_ind", JSON.stringify(ind));
      updateIndicators();
    });
    document.getElementById("cur-btns").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (b) setCurrency(b.dataset.cur);
    });
    document.getElementById("settings-form").addEventListener("click", function (ev) {
      const chip = ev.target.closest(".adv-chip");
      if (chip) applyAdviceChip(chip);
    });
    document.getElementById("btn-save").addEventListener("click", saveSettings);
    document.getElementById("btn-whatif").addEventListener("click", startWhatIf);
    document.getElementById("btn-advice").addEventListener("click", startAdvice);
    document.getElementById("btn-apply-advice").addEventListener("click", applyAllAdvice);
    document.getElementById("btn-reset").addEventListener("click", resetValues);

    if (!initCharts()) {
      document.getElementById("price-chart").innerHTML =
        '<p style="color:var(--muted);font-size:0.85rem">チャートライブラリ (unpkg.com) を読み込めませんでした。ネットワーク接続を確認してください。表とサマリは下部に表示されます。</p>';
    }
    // 保存済みのチャート設定を復元
    document.querySelectorAll("#iv-btns button").forEach(function (x) {
      x.classList.toggle("active", x.dataset.iv === ivSel);
    });
    document.querySelectorAll("#aero-iv-btns button").forEach(function (x) {
      x.classList.toggle("active", x.dataset.iv === aeroIv);
    });
    document.querySelectorAll("#ind-row input[data-ind]").forEach(function (cb) {
      cb.checked = !!ind[cb.dataset.ind];
    });
    document.getElementById("osc-select").value = ind.osc || "none";
    load().then(function () {
      if (localStorage.getItem("dash_cur") === "JPY") setCurrency("JPY");
    });
    loadSettings();
    setInterval(load, 60000);

    // ---- Aerodrome Radar（タイムロック監視の検知一覧） ----
    let aeroState = { rank: "all", page: 1, pageSize: 20 };
    let aeroLoaded = false;

    function fmtJstDate(iso) {
      if (!iso) return "—";
      try {
        const d = new Date(iso);
        return new Date(d.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 16).replace("T", " ");
      } catch (e) { return "—"; }
    }
    function rankPill(rank) {
      const cls = rank === "S" ? "rank-s" : rank === "A" ? "rank-a" : "rank-b";
      return '<span class="pill ' + cls + '">' + (rank || "?") + "級</span>";
    }
    const DECISION_JA = {
      BUY: '<span class="pos">💎 買い判断</span>',
      SELL: '<span class="neg">⚠️ 売り逃げ</span>',
      DANGER: '<span class="neg">🚨 即撤退</span>',
      WAIT: '<span class="mono" style="color:var(--muted)">⏳ 静観</span>',
    };
    function decisionHtml(d) {
      return DECISION_JA[d] || "—";
    }

    function renderAeroTiles(summary) {
      const tiles = ["total", "S", "A", "B"].map(function (k) {
        const label = k === "total" ? "検知件数（全体）" : k + "級";
        return '<div class="tile"><div class="k">' + label + '</div><div class="v">' + (summary[k] || 0) + "件</div></div>";
      }).join("");
      document.getElementById("aero-tiles").innerHTML = tiles;
    }

    function renderAeroTable(events) {
      const tbody = document.getElementById("aero-body");
      if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted)">該当する記録はありません</td></tr>';
        return;
      }
      tbody.innerHTML = events.map(function (e) {
        const addr = e.contract_address || "";
        const short = addr ? (addr.slice(0, 6) + "…" + addr.slice(-4)) : "—";
        const link = e.event_id
          ? '<a href="https://basescan.org/tx/' + e.event_id + '" target="_blank" rel="noopener" class="mono">' + short + "</a>"
          : short;
        return '<tr data-t0="' + (e.t0_timestamp || "") + '">' +
          '<td class="mono">' + fmtJstDate(e.t0_timestamp) + "</td>" +
          "<td>" + rankPill(e.ai_rank) + "</td>" +
          "<td>" + (e.ai_score != null ? e.ai_score : "—") + "</td>" +
          "<td>" + link + "</td>" +
          "<td>" + decisionHtml(e.final_decision) + "</td>" +
          '<td style="text-align:left;max-width:360px;white-space:normal;font-size:0.75rem;color:var(--muted)">' + (e.ai_summary || "—") + "</td>" +
          "</tr>";
      }).join("");
    }

    // ---- Aerodrome用チャート（BTC参考表示） ----
    let aeroChart = null, aeroCandleSeries = null;
    let aeroCurrentEventT0 = null; // null = 直近表示、値あり = その検知時刻を中心に表示中
    const AERO_DEFAULT_HOURS = { "1m": 24, "15m": 72, "1h": 168, "4h": 720, "1d": 4320, "1w": 17520 };
    const AERO_EVENT_WINDOW = {
      "1m": { before: 6, after: 24 },
      "15m": { before: 12, after: 48 },
      "1h": { before: 24, after: 72 },
      "4h": { before: 96, after: 288 },
      "1d": { before: 336, after: 720 },
      "1w": { before: 1440, after: 4320 },
    };

    function ensureAeroChart() {
      if (aeroChart || !window.LightweightCharts || !chartBase) return;
      aeroChart = LightweightCharts.createChart(document.getElementById("aero-chart"), chartBase);
      aeroCandleSeries = aeroChart.addCandlestickSeries({
        upColor: POS, downColor: NEG,
        borderUpColor: POS, borderDownColor: NEG,
        wickUpColor: POS, wickDownColor: NEG,
      });
    }

    function renderAeroChart(klines, centerMs) {
      if (!aeroCandleSeries) return;
      const candles = klines.map(function (k) {
        return { time: toChartTime(k.t), open: k.o, high: k.h, low: k.l, close: k.c };
      });
      document.getElementById("aero-chart-empty").style.display = candles.length ? "none" : "flex";
      aeroCandleSeries.setData(candles);
      if (centerMs != null && candles.length) {
        const targetTime = toChartTime(centerMs);
        let nearest = candles[0].time, diff = Math.abs(candles[0].time - targetTime);
        candles.forEach(function (c) {
          const d = Math.abs(c.time - targetTime);
          if (d < diff) { diff = d; nearest = c.time; }
        });
        aeroCandleSeries.setMarkers([{
          time: nearest, position: "aboveBar", color: WHATIF, shape: "arrowDown", text: "検知",
        }]);
      } else {
        aeroCandleSeries.setMarkers([]);
      }
      aeroChart.timeScale().fitContent();
    }

    async function loadAeroChartDefault() {
      ensureAeroChart();
      aeroCurrentEventT0 = null;
      document.getElementById("aero-chart-title").textContent =
        "BTC 参考チャート（" + IV_LABEL[aeroIv] + "・直近" + AERO_DEFAULT_HOURS[aeroIv] + "時間）";
      document.querySelectorAll("#aero-body tr").forEach(function (r) { r.classList.remove("active"); });
      try {
        const params = new URLSearchParams({ interval: aeroIv, hours: AERO_DEFAULT_HOURS[aeroIv] });
        const res = await fetch("/api/klines?" + params.toString()).then(function (r) { return r.json(); });
        renderAeroChart(res.klines || [], null);
      } catch (e) { /* チャート取得失敗はサイレントに無視（一覧は引き続き使える） */ }
    }

    async function loadAeroChartForEvent(t0Iso, rowEl) {
      ensureAeroChart();
      aeroCurrentEventT0 = t0Iso;
      document.querySelectorAll("#aero-body tr").forEach(function (r) { r.classList.remove("active"); });
      const row = rowEl || document.querySelector('#aero-body tr[data-t0="' + t0Iso + '"]');
      if (row) row.classList.add("active");
      document.getElementById("aero-chart-title").textContent =
        "BTC 参考チャート（" + IV_LABEL[aeroIv] + "・検知: " + fmtJstDate(t0Iso) + " JST 前後）";
      try {
        const w = AERO_EVENT_WINDOW[aeroIv] || AERO_EVENT_WINDOW["1h"];
        const params = new URLSearchParams({
          center: t0Iso, interval: aeroIv, before_hours: w.before, after_hours: w.after,
        });
        const res = await fetch("/api/aerodrome/price-chart?" + params.toString()).then(function (r) { return r.json(); });
        renderAeroChart(res.klines || [], res.center_ms);
      } catch (e) { /* noop */ }
    }

    document.getElementById("aero-body").addEventListener("click", function (ev) {
      const row = ev.target.closest("tr[data-t0]");
      if (!row || !row.dataset.t0) return;
      loadAeroChartForEvent(row.dataset.t0, row);
    });
    document.getElementById("aero-chart-reset").addEventListener("click", loadAeroChartDefault);
    document.getElementById("aero-iv-btns").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (!b) return;
      aeroIv = b.dataset.iv;
      localStorage.setItem("dash_aero_iv", aeroIv);
      document.querySelectorAll("#aero-iv-btns button").forEach(function (x) {
        x.classList.toggle("active", x.dataset.iv === aeroIv);
      });
      if (aeroCurrentEventT0) {
        loadAeroChartForEvent(aeroCurrentEventT0);
      } else {
        loadAeroChartDefault();
      }
    });

    async function loadAero() {
      const st = document.getElementById("aero-status-text");
      st.textContent = "読み込み中…";
      const params = new URLSearchParams({
        rank: aeroState.rank,
        page: aeroState.page, page_size: aeroState.pageSize,
      });
      try {
        const res = await fetch("/api/aerodrome/events?" + params.toString()).then(function (r) { return r.json(); });
        if (res.error) { st.textContent = "✗ " + res.error; return; }
        renderAeroTiles(res.summary || {});
        renderAeroTable(res.events || []);
        aeroState.page = res.page;
        const totalPages = Math.max(1, res.total_pages || 1);
        document.getElementById("aero-page-indicator").textContent = res.page + " / " + totalPages;
        document.getElementById("aero-total-count").textContent = "全 " + res.total + " 件";
        document.getElementById("aero-btn-prev").disabled = res.page <= 1;
        document.getElementById("aero-btn-next").disabled = res.page >= totalPages;
        st.textContent = "最終更新: " + new Date().toLocaleTimeString("ja-JP") + " JST";
      } catch (e) {
        st.textContent = "読み込み失敗: " + e;
      }
    }

    document.getElementById("aero-rank-btns").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (!b) return;
      aeroState.rank = b.dataset.rank;
      aeroState.page = 1;
      document.querySelectorAll("#aero-rank-btns button").forEach(function (x) { x.classList.toggle("active", x === b); });
      loadAero();
    });
    document.getElementById("aero-btn-prev").addEventListener("click", function () {
      if (aeroState.page > 1) { aeroState.page -= 1; loadAero(); }
    });
    document.getElementById("aero-btn-next").addEventListener("click", function () {
      aeroState.page += 1;
      loadAero();
    });

    function switchApp(app) {
      localStorage.setItem("dash_app", app);
      document.querySelectorAll("#app-toggle button").forEach(function (x) {
        x.classList.toggle("active", x.dataset.app === app);
      });
      document.getElementById("btc-view").style.display = app === "btc" ? "" : "none";
      document.getElementById("aero-view").style.display = app === "aero" ? "" : "none";
      document.getElementById("page-h1").textContent = app === "btc" ? "BTC Paper Trader" : "Aerodrome Radar";
      document.getElementById("page-sub").textContent = app === "btc"
        ? "時刻は日本時間 (JST) · 実績表示は60秒ごとに自動更新"
        : "時刻は日本時間 (JST) · タイムロック変更予約の検知一覧（Discord通知はS級/A級のみ）";
      if (app === "aero" && !aeroLoaded) {
        aeroLoaded = true;
        loadAero();
        loadAeroChartDefault();
      }
    }
    document.getElementById("app-toggle").addEventListener("click", function (ev) {
      const b = ev.target.closest("button");
      if (b) switchApp(b.dataset.app);
    });
    const savedApp = localStorage.getItem("dash_app");
    if (savedApp === "aero") switchApp("aero");
  </script>
</body>
</html>
"""

_TRADES_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BTC Paper Trader — 取引履歴</title>
  <style>
    :root {
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e7ecf3;
      --muted: #8b98a8;
      --accent: #6cb6e8;
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
    h1 { font-size: 1.15rem; font-weight: 600; margin: 0 0 0.25rem; }
    .sub { color: var(--muted); font-size: 0.82rem; margin-bottom: 1rem; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .sub a { color: var(--accent); text-decoration: none; }
    .sub a:hover { text-decoration: underline; }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.9rem 1rem;
      margin-top: 0.9rem;
    }
    .filter-row { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; margin-bottom: 0.6rem; }
    .filter-row:last-child { margin-bottom: 0; }
    .filter-label { font-size: 0.75rem; color: var(--muted); margin-right: 0.15rem; }
    button, select, input {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 6px;
      padding: 0.3rem 0.6rem;
      font-size: 0.8rem;
      cursor: pointer;
      font-family: inherit;
    }
    select, input[type="date"] { color: var(--text); cursor: default; background: var(--bg); }
    button.active {
      background: rgba(108, 182, 232, 0.18);
      border-color: var(--accent);
      color: var(--text);
    }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    #custom-range { display: none; gap: 0.4rem; align-items: center; }
    #status { font-size: 0.78rem; color: var(--muted); margin-left: auto; }
    .scroll-x { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
    th, td {
      text-align: right;
      padding: 0.45rem 0.5rem;
      border-bottom: 1px solid var(--border);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
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
    .pos { color: var(--pos); }
    .neg { color: var(--neg); }
    .pager { display: flex; align-items: center; gap: 0.6rem; margin-top: 0.75rem; font-size: 0.8rem; flex-wrap: wrap; }
    .pager .count { color: var(--muted); margin-left: auto; }
    @media (max-width: 640px) {
      .filter-row { gap: 0.3rem; }
      button, select, input { font-size: 0.78rem; padding: 0.28rem 0.5rem; }
      #status { margin-left: 0; width: 100%; }
      .pager .count { margin-left: 0; width: 100%; order: 3; }
    }
  </style>
</head>
<body>
  <h1>取引履歴</h1>
  <p class="sub"><a href="/">← ダッシュボードへ戻る</a> · 時刻は日本時間 (JST)</p>

  <section>
    <div class="filter-row" id="range-row">
      <span class="filter-label">期間:</span>
      <button data-range="today">今日</button>
      <button data-range="7d">7日</button>
      <button data-range="30d" class="active">30日</button>
      <button data-range="90d">90日</button>
      <button data-range="180d">180日</button>
      <button data-range="all">全期間</button>
      <button data-range="custom">カスタム</button>
      <span id="custom-range">
        <input type="date" id="f-from" />〜<input type="date" id="f-to" />
        <button id="btn-apply-range">適用</button>
      </span>
    </div>
    <div class="filter-row">
      <span class="filter-label">方向:</span>
      <select id="f-side">
        <option value="all">すべて</option>
        <option value="long">ロング</option>
        <option value="short">ショート</option>
      </select>
      <span class="filter-label" style="margin-left:0.5rem">決済理由:</span>
      <select id="f-reason">
        <option value="all">すべて</option>
        <option value="tp">利確</option>
        <option value="sl">損切り</option>
        <option value="time">時間切れ</option>
        <option value="partial_tp">部分利確</option>
        <option value="open">保有中</option>
      </select>
      <span class="filter-label" style="margin-left:0.5rem">表示件数:</span>
      <select id="f-pagesize">
        <option value="20">20</option>
        <option value="50" selected>50</option>
        <option value="100">100</option>
      </select>
      <span id="status">読み込み中…</span>
    </div>
  </section>

  <section>
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
    <div class="pager">
      <button id="btn-prev">« 前へ</button>
      <span id="page-indicator">1 / 1</span>
      <button id="btn-next">次へ »</button>
      <span class="count" id="total-count"></span>
    </div>
  </section>

  <script>
    const REASON_JA = { sl: "損切り", tp: "利確", time: "時間切れ", partial_tp: "部分利確" };
    let state = { range: "today", side: "all", reason: "all", pageSize: 50, page: 1 };
    try {
      const saved = JSON.parse(localStorage.getItem("trades_filters"));
      if (saved) state = Object.assign(state, saved, { page: 1 });
    } catch (e) { /* ignore */ }

    function fmtJst(ms) {
      if (ms == null) return "—";
      const d = new Date(ms + 9 * 3600 * 1000);
      return d.toISOString().slice(5, 16).replace("T", " ");
    }
    function fmtNum(v, digits) {
      if (v == null || isNaN(v)) return "—";
      return Number(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }
    function pnlHtml(v) {
      if (v == null) return "—";
      const cls = v >= 0 ? "pos" : "neg";
      return '<span class="' + cls + '">' + (v >= 0 ? "+" : "") + fmtNum(v, 2) + "</span>";
    }
    function sideLabel(s) {
      if (s === 1) return '<span class="pill long">LONG</span>';
      if (s === -1) return '<span class="pill short">SHORT</span>';
      return '<span class="pill flat">FLAT</span>';
    }
    function todayJst() {
      const d = new Date(Date.now() + 9 * 3600 * 1000);
      return d.toISOString().slice(0, 10);
    }

    function saveFilters() {
      localStorage.setItem("trades_filters", JSON.stringify({
        range: state.range, side: state.side, reason: state.reason, pageSize: state.pageSize,
        from: state.from, to: state.to,
      }));
    }

    async function load() {
      const st = document.getElementById("status");
      st.textContent = "読み込み中…";
      const params = new URLSearchParams({
        range: state.range, side: state.side, reason: state.reason,
        page: state.page, page_size: state.pageSize,
      });
      if (state.range === "custom") {
        if (state.from) params.set("from", state.from);
        if (state.to) params.set("to", state.to);
      }
      try {
        const res = await fetch("/api/trades/search?" + params.toString()).then(function (r) { return r.json(); });
        if (res.error) { st.textContent = "✗ " + res.error; return; }
        renderTable(res.trades || []);
        state.page = res.page;
        const totalPages = Math.max(1, res.total_pages || 1);
        document.getElementById("page-indicator").textContent = res.page + " / " + totalPages;
        document.getElementById("total-count").textContent = "全 " + res.total + " 件";
        document.getElementById("btn-prev").disabled = res.page <= 1;
        document.getElementById("btn-next").disabled = res.page >= totalPages;
        st.textContent = "最終更新: " + new Date().toLocaleTimeString("ja-JP") + " JST";
      } catch (e) {
        st.textContent = "読み込み失敗: " + e;
      }
    }

    function renderTable(trades) {
      const tbody = document.getElementById("trades-body");
      if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted)">該当する取引はありません</td></tr>';
        return;
      }
      tbody.innerHTML = trades.map(function (t) {
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

    document.getElementById("range-row").addEventListener("click", function (ev) {
      const b = ev.target.closest("button[data-range]");
      if (!b) return;
      state.range = b.dataset.range;
      state.page = 1;
      document.querySelectorAll("#range-row button[data-range]").forEach(function (x) {
        x.classList.toggle("active", x.dataset.range === state.range);
      });
      const custom = document.getElementById("custom-range");
      if (state.range === "custom") {
        custom.style.display = "inline-flex";
        if (!state.from) state.from = todayJst();
        if (!state.to) state.to = todayJst();
        document.getElementById("f-from").value = state.from;
        document.getElementById("f-to").value = state.to;
      } else {
        custom.style.display = "none";
        saveFilters();
        load();
      }
    });
    document.getElementById("btn-apply-range").addEventListener("click", function () {
      state.from = document.getElementById("f-from").value || todayJst();
      state.to = document.getElementById("f-to").value || todayJst();
      state.page = 1;
      saveFilters();
      load();
    });
    document.getElementById("f-side").addEventListener("change", function (ev) {
      state.side = ev.target.value; state.page = 1; saveFilters(); load();
    });
    document.getElementById("f-reason").addEventListener("change", function (ev) {
      state.reason = ev.target.value; state.page = 1; saveFilters(); load();
    });
    document.getElementById("f-pagesize").addEventListener("change", function (ev) {
      state.pageSize = parseInt(ev.target.value, 10) || 50; state.page = 1; saveFilters(); load();
    });
    document.getElementById("btn-prev").addEventListener("click", function () {
      if (state.page > 1) { state.page -= 1; load(); }
    });
    document.getElementById("btn-next").addEventListener("click", function () {
      state.page += 1; load();
    });

    document.querySelectorAll("#range-row button[data-range]").forEach(function (x) {
      x.classList.toggle("active", x.dataset.range === state.range);
    });
    document.getElementById("f-side").value = state.side;
    document.getElementById("f-reason").value = state.reason;
    document.getElementById("f-pagesize").value = String(state.pageSize);
    if (state.range === "custom") {
      document.getElementById("custom-range").style.display = "inline-flex";
      document.getElementById("f-from").value = state.from || todayJst();
      document.getElementById("f-to").value = state.to || todayJst();
    }
    load();
  </script>
</body>
</html>
"""


def _utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


_JST = timezone(timedelta(hours=9))


def _jst_date_to_ms(date_str: str, end_of_day: bool = False) -> int | None:
    """"YYYY-MM-DD"（JST基準の暦日）をUTCミリ秒に変換する。end_of_day=Trueなら翌日0時（排他的上限）。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_JST)
    except (ValueError, TypeError):
        return None
    if end_of_day:
        d += timedelta(days=1)
    return int(d.timestamp() * 1000)


def _resolve_trade_range(args: Any, now_ms: int) -> tuple[int, int, str | None]:
    """クエリパラメータから (from_ms, to_ms, error) を解決する。"""
    range_key = str(args.get("range", "30d"))
    if range_key == "today":
        today = datetime.now(_JST).strftime("%Y-%m-%d")
        return _jst_date_to_ms(today) or 0, now_ms, None
    if range_key == "all":
        return 0, now_ms, None
    if range_key == "custom":
        from_s = args.get("from")
        to_s = args.get("to")
        from_ms = _jst_date_to_ms(from_s) if from_s else 0
        to_ms = _jst_date_to_ms(to_s, end_of_day=True) if to_s else now_ms
        if from_ms is None or to_ms is None:
            return 0, now_ms, "from/to は YYYY-MM-DD 形式で指定してください"
        return from_ms, min(to_ms, now_ms + 1), None
    days_map = {"7d": 7, "30d": 30, "90d": 90, "180d": 180}
    days = days_map.get(range_key)
    if days is None:
        return 0, now_ms, f"unknown range: {range_key}"
    return now_ms - days * 86_400_000, now_ms, None


def create_app(config_path: Path | None = None) -> Flask:
    cfg = load_config(config_path)
    root = package_root()
    state_path = root / cfg.get("paper", {}).get("state_path", "data/paper_state.json")
    log_path = root / cfg.get("logging", {}).get("jsonl_path", "data/paper_events.jsonl")
    db_path = root / cfg.get("data", {}).get("cache_sqlite", "data/btc_klines.sqlite")
    local_cfg_path = root / "config.local.yaml"
    symbol = str(cfg.get("symbol", "BTCUSDT"))
    interval = str(cfg.get("intervals", {}).get("signal", "15m"))
    base_url = str(cfg.get("base_url", "https://fapi.binance.com"))

    app = Flask(__name__)

    whatif_lock = threading.Lock()
    whatif_job: dict[str, Any] = {"status": "idle", "result": None, "error": None, "started_ms": 0}
    advice_lock = threading.Lock()
    advice_job: dict[str, Any] = {"status": "idle", "result": None, "error": None, "started_ms": 0}
    fx_cache: dict[str, Any] = {"rate": None, "ms": 0}
    kline_mem: dict[str, dict[str, Any]] = {}  # Binance直接取得の短期キャッシュ（足ごと）

    def _token_ok() -> bool:
        required = os.environ.get("DASHBOARD_TOKEN")
        if not required:
            return True
        return request.headers.get("X-Dashboard-Token", "") == required

    def _current_params(fresh: dict[str, Any]) -> list[dict[str, Any]]:
        params = []
        for spec in _EDITABLE_PARAMS:
            p = dict(spec)
            p["value"] = _get_by_path(fresh, spec["path"])
            params.append(p)
        return params

    @app.get("/")
    def index() -> str:
        return _DASH_HTML

    @app.get("/trades")
    def trades_page() -> str:
        return _TRADES_HTML

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
        from ..data.binance_futures import INTERVAL_MS, fetch_klines_range

        iv = request.args.get("interval", interval)
        if iv not in _ALLOWED_INTERVALS:
            iv = interval
        step = INTERVAL_MS[iv]
        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError:
            hours = 24
        hours = max(1, min(24 * 3650, hours))
        now = _utc_ms()
        # 大きい足でもチャートが成立するよう最低60本、1分足の長期間は3000本で頭打ち
        bars_wanted = int(max(60, min(3000, hours * 3600 * 1000 // step + 2)))
        since = now - bars_wanted * step

        rows: list[dict[str, Any]] = []
        if db_path.exists():
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
                try:
                    cur = con.execute(
                        "SELECT open_time, open, high, low, close, volume FROM klines "
                        "WHERE symbol = ? AND interval = ? AND open_time >= ? ORDER BY open_time",
                        (symbol, iv, since),
                    )
                    rows = [
                        {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                        for r in cur.fetchall()
                    ]
                finally:
                    con.close()
            except sqlite3.Error:
                rows = []

        # キャッシュに無い/更新が止まっている足は Binance から直接補完する（DBには書かない）
        last_open = rows[-1]["t"] if rows else 0
        if now - last_open > 2 * step:
            mem = kline_mem.get(iv)
            if mem and now - mem["ms"] < 60_000 and mem["since"] <= since:
                fetched = mem["rows"]
            else:
                fetched = []
                try:
                    start = max(since, last_open + step) if rows else since
                    df = fetch_klines_range(base_url, symbol, iv, start, now)
                    fetched = [
                        {
                            "t": int(r.open_time), "o": float(r.open), "h": float(r.high),
                            "l": float(r.low), "c": float(r.close), "v": float(r.volume),
                        }
                        for r in df.itertuples()
                    ]
                    kline_mem[iv] = {"rows": fetched, "ms": now, "since": since}
                except Exception:
                    fetched = []
            if fetched:
                seen = {r["t"] for r in rows}
                rows.extend(r for r in fetched if r["t"] not in seen)
                rows.sort(key=lambda r: r["t"])
        rows = rows[-bars_wanted:]
        return jsonify(
            {"symbol": symbol, "interval": iv, "step_ms": step, "count": len(rows), "klines": rows}
        )

    @app.get("/api/trades")
    def api_trades() -> Response:
        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError:
            hours = 24
        hours = max(1, min(24 * 30, hours))
        since = _utc_ms() - hours * 3600 * 1000
        # 決済だけが期間内に入る取引も拾えるよう広めに読む
        max_lines = min(16000, int(hours * bars_per_hour(interval)) + 800)
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

    @app.get("/api/trades/search")
    def api_trades_search() -> Response:
        """取引履歴ページ用: 期間・方向・決済理由で絞り込み、ページネーションして返す。"""
        now_ms = _utc_ms()
        from_ms, to_ms, err = _resolve_trade_range(request.args, now_ms)
        if err:
            return jsonify({"error": err})

        side_f = str(request.args.get("side", "all"))
        reason_f = str(request.args.get("reason", "all"))
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            page_size = 50
        page_size = max(1, min(200, page_size))

        # 8MBチャンク上限までの全履歴（実運用の稼働期間なら十分カバーできる）から
        # エントリー/決済を組み直し、その後に期間・条件で絞り込む
        records = _tail_jsonl(log_path, max_lines=200_000)
        trades, open_tr = _build_trades(records)
        if open_tr is not None:
            trades.append({**open_tr, "exit_time": None, "exit_px": None, "pnl": None,
                           "reason": None, "partial": False, "open": True})

        def in_range(t: dict[str, Any]) -> bool:
            ts = t.get("exit_time") or t.get("entry_time") or 0
            return from_ms <= ts < to_ms

        filtered = [t for t in trades if in_range(t)]
        if side_f == "long":
            filtered = [t for t in filtered if t.get("side") == 1]
        elif side_f == "short":
            filtered = [t for t in filtered if t.get("side") == -1]
        if reason_f == "open":
            filtered = [t for t in filtered if t.get("open")]
        elif reason_f != "all":
            filtered = [t for t in filtered if not t.get("open") and t.get("reason") == reason_f]

        filtered.sort(key=lambda t: t.get("exit_time") or t.get("entry_time") or 0, reverse=True)
        total = len(filtered)
        total_pages = max(1, -(-total // page_size))  # ceil
        page = min(page, total_pages)
        start = (page - 1) * page_size
        page_rows = filtered[start : start + page_size]

        return jsonify({
            "trades": page_rows, "total": total, "page": page,
            "page_size": page_size, "total_pages": total_pages,
            "from_ms": from_ms, "to_ms": to_ms,
        })

    @app.get("/api/fxrate")
    def api_fxrate() -> Response:
        """USD/JPY レート（USDT≒USDとして表示換算に使う）。6時間キャッシュ。"""
        now = _utc_ms()
        if fx_cache["rate"] is not None and now - fx_cache["ms"] < 6 * 3600 * 1000:
            return jsonify({"rate": fx_cache["rate"], "cached": True, "source": "frankfurter.app"})
        try:
            r = requests.get(
                "https://api.frankfurter.app/latest",
                params={"from": "USD", "to": "JPY"},
                timeout=8,
            )
            r.raise_for_status()
            rate = float(r.json()["rates"]["JPY"])
            fx_cache.update(rate=rate, ms=now)
            return jsonify({"rate": rate, "cached": False, "source": "frankfurter.app"})
        except Exception as e:
            if fx_cache["rate"] is not None:
                return jsonify({"rate": fx_cache["rate"], "cached": True, "stale": True})
            return jsonify({"rate": None, "error": f"為替レート取得失敗: {str(e)[:150]}"})

    @app.get("/api/settings")
    def api_settings_get() -> Response:
        fresh = load_config(config_path)
        local_overrides: dict[str, Any] = {}
        if local_cfg_path.exists():
            try:
                with open(local_cfg_path, encoding="utf-8") as f:
                    local_overrides = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                local_overrides = {}
        return jsonify(
            {
                "params": _current_params(fresh),
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
        write_local_overrides(local_cfg_path, values, source="dashboard")
        return jsonify({"ok": True, "written": str(local_cfg_path), "values": values})

    @app.post("/api/advice")
    def api_advice_start() -> tuple[Response, int] | Response:
        if not _token_ok():
            return jsonify({"error": "invalid or missing X-Dashboard-Token"}), 401
        with advice_lock:
            if advice_job["status"] == "running":
                return jsonify({"error": "already running"}), 409
            advice_job.update({"status": "running", "result": None, "error": None, "started_ms": _utc_ms()})

        def _run() -> None:
            try:
                from ..advisor.param_advisor import advise_params

                fresh = load_config(config_path)
                days = 7
                since = _utc_ms() - days * 24 * 3600 * 1000
                bph = bars_per_hour(str(fresh["intervals"]["signal"]))
                records = _tail_jsonl(log_path, max_lines=min(16000, int(days * 24 * bph) + 800))
                stats = _advice_stats(records, since, days)
                result = advise_params(fresh, _current_params(fresh), stats)
                with advice_lock:
                    advice_job.update({"status": "done", "result": result})
            except Exception as e:  # CLI 不在やタイムアウトも画面に返す
                with advice_lock:
                    advice_job.update({"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"started": True})

    @app.get("/api/advice")
    def api_advice_status() -> Response:
        with advice_lock:
            out = {
                "status": advice_job["status"],
                "error": advice_job["error"],
                "result": advice_job["result"],
            }
            if advice_job["status"] == "running":
                out["elapsed_seconds"] = (_utc_ms() - advice_job["started_ms"]) / 1000.0
        return jsonify(out)

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
        hours = max(6, min(24 * 30, hours))

        with whatif_lock:
            if whatif_job["status"] == "running":
                return jsonify({"error": "already running"}), 409
            whatif_job.update({"status": "running", "result": None, "error": None, "started_ms": _utc_ms()})

        overrides = _values_to_nested(values)

        def _run() -> None:
            try:
                from ..backtest.whatif import run_what_if

                fresh = load_config(config_path)
                bph = bars_per_hour(str(fresh["intervals"]["signal"]))
                res = run_what_if(fresh, overrides, eval_bars=int(hours * bph))
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

    @app.get("/api/autotune-history")
    def api_autotune_history() -> Response:
        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            limit = 20
        limit = max(1, min(100, limit))
        hist_path = root / "data" / "auto_tune_history.jsonl"
        rows = _tail_jsonl(hist_path, max_lines=limit)
        return jsonify({"count": len(rows), "history": list(reversed(rows))})

    @app.get("/api/aerodrome/events")
    def api_aerodrome_events() -> Response:
        """aerodrome_radar のタイムロック検知記録（Firestore）を返す。
        通知はS級/A級のみだが、B級も含め検知した全件をここでは確認できる。
        価格取得(core/pricing.py)は現状プレースホルダーのため、T0/T48価格・PnL・
        答え合わせ状態は返さない（実装され次第あらためて追加する）。"""
        try:
            from services.firebase_service import FirebaseService
        except Exception as e:
            return jsonify({"error": f"Firebase未設定: {str(e)[:200]}"})

        rank_f = str(request.args.get("rank", "all"))
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        try:
            page_size = int(request.args.get("page_size", "20"))
        except ValueError:
            page_size = 20
        page_size = max(1, min(100, page_size))

        all_events = FirebaseService.query_simulations(limit_fetch=500)
        for e in all_events:
            # raw_calldataは大きく未使用。価格系はプレースホルダー値のため誤解を避けて除外
            for k in ("raw_calldata", "t0_price", "t48_price", "simulated_pnl", "status", "slippage"):
                e.pop(k, None)

        # サマリーは絞り込み前の件数（全体/ランク別）
        summary: dict[str, int] = {"total": len(all_events), "S": 0, "A": 0, "B": 0}
        for e in all_events:
            rank = e.get("ai_rank")
            if rank in summary:
                summary[rank] += 1

        filtered = all_events
        if rank_f != "all":
            filtered = [e for e in filtered if e.get("ai_rank") == rank_f]

        total = len(filtered)
        total_pages = max(1, -(-total // page_size))  # ceil
        page = min(page, total_pages)
        start = (page - 1) * page_size
        page_rows = filtered[start : start + page_size]

        return jsonify(
            {
                "events": page_rows, "total": total, "page": page,
                "page_size": page_size, "total_pages": total_pages,
                "summary": summary,
            }
        )

    @app.get("/api/aerodrome/price-chart")
    def api_aerodrome_price_chart() -> Response:
        """検知時刻を挟んだ前後のBTC相場を返す（対象トークン自体の価格ではなく、
        市場全体の値動きを参考として見るための代替チャート）。
        既存のKlineキャッシュ(SQLite)から読むだけで、Binanceへのライブ取得は行わない
        （検知は常に過去の出来事なので、既にキャッシュ済みのはず）。"""
        center_raw = request.args.get("center")
        if not center_raw:
            return jsonify({"error": "center is required", "klines": []})
        try:
            center_ms = int(datetime.fromisoformat(center_raw.replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError):
            try:
                center_ms = int(center_raw)
            except ValueError:
                return jsonify({"error": "invalid center", "klines": []})

        iv = request.args.get("interval", "1h")
        if iv not in _ALLOWED_INTERVALS:
            iv = "1h"
        try:
            before_hours = max(1, min(24 * 400, int(request.args.get("before_hours", "24"))))
        except ValueError:
            before_hours = 24
        try:
            after_hours = max(1, min(24 * 400, int(request.args.get("after_hours", "72"))))
        except ValueError:
            after_hours = 72

        since = center_ms - before_hours * 3600 * 1000
        until = center_ms + after_hours * 3600 * 1000

        rows: list[dict[str, Any]] = []
        if db_path.exists():
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
                try:
                    cur = con.execute(
                        "SELECT open_time, open, high, low, close, volume FROM klines "
                        "WHERE symbol = ? AND interval = ? AND open_time >= ? AND open_time <= ? "
                        "ORDER BY open_time",
                        (symbol, iv, since, until),
                    )
                    rows = [
                        {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                        for r in cur.fetchall()
                    ]
                finally:
                    con.close()
            except sqlite3.Error as e:
                return jsonify({"error": str(e), "klines": []})

        return jsonify(
            {"symbol": symbol, "interval": iv, "center_ms": center_ms, "count": len(rows), "klines": rows}
        )

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

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from ..config import load_config, package_root

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
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
    }
    .toolbar a { color: var(--accent); text-decoration: none; font-size: 0.875rem; }
    .toolbar a:hover { text-decoration: underline; }
    #status { font-size: 0.8rem; color: var(--muted); }
    .range-btns { display: flex; gap: 0.25rem; }
    .range-btns button {
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
    .grid2 { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    .grid2 section { margin-top: 0; }
    .scroll-x { overflow-x: auto; }
    @media (max-width: 640px) {
      #price-chart { height: 300px; }
      dl { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <h1>BTC Paper Trader</h1>
  <p class="sub">読み取り専用ダッシュボード · 時刻は日本時間 (JST) · 60秒ごとに自動更新</p>

  <div class="toolbar">
    <div class="range-btns" id="range-btns">
      <button data-hours="24" class="active">1日</button>
      <button data-hours="72">3日</button>
      <button data-hours="168">7日</button>
    </div>
    <span id="status">読み込み中…</span>
    <a href="/api/state" target="_blank" rel="noopener">state</a>
    <a href="/api/trades?hours=24" target="_blank" rel="noopener">trades</a>
    <a href="/api/events?limit=100" target="_blank" rel="noopener">events</a>
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
  </section>

  <section>
    <h2>取引履歴</h2>
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

  <div class="grid2" style="margin-top: 1rem;">
    <section>
      <h2>口座・ポジション</h2>
      <dl id="state-dl"></dl>
    </section>
    <section>
      <h2>設定サマリ</h2>
      <dl id="cfg-dl"></dl>
    </section>
  </div>

  <script>
    const POS = "#3ecf8e", NEG = "#e06c75", MUTED = "#8b98a8", ACCENT = "#3d8fd1";
    const JST_OFFSET = 9 * 3600;
    let hours = 24;
    let priceChart = null, candleSeries = null, equityChart = null, equitySeries = null;

    const REASON_JA = {
      sl: "損切り", tp: "利確", time: "時間切れ", partial_tp: "部分利確",
    };

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

    function renderTiles(state, trades) {
      const closed = trades.filter(function (t) { return !t.partial && t.pnl != null; });
      const allPnl = trades.reduce(function (a, t) { return a + (t.pnl || 0); }, 0);
      const wins = closed.filter(function (t) { return t.pnl > 0; }).length;
      const winRate = closed.length ? (wins / closed.length * 100) : null;
      const rangeLabel = hours === 24 ? "直近1日" : hours === 72 ? "直近3日" : "直近7日";
      const posLine = state.side === 0 || state.side == null
        ? '<span class="pill flat">FLAT</span>'
        : sideLabel(state.side) + ' <span class="s">建値 ' + fmtNum(state.entry_px, 1) + "</span>";
      const tiles = [
        { k: "評価額 (USDT)", v: fmtNum(state.quote, 2), s: "" },
        { k: rangeLabel + "の実現損益", v: pnlHtml(allPnl), s: "取引 " + closed.length + " 件" },
        { k: "勝率 (" + rangeLabel + ")", v: winRate == null ? "—" : fmtNum(winRate, 0) + "%", s: closed.length ? wins + "勝" + (closed.length - wins) + "敗" : "取引なし" },
        { k: "本日の損益", v: pnlHtml(state.daily_pnl), s: state.halt_new_entries ? "⚠ 新規停止中" : "" },
        { k: "現在ポジション", v: posLine, s: "" },
      ];
      document.getElementById("tiles").innerHTML = tiles.map(function (t) {
        return '<div class="tile"><div class="k">' + t.k + '</div><div class="v">' + t.v + '</div>' +
          (t.s ? '<div class="s">' + t.s + "</div>" : "") + "</div>";
      }).join("");
    }

    function renderChart(klines, trades) {
      if (!candleSeries) return;
      const candles = klines.map(function (k) {
        return { time: toChartTime(k.t), open: k.o, high: k.h, low: k.l, close: k.c };
      });
      candleSeries.setData(candles);

      const minT = candles.length ? candles[0].time : 0;
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
      candleSeries.setMarkers(markers);
      priceChart.timeScale().fitContent();
    }

    function renderEquity(equity) {
      if (!equitySeries) return;
      const seen = {};
      const pts = [];
      equity.forEach(function (p) {
        const tt = toChartTime(p.t);
        if (!seen[tt]) { seen[tt] = 1; pts.push({ time: tt, value: p.q }); }
      });
      equitySeries.setData(pts);
      equityChart.timeScale().fitContent();
    }

    function renderTrades(trades) {
      const tbody = document.getElementById("trades-body");
      if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted)">この期間の取引はありません</td></tr>';
        return;
      }
      tbody.innerHTML = trades.slice().reverse().map(function (t) {
        const reason = REASON_JA[t.reason] || t.reason || "—";
        return "<tr>" +
          '<td class="mono">' + fmtJst(t.entry_time) + "</td>" +
          '<td class="mono">' + (t.open ? '<span class="pill flat">保有中</span>' : fmtJst(t.exit_time)) + "</td>" +
          "<td>" + sideLabel(t.side) + (t.partial ? ' <span class="s" style="color:var(--muted)">½</span>' : "") + "</td>" +
          "<td>" + fmtNum(t.entry_px, 1) + "</td>" +
          "<td>" + fmtNum(t.exit_px, 1) + "</td>" +
          "<td>" + pnlHtml(t.pnl) + "</td>" +
          "<td>" + (t.open ? "—" : reason) + "</td>" +
          "</tr>";
      }).join("");
    }

    function renderPanels(state, cfg) {
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
      const cdl = document.getElementById("cfg-dl");
      const cr = [
        ["シンボル", cfg.symbol || "—"],
        ["entry_threshold", cfg.entry_threshold != null ? String(cfg.entry_threshold) : "—"],
        ["min_confidence", cfg.min_confidence != null ? String(cfg.min_confidence) : "—"],
        ["max_daily_loss_pct", cfg.max_daily_loss_pct != null ? String(cfg.max_daily_loss_pct) : "—"],
      ];
      cdl.innerHTML = cr.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("");
    }

    async function load() {
      const st = document.getElementById("status");
      try {
        const [state, cfg, kRes, tRes] = await Promise.all([
          fetch("/api/state").then(function (r) { return r.json(); }),
          fetch("/api/config-summary").then(function (r) { return r.json(); }),
          fetch("/api/klines?hours=" + hours).then(function (r) { return r.json(); }),
          fetch("/api/trades?hours=" + hours).then(function (r) { return r.json(); }),
        ]);
        const trades = tRes.trades || [];
        document.getElementById("price-title").textContent =
          (kRes.symbol || "BTCUSDT") + " " + (kRes.interval || "15m") + " — 値動きと売買ポイント";
        renderTiles(state, trades);
        renderChart(kRes.klines || [], trades);
        renderEquity(tRes.equity || []);
        renderTrades(trades);
        renderPanels(state, cfg);
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

    if (!initCharts()) {
      document.getElementById("price-chart").innerHTML =
        '<p style="color:var(--muted);font-size:0.85rem">チャートライブラリ (unpkg.com) を読み込めませんでした。ネットワーク接続を確認してください。表とサマリは下部に表示されます。</p>';
    }
    load();
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
    """paper_events.jsonl のレコード列からエントリー/決済をペアリングして取引一覧を作る。

    戻り値: (確定・部分決済の取引リスト, 未決済ポジション or None)
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


def create_app(config_path: Path | None = None) -> Flask:
    cfg = load_config(config_path)
    root = package_root()
    state_path = root / cfg.get("paper", {}).get("state_path", "data/paper_state.json")
    log_path = root / cfg.get("logging", {}).get("jsonl_path", "data/paper_events.jsonl")
    db_path = root / cfg.get("data", {}).get("cache_sqlite", "data/btc_klines.sqlite")
    symbol = str(cfg.get("symbol", "BTCUSDT"))
    interval = str(cfg.get("intervals", {}).get("signal", "15m"))

    app = Flask(__name__)

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

    @app.get("/api/events")
    def api_events() -> Response:
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            limit = 100
        limit = max(1, min(500, limit))
        rows = _tail_jsonl(log_path, max_lines=limit)
        return jsonify({"path": str(log_path), "count": len(rows), "events": rows})

    @app.get("/api/config-summary")
    def api_config_summary() -> Response:
        c = cfg.get("combine", {})
        f = cfg.get("filters", {})
        r = cfg.get("risk", {})
        return jsonify(
            {
                "symbol": cfg.get("symbol"),
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

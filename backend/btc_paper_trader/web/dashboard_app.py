from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request

from ..config import load_config, package_root

_DASH_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BTC Paper Trader — Dashboard</title>
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
    .sub { color: var(--muted); font-size: 0.875rem; margin-bottom: 1.25rem; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    section {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.1rem;
    }
    section h2 {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin: 0 0 0.75rem;
      font-weight: 600;
    }
    dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; }
    dt { color: var(--muted); }
    dd { margin: 0; font-variant-numeric: tabular-nums; }
    .pill {
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
    }
    .long { background: rgba(62, 207, 142, 0.15); color: var(--pos); }
    .short { background: rgba(224, 108, 117, 0.15); color: var(--neg); }
    .flat { background: rgba(139, 152, 168, 0.2); color: var(--muted); }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8rem;
    }
    th, td {
      text-align: left;
      padding: 0.45rem 0.5rem;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--muted); font-weight: 500; }
    tr:hover td { background: rgba(255,255,255,0.03); }
    .mono { font-family: ui-monospace, monospace; font-size: 0.78rem; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 1rem;
    }
    .toolbar a {
      color: var(--accent);
      text-decoration: none;
      font-size: 0.875rem;
    }
    .toolbar a:hover { text-decoration: underline; }
    #status { font-size: 0.8rem; color: var(--muted); }
    @media (max-width: 640px) {
      dl { grid-template-columns: 1fr; }
      dt { margin-top: 0.5rem; }
      dt:first-child { margin-top: 0; }
    }
  </style>
</head>
<body>
  <h1>BTC Paper Trader</h1>
  <p class="sub">読み取り専用 · 状態と直近ログを表示します（30秒ごとに更新）</p>
  <div class="toolbar">
    <span id="status">読み込み中…</span>
    <a href="/api/state" target="_blank" rel="noopener">state.json</a>
    <a href="/api/events?limit=100" target="_blank" rel="noopener">events JSON</a>
  </div>
  <div class="grid">
    <section>
      <h2>口座・ポジション</h2>
      <dl id="state-dl"></dl>
    </section>
    <section>
      <h2>設定サマリ</h2>
      <dl id="cfg-dl"></dl>
    </section>
  </div>
  <section style="margin-top: 1rem;">
    <h2>直近イベント（paper_events.jsonl）</h2>
    <div style="overflow-x: auto;">
      <table>
        <thead>
          <tr>
            <th>時刻(UTC)</th>
            <th>バー open</th>
            <th>評価額</th>
            <th>方向</th>
            <th>pending</th>
            <th>メモ</th>
          </tr>
        </thead>
        <tbody id="ev-body"></tbody>
      </table>
    </div>
  </section>
  <script>
    function sideLabel(s) {
      if (s === 1) return '<span class="pill long">LONG</span>';
      if (s === -1) return '<span class="pill short">SHORT</span>';
      return '<span class="pill flat">FLAT</span>';
    }
    function fmtTs(ms) {
      if (!ms) return "—";
      const d = new Date(ms);
      return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
    }
    async function load() {
      const st = document.getElementById("status");
      try {
        const [state, cfg, evRes] = await Promise.all([
          fetch("/api/state").then(r => r.json()),
          fetch("/api/config-summary").then(r => r.json()),
          fetch("/api/events?limit=80").then(r => r.json()),
        ]);
        st.textContent = "最終更新: " + new Date().toLocaleString("ja-JP", { timeZone: "UTC" }) + " UTC";

        const sdl = document.getElementById("state-dl");
        const rows = [
          ["評価額 (quote)", state.quote != null ? Number(state.quote).toFixed(2) : "—"],
          ["方向", sideLabel(state.side)],
          ["建値", state.entry_px != null ? Number(state.entry_px).toFixed(2) : "—"],
          ["pending", state.pending != null ? String(state.pending) : "—"],
          ["日次損益", state.daily_pnl != null ? Number(state.daily_pnl).toFixed(2) : "—"],
          ["新規停止", state.halt_new_entries ? "はい" : "いいえ"],
          ["last_hour_key", state.last_hour_key || "—"],
          ["last_m15_open_time", state.last_m15_open_time || "—"],
        ];
        sdl.innerHTML = rows.map(([k,v]) => "<dt>" + k + "</dt><dd>" + v + "</dd>").join("");

        const cdl = document.getElementById("cfg-dl");
        const cr = [
          ["シンボル", cfg.symbol || "—"],
          ["entry_threshold", cfg.entry_threshold != null ? String(cfg.entry_threshold) : "—"],
          ["min_confidence", cfg.min_confidence != null ? String(cfg.min_confidence) : "—"],
          ["max_daily_loss_pct", cfg.max_daily_loss_pct != null ? String(cfg.max_daily_loss_pct) : "—"],
        ];
        cdl.innerHTML = cr.map(([k,v]) => "<dt>" + k + "</dt><dd>" + v + "</dd>").join("");

        const tbody = document.getElementById("ev-body");
        const evs = evRes.events || [];
        tbody.innerHTML = evs.slice().reverse().map(function(row) {
          const evs = row.events || [];
          let memo = "";
          for (let i = 0; i < evs.length; i++) {
            const e = evs[i];
            if (e.type === "entry") {
              memo += "ENTRY " + (e.side === 1 ? "L" : "S") + " ";
            } else if (e.type === "decision" && e.reason) {
              memo += (e.reason || "").slice(0, 24) + " ";
            } else if (e.pnl != null) {
              memo += "PnL " + Number(e.pnl).toFixed(2) + " ";
            }
          }
          return "<tr><td class='mono'>" + fmtTs(row.t) + "</td><td class='mono'>" + (row.bar_open_time || "—") +
            "</td><td>" + (row.quote != null ? Number(row.quote).toFixed(2) : "—") + "</td><td>" +
            sideLabel(row.side) + "</td><td>" + (row.pending != null ? row.pending : "—") +
            "</td><td class='mono'>" + (memo.trim() || "—") + "</td></tr>";
        }).join("");
      } catch (e) {
        st.textContent = "読み込み失敗: " + e;
      }
    }
    load();
    setInterval(load, 30000);
  </script>
</body>
</html>
"""


def _tail_jsonl(path: Path, max_lines: int = 200) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    size = path.stat().st_size
    chunk = min(2_000_000, size)
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


def create_app(config_path: Path | None = None) -> Flask:
    cfg = load_config(config_path)
    root = package_root()
    state_path = root / cfg.get("paper", {}).get("state_path", "data/paper_state.json")
    log_path = root / cfg.get("logging", {}).get("jsonl_path", "data/paper_events.jsonl")

    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return render_template_string(_DASH_HTML)

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

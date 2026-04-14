from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backtest.engine import prepare_frame, run_backtest, train_model_slice
from .backtest.tune import tune_last_window_and_write
from .backtest.walk_forward import walk_forward
from .config import load_config, package_root
from .eval.metrics import summarize_trades
from .notify.discord import post_daily_summary, post_hourly_summary, post_tune_result
from .paper.runner import paper_step_once, run_paper_loop


def main() -> None:
    p = argparse.ArgumentParser(prog="btc_paper_trader", description="BTC USDT-M paper trading (Binance futures)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_fetch = sub.add_parser("fetch", help="Download / refresh klines cache")
    s_fetch.add_argument("--config", type=Path, default=None)

    s_bt = sub.add_parser("backtest", help="Run single-window backtest")
    s_bt.add_argument("--config", type=Path, default=None)

    s_wf = sub.add_parser("walk-forward", help="Walk-forward grid search")
    s_wf.add_argument("--config", type=Path, default=None)

    s_paper = sub.add_parser("paper", help="Paper trading loop (Ctrl+C to stop)")
    s_paper.add_argument("--config", type=Path, default=None)
    s_paper.add_argument("--once", action="store_true", help="Single poll cycle (for testing)")

    s_nt = sub.add_parser("notify-test", help="Send test embeds to Discord webhooks (env HOURLY/DAILY)")
    s_nt.add_argument("--config", type=Path, default=None)

    s_tune = sub.add_parser(
        "tune",
        help="Grid-search last WF window; write data/runtime_params.json (merged on next load_config)",
    )
    s_tune.add_argument("--config", type=Path, default=None)

    args = p.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "fetch":
        prepare_frame(cfg)
        print("OK: cache refreshed under", package_root() / cfg["data"]["cache_sqlite"])
        return

    if args.cmd == "backtest":
        df = prepare_frame(cfg)
        n = len(df)
        i_train0 = max(0, n - 9000)
        model = train_model_slice(df, cfg, i_train0, n - 1)
        pnls, trades = run_backtest(df, model, cfg, i_train0 + 500, n - 1)
        summ = summarize_trades(pnls, cfg["backtest"]["initial_quote"])
        print(json.dumps(summ, indent=2, ensure_ascii=False))
        out = package_root() / "data" / "last_backtest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"summary": summ, "n_trades": len(trades)}, f, ensure_ascii=False, indent=2)
        return

    if args.cmd == "walk-forward":
        res = walk_forward(cfg)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return

    if args.cmd == "paper":
        if args.once:
            paper_step_once(cfg)
        else:
            run_paper_loop(cfg)
        return

    if args.cmd == "tune":
        out = tune_last_window_and_write(cfg)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        if out.get("skipped"):
            print("SKIP: runtime_params.json unchanged (not better than current)")
            bs = out.get("baseline_score")
            bst = out.get("best_score")
            post_tune_result(
                "**tune**（過去データで取引パラメータの候補を自動探索する処理）が完了しました。"
                "**今お使いの設定のほうが成績評価が良かった**ため、設定ファイルは変更していません。",
                fields=[
                    {
                        "name": "理由（やさしい説明）",
                        "value": (
                            "複数のパラメータの組を試しましたが、**テスト区間の成績スコア**が、"
                            "いま `runtime_params.json` に入っている設定（現行）を上回りませんでした。\n"
                            "悪い候補で上書きしないよう、**ファイルは更新していません**。これは正常な動きです。\n"
                            "※スコアは損益・ドローダウン・PF などから計算した総合値です（大きいほど良い想定）。"
                        )[:1000],
                        "inline": False,
                    },
                    {
                        "name": "参考（数値の見方）",
                        "value": (
                            f"・**現行設定のスコア**: {bs}\n"
                            f"・**今回いちばん良かった候補のスコア**: {bst}\n"
                            "両方を比べ、候補が現行より良くないと判断したためスキップしました。"
                        )[:1000],
                        "inline": False,
                    },
                ],
            )
        else:
            print("OK: wrote", package_root() / "data" / "runtime_params.json")
            summ = out.get("test_summary") or {}
            post_tune_result(
                "`runtime_params.json` を更新しました（直近ウォークフォワードのテスト区間で最良スコア）。",
                fields=[
                    {"name": "best_score", "value": f"{out.get('best_score')}", "inline": True},
                    {"name": "test PF", "value": f"{summ.get('profit_factor', 0):.2f}", "inline": True},
                    {"name": "test trades", "value": f"{summ.get('n_trades', 0)}", "inline": True},
                    {"name": "risk", "value": str(out.get("risk")), "inline": False},
                    {"name": "combine", "value": str(out.get("combine")), "inline": False},
                    {"name": "filters", "value": str(out.get("filters")), "inline": False},
                ],
            )
        return

    if args.cmd == "notify-test":
        post_hourly_summary(
            "接続テスト（毎時チャンネル）: `btc_paper_trader notify-test` から送信しました。",
            fields=[{"name": "status", "value": "ok", "inline": True}],
        )
        post_daily_summary(
            "接続テスト（日次チャンネル）: 日次ウェブフックへの送信確認です。",
            fields=[{"name": "status", "value": "ok", "inline": True}],
        )
        print("OK: notify-test sent (if DISCORD_WEBHOOK_URL_* are set in environment or backend/.env)")
        return


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "config.yaml"


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE into os.environ if not already set (backend/.env)."""
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _runtime_params_acceptable(rt: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """
    Avoid applying clearly bad tuned params to live paper trading.
    If test_summary is missing, keep backward compatibility and allow merge.
    """
    ts = rt.get("test_summary")
    if not isinstance(ts, dict):
        return True

    guard = cfg.get("runtime_guard") or {}
    min_trades = int(guard.get("min_trades", 8))
    min_pf = float(guard.get("min_profit_factor", 1.05))
    min_wr = float(guard.get("min_win_rate", 0.45))
    min_expectancy = float(guard.get("min_expectancy", 0.0))

    n_trades = int(ts.get("n_trades", 0) or 0)
    pf = float(ts.get("profit_factor", 0.0) or 0.0)
    win_rate = float(ts.get("win_rate", 0.0) or 0.0)
    expectancy = float(ts.get("expectancy", 0.0) or 0.0)

    return (
        n_trades >= min_trades
        and pf >= min_pf
        and win_rate >= min_wr
        and expectancy >= min_expectancy
    )


def package_root() -> Path:
    return Path(__file__).resolve().parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = package_root()
    _load_env_file(root.parent / ".env")
    cfg_path = path or (root / DEFAULT_CONFIG_NAME)
    with open(cfg_path, encoding="utf-8") as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}

    local = root / "config.local.yaml"
    if local.exists():
        with open(local, encoding="utf-8") as f:
            local_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local_cfg)

    use_runtime = bool(cfg.get("use_runtime_params", True))
    runtime = root / "data" / "runtime_params.json"
    if use_runtime and runtime.exists():
        try:
            with open(runtime, encoding="utf-8") as f:
                rt = json.load(f)
        except (OSError, json.JSONDecodeError):
            rt = {}
        if _runtime_params_acceptable(rt, cfg):
            for key in ("risk", "combine", "filters"):
                if key in rt and isinstance(rt[key], dict):
                    cfg = _deep_merge(cfg, {key: rt[key]})

    return cfg


def env_webhook_hourly() -> str | None:
    return os.environ.get("DISCORD_WEBHOOK_URL_HOURLY")


def env_webhook_daily() -> str | None:
    return os.environ.get("DISCORD_WEBHOOK_URL_DAILY")

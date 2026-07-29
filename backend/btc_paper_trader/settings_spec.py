"""ダッシュボードと自動チューナーが共用する、編集可能パラメータの定義と検証。

auto: True の項目だけが Claude 自動チューニングの変更対象。
資金リスク系（position_fraction / max_daily_loss_pct）と advisor.mode は
意図的に auto: False とし、人間の手動操作でのみ変更できる。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

EDITABLE_PARAMS: list[dict[str, Any]] = [
    {"path": "combine.entry_threshold", "label": "エントリー閾値", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "auto": True, "help": "小さいほど取引が増える"},
    {"path": "combine.weight_model", "label": "モデル比重", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.05, "auto": True, "help": "パターン比重は自動で 1−この値"},
    {"path": "filters.min_confidence", "label": "最低確信度", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.01, "auto": True, "help": "大きいほど厳選"},
    {"path": "risk.tp_atr_mult", "label": "利確幅 (ATR倍)", "type": "float",
     "min": 0.1, "max": 10.0, "step": 0.1, "auto": True, "help": ""},
    {"path": "risk.sl_atr_mult", "label": "損切り幅 (ATR倍)", "type": "float",
     "min": 0.1, "max": 10.0, "step": 0.1, "auto": True, "help": ""},
    {"path": "risk.max_hold_bars", "label": "最大保有バー数", "type": "int",
     "min": 1, "max": 500, "step": 1, "auto": True, "help": "運用中の時間足の本数"},
    {"path": "risk.position_fraction", "label": "投入資金割合", "type": "float",
     "min": 0.01, "max": 1.0, "step": 0.01, "auto": False, "help": "自動調整の対象外（手動のみ）"},
    {"path": "risk.max_daily_loss_pct", "label": "日次最大損失率", "type": "float",
     "min": 0.005, "max": 1.0, "step": 0.005, "auto": False, "help": "超えると当日の新規停止（手動のみ）"},
    {"path": "advisor.mode", "label": "Claudeアドバイザー", "type": "choice",
     "choices": ["advise", "gate", "off"], "auto": False, "help": "gate=vetoでエントリー中止"},
]


def get_by_path(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def values_to_nested(values: dict[str, Any]) -> dict[str, Any]:
    """{"combine.entry_threshold": 0.1} → {"combine": {"entry_threshold": 0.1}}"""
    nested: dict[str, Any] = {}
    for dotted, v in values.items():
        cur = nested
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return nested


def validate_values(raw: Any, auto_only: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """編集可能リストに対して型・範囲を検証し、正規化した values を返す。

    auto_only=True の場合、auto: True の項目以外が含まれていたら拒否する
    （自動チューナーが資金リスク系へ手を出すのを防ぐ）。
    """
    if not isinstance(raw, dict):
        return None, "values must be an object"
    spec_by_path = {p["path"]: p for p in EDITABLE_PARAMS}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        spec = spec_by_path.get(k)
        if spec is None:
            return None, f"editable でないキー: {k}"
        if auto_only and not spec.get("auto"):
            return None, f"自動調整の対象外キー: {k}"
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


def write_local_overrides(local_cfg_path: Path, values: dict[str, Any], source: str) -> None:
    """検証済み values を config.local.yaml に deep-merge して原子的に書き込む。"""
    from .config import _deep_merge

    existing: dict[str, Any] = {}
    if local_cfg_path.exists():
        try:
            with open(local_cfg_path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            existing = {}
    merged = _deep_merge(existing, values_to_nested(values))
    tmp = local_cfg_path.with_suffix(".yaml.tmp")
    header = (
        "# このファイルはダッシュボードの設定変更と Claude 自動チューニングで更新されます。\n"
        f"# 最終更新元: {source}\n"
        "# config.yaml と runtime_params.json より優先されます。\n"
    )
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, local_cfg_path)

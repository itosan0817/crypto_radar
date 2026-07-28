"""Claude Code CLI (ヘッドレスモード) の同期呼び出しラッパー。

サブスクリプション認証で動作するため API 課金は発生しない。
aerodrome側の services/ai_service.py と同じ方式だが、btc_paper_trader は
同期ループなので subprocess を直接使う。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

_cli_path: str | None = None
_cli_checked = False


def find_cli() -> str | None:
    """claude CLI のパスを取得（初回のみ探索）"""
    global _cli_path, _cli_checked
    if not _cli_checked:
        _cli_path = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")
        _cli_checked = True
    return _cli_path


def run_claude(prompt: str, model: str, timeout: int = 120) -> str:
    """claude -p を実行し、応答テキストを返す。失敗時は例外。"""
    cli = find_cli()
    if not cli:
        raise RuntimeError("claude CLI not found (set CLAUDE_CLI_PATH or install claude)")
    proc = subprocess.run(
        [cli, "-p", "--model", model, "--output-format", "json"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {(proc.stderr or '')[:200]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise RuntimeError(f"claude CLI error: {str(envelope.get('result'))[:200]}")
    return envelope.get("result", "")


def extract_json(text: str) -> dict[str, Any]:
    """応答テキストからJSON部分を取り出してパースする"""
    text = text.replace("```json", "").replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)

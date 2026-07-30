"""
Windows のサービス／パイプリダイレクト環境で、print(flush=True) が OSError(22) になることがある。
また Windows コンソールの既定コードページ(cp932等)では絵文字が UnicodeEncodeError になることもある。
その場合でもプロセスを落とさないための安全なコンソール出力。
"""
from __future__ import annotations

import sys


def safe_print(msg: str, *, flush: bool = True) -> None:
    """標準出力へ書き込み。失敗時は stderr / buffer へフォールバック。いずれも失敗なら黙ってスキップ。"""
    line = msg + "\n"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.write(line)
            if flush:
                stream.flush()
            return
        except (OSError, UnicodeError):
            continue
    try:
        buf = getattr(sys.stderr, "buffer", None) or getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(line.encode("utf-8", errors="replace"))
            buf.flush()
    except OSError:
        pass

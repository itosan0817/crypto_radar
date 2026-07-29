from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

KLINES_PATH = "/fapi/v1/klines"
FUNDING_PATH = "/fapi/v1/fundingRate"
MAX_LIMIT = 1500
MAX_FUNDING_LIMIT = 1000

# Binance interval -> milliseconds
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}

INTERVAL_LABEL_JA: dict[str, str] = {
    "1m": "1分足", "3m": "3分足", "5m": "5分足", "15m": "15分足", "30m": "30分足",
    "1h": "1時間足", "2h": "2時間足", "4h": "4時間足", "6h": "6時間足", "8h": "8時間足",
    "12h": "12時間足", "1d": "日足", "3d": "3日足", "1w": "週足",
}


def bars_per_hour(interval: str) -> float:
    """指定インターバルの1時間あたりのバー本数（15m→4, 1h→1 など）。"""
    return 3_600_000 / INTERVAL_MS[interval]


def interval_label_ja(interval: str) -> str:
    return INTERVAL_LABEL_JA.get(interval, interval)


def _ensure_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def fetch_klines_page(
    base_url: str,
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = MAX_LIMIT,
) -> list[list[Any]]:
    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": min(limit, MAX_LIMIT)}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    url = base_url.rstrip("/") + KLINES_PATH
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def klines_to_df(raw: list[list[Any]]) -> pd.DataFrame:
    cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]
    if not raw:
        return pd.DataFrame(columns=cols[:7])
    df = pd.DataFrame(raw, columns=cols)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


def fetch_klines_range(
    base_url: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Paginate Binance USDT-M klines in [start_ms, end_ms)."""
    step_ms = INTERVAL_MS.get(interval)
    if step_ms is None:
        raise ValueError(f"unsupported interval: {interval}")
    chunks: list[pd.DataFrame] = []
    cur = start_ms
    while cur < end_ms:
        raw = fetch_klines_page(base_url, symbol, interval, start_ms=cur, end_ms=end_ms, limit=MAX_LIMIT)
        if not raw:
            break
        df = klines_to_df(raw)
        chunks.append(df)
        last_open = int(df["open_time"].iloc[-1])
        cur = last_open + step_ms
        if len(df) < MAX_LIMIT:
            break
        time.sleep(sleep_s)
    if not chunks:
        return klines_to_df([])
    out = pd.concat(chunks, ignore_index=True)
    out = out.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    out = out[(out["open_time"] >= start_ms) & (out["open_time"] < end_ms)]
    return out.reset_index(drop=True)


def fetch_funding_rates_range(
    base_url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    sleep_s: float = 0.15,
) -> pd.DataFrame:
    """
    Paginate Binance USDT-M funding rates in [start_ms, end_ms).
    Returns columns: funding_time, funding_rate, mark_price.
    """
    url = base_url.rstrip("/") + FUNDING_PATH
    chunks: list[pd.DataFrame] = []
    cur = int(start_ms)
    while cur < end_ms:
        params: dict[str, Any] = {
            "symbol": symbol,
            "startTime": int(cur),
            "endTime": int(end_ms),
            "limit": MAX_FUNDING_LIMIT,
        }
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        raw = r.json()
        if not raw:
            break
        df = pd.DataFrame(raw)
        if "fundingTime" not in df.columns or "fundingRate" not in df.columns:
            break
        out = pd.DataFrame(
            {
                "funding_time": df["fundingTime"].astype("int64"),
                "funding_rate": df["fundingRate"].astype(float),
                "mark_price": pd.to_numeric(df.get("markPrice"), errors="coerce"),
            }
        )
        chunks.append(out)
        last_t = int(out["funding_time"].iloc[-1])
        cur = last_t + 1
        if len(out) < MAX_FUNDING_LIMIT:
            break
        time.sleep(sleep_s)

    if not chunks:
        return pd.DataFrame(columns=["funding_time", "funding_rate", "mark_price"])
    ret = pd.concat(chunks, ignore_index=True)
    ret = ret.drop_duplicates(subset=["funding_time"]).sort_values("funding_time").reset_index(drop=True)
    ret = ret[(ret["funding_time"] >= start_ms) & (ret["funding_time"] < end_ms)]
    return ret.reset_index(drop=True)


def upsert_sqlite(df: pd.DataFrame, db_path: Path, symbol: str, interval: str) -> None:
    """Replace rows for (symbol, interval) with new df."""
    init_sqlite_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM klines WHERE symbol = ? AND interval = ?", (symbol, interval))
        df2 = df.copy()
        df2.insert(0, "interval", interval)
        df2.insert(0, "symbol", symbol)
        df2.to_sql("klines", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()


def load_from_sqlite(db_path: Path, symbol: str, interval: str) -> pd.DataFrame:
    if not db_path.exists():
        return klines_to_df([])
    conn = sqlite3.connect(db_path)
    try:
        q = "SELECT open_time, open, high, low, close, volume, close_time FROM klines WHERE symbol = ? AND interval = ? ORDER BY open_time"
        df = pd.read_sql_query(q, conn, params=(symbol, interval))
        return df
    finally:
        conn.close()


def init_sqlite_schema(db_path: Path) -> None:
    _ensure_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS klines (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                close_time INTEGER NOT NULL,
                PRIMARY KEY (symbol, interval, open_time)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

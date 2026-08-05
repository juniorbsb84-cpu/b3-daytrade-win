"""Download e persistencia de barras do MT5.

Limites medidos no terminal Clear (04/08/2026):
  * copy_rates_from_pos aceita no maximo 99.999 barras por chamada;
  * o historico total disponivel por simbolo/timeframe tambem satura em 100.000
    barras -> M5 alcanca ~jan/2023 em WIN/WDO e ~dez/2021 em acoes;
  * H1 e D1 alcancam 5 anos completos;
  * M1 so tem ~9 meses (nao serve de base de pesquisa longa).
"""
from __future__ import annotations

import logging

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from src.core import paths
from src.core.mt5session import ensure_selected, mt5_session

log = logging.getLogger(__name__)

TF = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "D1": mt5.TIMEFRAME_D1,
}

MAX_CALL = 99_999


def _to_frame(rates: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    # REGRA DE TEMPO: unit='s' sem fuso == hora de Brasilia. Ver core/timeutil.
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time").sort_index()
    keep = [c for c in ("open", "high", "low", "close", "tick_volume", "real_volume", "spread") if c in df.columns]
    return df[keep].astype({c: "float64" for c in keep if c in ("open", "high", "low", "close")})


def fetch_bars(symbol: str, timeframe: str, max_bars: int = 100_000,
               chunk: int = 40_000) -> pd.DataFrame:
    """Baixa ate `max_bars` barras paginando de tras pra frente."""
    if timeframe not in TF:
        raise ValueError(f"timeframe invalido: {timeframe}")
    ensure_selected(symbol)
    tf = TF[timeframe]
    frames = []
    start = 0
    while start < max_bars:
        n = min(chunk, max_bars - start, MAX_CALL)
        r = mt5.copy_rates_from_pos(symbol, tf, start, n)
        if r is None or len(r) == 0:
            break
        frames.append(_to_frame(r))
        if len(r) < n:
            break
        start += len(r)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def save_bars(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    p = paths.bars_path(symbol, timeframe)
    if p.exists():
        old = pd.read_parquet(p)
        df = pd.concat([old, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(p)


def load_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    p = paths.bars_path(symbol, timeframe)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def download(symbols, timeframes=("M5", "H1", "D1"), max_bars: int = 100_000,
             verbose: bool = True) -> pd.DataFrame:
    """Baixa e persiste. Retorna relatorio com cobertura por simbolo/tf."""
    rows = []
    with mt5_session():
        for sym in symbols:
            for tf in timeframes:
                try:
                    df = fetch_bars(sym, tf, max_bars=max_bars)
                except Exception as exc:  # noqa: BLE001
                    rows.append({"symbol": sym, "tf": tf, "n": 0, "erro": str(exc)})
                    continue
                if df.empty:
                    rows.append({"symbol": sym, "tf": tf, "n": 0, "erro": "vazio"})
                    continue
                save_bars(sym, tf, df)
                rows.append({"symbol": sym, "tf": tf, "n": len(df),
                             "inicio": df.index[0], "fim": df.index[-1], "erro": ""})
                if verbose:
                    print(f"  {sym:10s} {tf:3s} n={len(df):7d}  {df.index[0]} -> {df.index[-1]}")
    return pd.DataFrame(rows)

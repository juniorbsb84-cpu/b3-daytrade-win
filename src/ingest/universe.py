"""Construcao do universo negociavel da B3 a partir do terminal.

Nao usamos lista fixa de acoes: o universo e derivado dos simbolos que o
proprio broker oferece, ranqueados por liquidez financeira medida em D1.
"""
from __future__ import annotations

import re

import MetaTrader5 as mt5
import pandas as pd

from src.core import paths
from src.core.mt5session import ensure_selected, mt5_session

# Ticker de acao/unit/ETF: 4 letras + 1-2 digitos. Exclui opcoes e fracionario.
RE_STOCK = re.compile(r"^[A-Z]{4}(3|4|5|6|11)$")

UNIVERSE_FILE = paths.UNIVERSE / "b3_universe.parquet"
LIQUID_FILE = paths.UNIVERSE / "b3_liquid.parquet"


def list_candidates() -> list[str]:
    """Todos os simbolos do grupo BOVESPA que parecem acao/unit/ETF."""
    out = []
    for s in mt5.symbols_get():
        if not s.path.upper().startswith("BOVESPA"):
            continue
        if RE_STOCK.match(s.name):
            out.append(s.name)
    return sorted(set(out))


def measure_liquidity(symbols: list[str], lookback_days: int = 120) -> pd.DataFrame:
    """Mede liquidez com barras D1: volume financeiro mediano e preco."""
    rows = []
    for sym in symbols:
        ensure_selected(sym)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, lookback_days)
        if r is None or len(r) < 40:
            continue
        df = pd.DataFrame(r)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        fin = df["real_volume"] * df["close"]
        if fin.median() <= 0:
            fin = df["tick_volume"] * df["close"] * 100.0  # fallback grosseiro
        rng = (df["high"] - df["low"]) / df["close"]
        rows.append({
            "symbol": sym,
            "price": float(df["close"].iloc[-1]),
            "fin_vol_median": float(fin.median()),
            "fin_vol_p25": float(fin.quantile(0.25)),
            "atr_pct": float(rng.median()),
            "days": len(df),
            "last": df["time"].iloc[-1],
        })
    return pd.DataFrame(rows).sort_values("fin_vol_median", ascending=False)


def build(top_n: int = 60, min_fin_vol: float = 20_000_000.0,
          min_price: float = 4.0, verbose: bool = True) -> pd.DataFrame:
    """Constroi e persiste o universo liquido."""
    with mt5_session():
        cands = list_candidates()
        if verbose:
            print(f"candidatos com formato de acao/ETF: {len(cands)}")
        liq = measure_liquidity(cands)
    liq.to_parquet(UNIVERSE_FILE)

    sel = liq[(liq["fin_vol_median"] >= min_fin_vol)
              & (liq["price"] >= min_price)
              & (liq["days"] >= 80)].head(top_n).copy()
    sel.to_parquet(LIQUID_FILE)
    if verbose:
        print(f"universo liquido: {len(sel)} tickers "
              f"(volume financeiro mediano >= R${min_fin_vol:,.0f})")
    return sel


def load_liquid() -> pd.DataFrame:
    if not LIQUID_FILE.exists():
        raise FileNotFoundError("universo ainda nao construido -- rode scripts/01_build_universe.py")
    return pd.read_parquet(LIQUID_FILE)

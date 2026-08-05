"""Painel de PnL diario: familia x parametro x simbolo x dia.

Por que existe: escolher a melhor combinacao POR ATIVO gera dezenas de decisoes
independentes e infla o Sharpe por pura selecao. O teste honesto e tratar a
familia como UMA hipotese: o MESMO conjunto de parametros e aplicado a todos os
ativos, e o que se avalia e a carteira agregada. Assim o numero de tentativas
cai para o tamanho da grade e a amostra efetiva multiplica pelo numero de
ativos.

Este modulo gera a materia prima: para cada simbolo, uma matriz
(dias x parametros) de PnL diario liquido, por familia.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest import features
from src.backtest.engine import simulate
from src.core import instruments, paths
from src.ingest.bars import load_bars
from src.strategies import families
from research.sweep import qty_for, session_for

PANEL_DIR = paths.RESULTS / "panel"
PANEL_DIR.mkdir(parents=True, exist_ok=True)


def build_symbol_panel(symbol: str, risk_brl: float = 300.0) -> dict:
    raw = load_bars(symbol, "M5")
    if raw.empty or len(raw) < 20_000:
        return {"symbol": symbol, "erro": "sem dados"}

    ss, se = session_for(symbol)
    d = features.build(raw, session_start=ss, session_end=se)
    inst = instruments.get(symbol)
    days = pd.DatetimeIndex(sorted(set(d["date"])))

    n_written = 0
    for strat in families.ALL:
        cols = {}
        n_tr = {}
        for p in strat.grid():
            p = dict(p)
            ii = strat.intents(d, p)
            if len(ii.bar_idx) < 20:
                continue
            q = qty_for(symbol, d, risk_brl, ii.stop_dist, ii.bar_idx, inst)
            keep = q > 0
            if keep.sum() < 20:
                continue
            ii.bar_idx, ii.side = ii.bar_idx[keep], ii.side[keep]
            ii.stop_dist, ii.target_dist = ii.stop_dist[keep], ii.target_dist[keep]
            ii.exit_idx = ii.exit_idx[keep]
            tr = simulate(d, ii, inst, qty=q[keep])
            if len(tr) < 20:
                continue
            key = strat.key(p)
            s = tr.groupby(pd.to_datetime(tr["exit_time"]).dt.normalize())["net_brl"].sum()
            cols[key] = s.reindex(days, fill_value=0.0)
            n_tr[key] = len(tr)
        if not cols:
            continue
        mat = pd.DataFrame(cols, index=days)
        safe = symbol.replace("$", "_")
        mat.to_parquet(PANEL_DIR / f"{safe}__{strat.name}.parquet")
        pd.Series(n_tr).to_frame("n_trades").to_parquet(
            PANEL_DIR / f"{safe}__{strat.name}__n.parquet")
        n_written += len(cols)
    return {"symbol": symbol, "erro": "", "n_series": n_written}


def _worker(args):
    symbol, risk_brl = args
    try:
        return build_symbol_panel(symbol, risk_brl)
    except Exception:  # noqa: BLE001
        return {"symbol": symbol, "erro": traceback.format_exc(limit=3)}


def load_family_panel(family: str, symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Empilha os paineis dos simbolos: devolve (pnl_por_param_somado, n_trades)."""
    frames, counts = [], []
    for sym in symbols:
        safe = sym.replace("$", "_")
        f = PANEL_DIR / f"{safe}__{family}.parquet"
        if not f.exists():
            continue
        frames.append(pd.read_parquet(f))
        fn = PANEL_DIR / f"{safe}__{family}__n.parquet"
        if fn.exists():
            counts.append(pd.read_parquet(fn)["n_trades"])
    if not frames:
        return pd.DataFrame(), pd.Series(dtype=int)
    # soma por dia sobre todos os ativos; coluna ausente num ativo conta zero
    df = pd.concat(frames).groupby(level=0).sum().sort_index()
    n = (pd.concat(counts).groupby(level=0).sum() if counts else pd.Series(dtype=int))
    return df, n


def n_symbols_per_param(family: str, symbols: list[str]) -> pd.Series:
    """Quantos ativos efetivamente geraram trades em cada combinacao."""
    tot = {}
    for sym in symbols:
        safe = sym.replace("$", "_")
        f = PANEL_DIR / f"{safe}__{family}.parquet"
        if not f.exists():
            continue
        for c in pd.read_parquet(f).columns:
            tot[c] = tot.get(c, 0) + 1
    return pd.Series(tot, dtype=int)

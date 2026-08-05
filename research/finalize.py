"""Congela a configuracao operacional a partir de todo o historico disponivel.

A regra de selecao e EXATAMENTE a mesma usada dentro do walk-forward -- a
unica diferenca e que aqui a janela de treino e o historico inteiro, porque o
proximo pregao e o teste. Nenhum parametro e escolhido a mao.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from research.win_deep import (MAX_FAMILY_WEIGHT, MIN_TRAIN_SHARPE,
                               RISK_PER_FAMILY, _daily, _sharpe, simulate_all)
from src.execution.config import parse_param_key


def select_on_full_history(all_trades: dict, days: pd.DatetimeIndex,
                           min_trades: int = 60) -> dict:
    """Melhor parametro por familia + pesos por paridade de risco."""
    picks = {}
    for fam, per_param in all_trades.items():
        best = (None, -np.inf, np.nan, 0)
        for key, tr in per_param.items():
            if len(tr) < min_trades:
                continue
            dp = _daily(tr, days)
            sh = _sharpe(dp)
            if sh > best[1]:
                best = (key, sh, float(dp.std(ddof=1)), len(tr))
        if best[0] is not None and best[1] >= MIN_TRAIN_SHARPE and best[2] > 0:
            picks[fam] = {"key": best[0], "sharpe": best[1], "vol": best[2],
                          "n_trades": best[3]}
    if not picks:
        return {}
    inv = {f: 1.0 / v["vol"] for f, v in picks.items()}
    tot = sum(inv.values())
    w = {f: min(x / tot, MAX_FAMILY_WEIGHT) for f, x in inv.items()}
    s = sum(w.values())
    for f in picks:
        picks[f]["weight"] = w[f] / s
    return picks


def build_legs(symbol: str, timeframe: str, total_risk: float) -> list[dict]:
    all_trades, days, _ = simulate_all(symbol, timeframe=timeframe)
    picks = select_on_full_history(all_trades, days)
    legs = []
    for fam, v in picks.items():
        family, params = parse_param_key(v["key"])
        legs.append({"symbol": symbol, "family": family, "params": params,
                     "timeframe": timeframe,
                     "risk_brl": round(total_risk * v["weight"], 2),
                     "sharpe_treino": round(v["sharpe"], 3),
                     "n_trades_treino": v["n_trades"],
                     "peso": round(v["weight"], 3),
                     "chave": v["key"]})
    return legs

"""Combinacao de estrategias aprovadas em uma carteira e alocacao de risco.

Principios:
  * o risco e orcado em REAIS POR TRADE, nao em quantidade -- a quantidade sai
    da distancia do stop, entao volatilidade alta reduz o lote automaticamente;
  * paridade de risco por volatilidade OOS, com teto por perna, para nao deixar
    uma estrategia dominar a carteira;
  * teto agregado de risco simultaneo: e o numero que realmente limita o dia
    ruim, mais do que qualquer stop individual;
  * correlacao entre pernas e medida no PnL diario OOS e usada para cortar
    redundancia (duas pernas com corr > limite -> fica a de maior DSR).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Leg:
    symbol: str
    family: str
    params: dict
    risk_brl: float
    sharpe: float
    dsr: float

    def to_dict(self):
        return asdict(self)


def daily_matrix(oos_trades: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    """PnL diario OOS de cada perna, alinhado em uma matriz dias x pernas."""
    series = {}
    for key, tr in oos_trades.items():
        if tr is None or tr.empty:
            continue
        s = tr.groupby(pd.to_datetime(tr["exit_time"]).dt.normalize())["net_brl"].sum()
        series[f"{key[0]}:{key[1]}"] = s
    if not series:
        return pd.DataFrame()
    m = pd.DataFrame(series).sort_index()
    return m.fillna(0.0)


def drop_redundant(m: pd.DataFrame, scores: dict[str, float],
                   max_corr: float = 0.7) -> list[str]:
    """Mantem, entre pares muito correlacionados, apenas o de maior score."""
    if m.shape[1] <= 1:
        return list(m.columns)
    corr = m.corr().fillna(0.0)
    order = sorted(m.columns, key=lambda c: -scores.get(c, 0.0))
    keep: list[str] = []
    for c in order:
        if all(abs(corr.loc[c, k]) <= max_corr for k in keep):
            keep.append(c)
    return keep


def allocate(selected: pd.DataFrame, oos_trades: dict,
             total_risk_brl: float = 1500.0,
             max_leg_frac: float = 0.35,
             max_corr: float = 0.7) -> tuple[list[Leg], pd.DataFrame]:
    """`selected`: linhas do sweep aprovadas. Devolve as pernas com risco por trade."""
    if selected.empty:
        return [], pd.DataFrame()

    m = daily_matrix(oos_trades)
    scores = {f"{r.symbol}:{r.family}": float(r.dsr) for r in selected.itertuples()}
    keep = drop_redundant(m, scores, max_corr=max_corr) if not m.empty else \
        [f"{r.symbol}:{r.family}" for r in selected.itertuples()]

    sel = selected[selected.apply(lambda r: f"{r['symbol']}:{r['family']}" in keep, axis=1)].copy()
    if sel.empty:
        return [], m

    # paridade de risco: peso ~ 1/vol do PnL diario OOS
    vols = {}
    for r in sel.itertuples():
        col = f"{r.symbol}:{r.family}"
        v = m[col].std(ddof=1) if col in m.columns else np.nan
        vols[col] = v if np.isfinite(v) and v > 0 else np.nan
    inv = {k: (1.0 / v if np.isfinite(v) else 0.0) for k, v in vols.items()}
    tot = sum(inv.values())
    if tot <= 0:
        w = {k: 1.0 / len(inv) for k in inv}
    else:
        w = {k: v / tot for k, v in inv.items()}
    # teto por perna e renormalizacao
    w = {k: min(v, max_leg_frac) for k, v in w.items()}
    s = sum(w.values())
    w = {k: v / s for k, v in w.items()}

    legs = []
    for r in sel.itertuples():
        col = f"{r.symbol}:{r.family}"
        legs.append(Leg(symbol=r.symbol, family=r.family,
                        params=getattr(r, "params_escolhidos", {}) or {},
                        risk_brl=round(total_risk_brl * w[col], 2),
                        sharpe=float(r.sharpe), dsr=float(r.dsr)))
    return legs, m[list(w)] if not m.empty else m


def portfolio_stats(m: pd.DataFrame, weights: dict[str, float] | None = None) -> dict:
    """Metricas da carteira somada (pesos em fracao do risco total)."""
    if m.empty:
        return {}
    if weights is None:
        agg = m.sum(axis=1)
    else:
        agg = sum(m[c] * weights.get(c, 0.0) for c in m.columns)
    sd = agg.std(ddof=1)
    sharpe = float(agg.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    eq = agg.cumsum()
    dd = float((eq - eq.cummax()).min())
    return {"n_days": len(agg), "total_brl": float(agg.sum()),
            "sharpe": sharpe, "max_dd_brl": dd,
            "ret_over_dd": float(agg.sum() / abs(dd)) if dd < 0 else np.inf,
            "pct_dias_positivos": float((agg > 0).mean())}

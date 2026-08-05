"""Teste de Monte Carlo com o PIPELINE INTEIRO dentro de cada replica.

Por que nao basta o DSR aqui: a formula do Deflated Sharpe supoe que a
dispersao dos Sharpes entre tentativas e ruido amostral. Na varredura por
ativo, boa parte da dispersao e DETERMINISTICA -- acoes perdem porque a
friccao relativa e alta, nao porque tiveram azar. Usar essa dispersao como
"ruido" super-deflaciona e reprovaria ate uma estrategia genuina.

O teste correto e empirico: gerar a distribuicao nula do resultado FINAL,
rodando a mesma busca completa (6 familias x grade inteira x escolha por
walk-forward x combinacao em carteira) sobre um mundo sem sinal.

Hipotese nula usada: "a direcao das entradas nao carrega informacao".
Cada replica embaralha o LADO de cada entrada (mantendo a proporcao de compras
observada), preservando tudo o mais -- quantidade de trades, horario, tamanho
do stop, regra de saida, custo. Se o resultado observado nao se destacar dessa
distribuicao, nao ha evidencia de direcao previsivel.

Como a selecao de parametros e de familias acontece DENTRO de cada replica, a
distribuicao nula ja embute o ganho por garimpo -- e exatamente a correcao de
multiplas tentativas que se quer.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest import features
from src.backtest.engine import simulate
from src.core import instruments
from src.ingest.bars import load_bars
from src.strategies import families
from research.sweep import qty_for
from research.win_deep import RISK_PER_FAMILY, portfolio_walk_forward


def _prepare(symbol: str, timeframe: str, cost_mode: str = "base"):
    raw = load_bars(symbol, timeframe)
    ss, se = ("09:00", "18:20") if symbol.startswith(("WIN", "WDO")) else ("10:00", "17:55")
    d = features.build(raw, session_start=ss, session_end=se)
    inst = instruments.get(symbol)
    if cost_mode == "brutal":
        inst = instruments.brutal(inst)
    return d, inst


def _intents_cache(d: pd.DataFrame, inst, risk_brl: float):
    """Calcula uma unica vez as intencoes de toda a grade (parte cara e deterministica)."""
    cache = {}
    for strat in families.ALL:
        per = {}
        for p in strat.grid():
            p = dict(p)
            ii = strat.intents(d, p)
            if len(ii.bar_idx) < 20:
                continue
            q = qty_for(inst.symbol, d, risk_brl, ii.stop_dist, ii.bar_idx, inst)
            keep = q > 0
            if keep.sum() < 20:
                continue
            per[strat.key(p)] = (
                ii.bar_idx[keep], ii.side[keep], ii.stop_dist[keep],
                ii.target_dist[keep], ii.exit_idx[keep], q[keep])
        if per:
            cache[strat.name] = per
    return cache


def _run_pipeline(d, inst, cache, days, seed: int | None) -> float:
    """Roda a busca completa; `seed=None` = mundo real, senao = replica nula."""
    from src.backtest.engine import EntryIntent
    rng = np.random.default_rng(seed) if seed is not None else None

    all_trades = {}
    for fam, per in cache.items():
        out = {}
        for key, (bi, side, sd, td, ei, q) in per.items():
            s = side
            if rng is not None:
                p_long = float((side > 0).mean())
                s = np.where(rng.random(len(side)) < p_long, 1, -1).astype("int64")
            ii = EntryIntent(bi, s, sd, td, ei)
            tr = simulate(d, ii, inst, qty=q)
            if len(tr) >= 20:
                out[key] = tr
        if out:
            all_trades[fam] = out
    if not all_trades:
        return np.nan
    res = portfolio_walk_forward(all_trades, days)
    oos = res["oos_daily"]
    if oos.empty or oos.std(ddof=1) == 0:
        return np.nan
    return float(oos.mean() / oos.std(ddof=1) * np.sqrt(252))


def _worker(args):
    """Um worker cuida de um LOTE de sementes: features e intencoes sao
    calculadas uma unica vez e reaproveitadas em todas as replicas do lote."""
    symbol, timeframe, cost_mode, seeds = args
    d, inst = _prepare(symbol, timeframe, cost_mode)
    cache = _intents_cache(d, inst, RISK_PER_FAMILY)
    days = pd.DatetimeIndex(sorted(set(d["date"])))
    return [_run_pipeline(d, inst, cache, days, s) for s in seeds]

"""Walk-forward ancorado com selecao de parametros dentro da amostra.

Como as estrategias deste projeto sao regras fixas (nenhum parametro e ajustado
dentro do backtest), calcular a lista de trades UMA vez sobre todo o historico
e depois fatiar por data e matematicamente identico a re-rodar por janela --
so que ordens de magnitude mais rapido. O que NAO pode ser fatiado depois e a
ESCOLHA do parametro: essa acontece so com dados de treino, dobra a dobra.

Como nao ha posicao overnight, um trade nunca cruza a fronteira de duas dobras,
o que dispensa purge explicito. O embargo ainda e aplicado em dias para conter
autocorrelacao de curto prazo entre dobras vizinhas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Fold:
    idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def anchored_folds(days: pd.DatetimeIndex, n_folds: int = 5,
                   min_train_frac: float = 0.40, embargo_days: int = 2) -> list[Fold]:
    days = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(days).normalize())))
    n = len(days)
    if n < 100:
        raise ValueError(f"historico curto demais para walk-forward: {n} dias")
    start_test = int(n * min_train_frac)
    edges = np.linspace(start_test, n, n_folds + 1).astype(int)
    folds = []
    for k in range(n_folds):
        a, b = edges[k], edges[k + 1]
        if b - a < 5:
            continue
        tr_end = a - embargo_days - 1
        if tr_end <= 10:
            continue
        folds.append(Fold(idx=k,
                          train_start=days[0], train_end=days[tr_end],
                          test_start=days[a], test_end=days[b - 1]))
    return folds


def slice_trades(trades: pd.DataFrame, start, end) -> pd.DataFrame:
    if trades.empty:
        return trades
    t = pd.to_datetime(trades["entry_time"])
    m = (t >= pd.Timestamp(start)) & (t <= pd.Timestamp(end) + pd.Timedelta(days=1))
    return trades.loc[m]


@dataclass
class WFResult:
    oos_trades: pd.DataFrame
    chosen: list[dict] = field(default_factory=list)
    n_trials: int = 0
    trial_sharpes: np.ndarray | None = None


def _daily(trades: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    if trades.empty:
        return pd.Series(0.0, index=days)
    s = trades.groupby(pd.to_datetime(trades["exit_time"]).dt.normalize())["net_brl"].sum()
    return s.reindex(days, fill_value=0.0)


def _sharpe(x: pd.Series) -> float:
    if len(x) < 5 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252))


def walk_forward(trades_by_param: dict, days: pd.DatetimeIndex,
                 n_folds: int = 5, min_train_frac: float = 0.40,
                 embargo_days: int = 2, min_train_trades: int = 30,
                 score=None) -> WFResult:
    """`trades_by_param`: {chave_do_parametro: DataFrame de trades (historico inteiro)}.

    Em cada dobra escolhe a chave com melhor score no treino e guarda os
    trades dessa chave APENAS no teste.
    """
    days = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(days).normalize())))
    folds = anchored_folds(days, n_folds, min_train_frac, embargo_days)
    score = score or _sharpe

    oos_parts, chosen = [], []
    for f in folds:
        tr_days = days[(days >= f.train_start) & (days <= f.train_end)]
        best_key, best_val = None, -np.inf
        for key, tr in trades_by_param.items():
            sub = slice_trades(tr, f.train_start, f.train_end)
            if len(sub) < min_train_trades:
                continue
            val = score(_daily(sub, tr_days))
            if val > best_val:
                best_key, best_val = key, val
        if best_key is None:
            continue
        te = slice_trades(trades_by_param[best_key], f.test_start, f.test_end)
        chosen.append({"fold": f.idx, "param": best_key, "train_score": best_val,
                       "train_end": f.train_end, "test_start": f.test_start,
                       "test_end": f.test_end, "n_test_trades": len(te)})
        if not te.empty:
            oos_parts.append(te)

    oos = pd.concat(oos_parts).sort_values("entry_time") if oos_parts else pd.DataFrame()

    # Sharpes de todas as tentativas no periodo OOS agregado -- alimenta o DSR.
    if oos_parts:
        oos_days = days[(days >= folds[0].test_start)]
        trial_sharpes = np.array([
            _sharpe(_daily(slice_trades(tr, folds[0].test_start, days[-1]), oos_days))
            for tr in trades_by_param.values()
        ])
    else:
        trial_sharpes = np.array([])

    return WFResult(oos_trades=oos, chosen=chosen,
                    n_trials=len(trades_by_param), trial_sharpes=trial_sharpes)

"""Pesquisa aprofundada no instrumento que sobreviveu a varredura: o mini indice.

Contexto do resultado da varredura ampla (scripts/03):
  * em acoes, TODAS as familias morrem na friccao -- Sharpe OOS mediano entre
    -1,6 e -3,1, com apenas ~3% dos ativos positivos;
  * o WIN e o unico ativo positivo em varias familias ao mesmo tempo, o que faz
    sentido economico: 1 tick = R$1,00 por contrato contra uma amplitude diaria
    tipica de 1.500+ pontos (R$300). A friccao relativa e uma ordem de grandeza
    menor que em acao.

Aqui a hipotese testada nao e "achar o melhor parametro", e sim:
    "uma CARTEIRA de familias independentes no WIN, com parametros escolhidos
     so com dados de treino, entrega Sharpe positivo fora da amostra?"

A composicao da carteira tambem e decidida dobra a dobra, com dados de treino:
entra a familia cujo Sharpe de treino passa do corte, com peso por paridade de
risco. Nenhuma familia e escolhida a dedo depois de ver o resultado.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest import features, metrics
from src.backtest.engine import simulate
from src.core import instruments, paths
from src.ingest.bars import load_bars
from src.strategies import families
from src.validation import stats, walkforward
from research.sweep import qty_for

RISK_PER_FAMILY = 300.0
TOTAL_RISK = 900.0
MIN_TRAIN_SHARPE = 0.30
MAX_FAMILY_WEIGHT = 0.50


def simulate_all(symbol: str, timeframe: str = "M5", cost_mode: str = "base",
                 risk_brl: float = RISK_PER_FAMILY):
    """{familia: {chave_param: trades}} para todas as combinacoes."""
    raw = load_bars(symbol, timeframe)
    if raw.empty:
        raise SystemExit(f"sem dados de {symbol} {timeframe}")
    ss, se = ("09:00", "18:20") if symbol.startswith(("WIN", "WDO")) else ("10:00", "17:55")
    d = features.build(raw, session_start=ss, session_end=se)

    inst = instruments.get(symbol)
    if cost_mode == "brutal":
        inst = instruments.brutal(inst)
    elif cost_mode == "otimista":
        inst = instruments.optimistic(inst)

    out = {}
    for strat in families.ALL:
        per_param = {}
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
            if len(tr) >= 20:
                per_param[strat.key(p)] = tr
        if per_param:
            out[strat.name] = per_param
    days = pd.DatetimeIndex(sorted(set(d["date"])))
    return out, days, d


def _daily(tr: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    if tr is None or tr.empty:
        return pd.Series(0.0, index=days)
    s = tr.groupby(pd.to_datetime(tr["exit_time"]).dt.normalize())["net_brl"].sum()
    return s.reindex(days, fill_value=0.0)


def _sharpe(x: pd.Series) -> float:
    if len(x) < 5 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252))


def portfolio_walk_forward(all_trades: dict, days: pd.DatetimeIndex,
                           n_folds: int = 6, min_train_frac: float = 0.35,
                           min_train_trades: int = 40) -> dict:
    """Carteira de familias, com selecao e pesos definidos so no treino."""
    folds = walkforward.anchored_folds(days, n_folds=n_folds,
                                       min_train_frac=min_train_frac, embargo_days=2)
    oos_parts, log_rows = [], []

    for f in folds:
        tr_days = days[(days >= f.train_start) & (days <= f.train_end)]
        te_days = days[(days >= f.test_start) & (days <= f.test_end)]
        picks = {}
        for fam, per_param in all_trades.items():
            best_key, best_sh, best_vol = None, -np.inf, np.nan
            for key, tr in per_param.items():
                sub = walkforward.slice_trades(tr, f.train_start, f.train_end)
                if len(sub) < min_train_trades:
                    continue
                dp = _daily(sub, tr_days)
                sh = _sharpe(dp)
                if sh > best_sh:
                    best_key, best_sh, best_vol = key, sh, float(dp.std(ddof=1))
            if best_key is not None and best_sh >= MIN_TRAIN_SHARPE and best_vol > 0:
                picks[fam] = (best_key, best_sh, best_vol)

        if not picks:
            log_rows.append({"fold": f.idx, "test_start": f.test_start,
                             "familias": "", "peso": ""})
            continue

        inv = {fam: 1.0 / v[2] for fam, v in picks.items()}
        tot = sum(inv.values())
        w = {fam: min(x / tot, MAX_FAMILY_WEIGHT) for fam, x in inv.items()}
        s = sum(w.values())
        w = {fam: x / s for fam, x in w.items()}

        agg = pd.Series(0.0, index=te_days)
        for fam, (key, sh, vol) in picks.items():
            te = walkforward.slice_trades(all_trades[fam][key], f.test_start, f.test_end)
            scale = (TOTAL_RISK * w[fam]) / RISK_PER_FAMILY
            agg = agg.add(_daily(te, te_days) * scale, fill_value=0.0)
        oos_parts.append(agg)
        log_rows.append({
            "fold": f.idx, "test_start": f.test_start, "test_end": f.test_end,
            "familias": ", ".join(f"{k}({w[k]:.2f})" for k in sorted(w)),
            "params": " || ".join(f"{picks[k][0]}" for k in sorted(picks)),
            "train_sharpe": " ".join(f"{k}={picks[k][1]:.2f}" for k in sorted(picks)),
        })

    oos = pd.concat(oos_parts).sort_index() if oos_parts else pd.Series(dtype=float)
    return {"oos_daily": oos, "folds": pd.DataFrame(log_rows)}


def report(oos: pd.Series, n_trials: int, var_trials: float, titulo: str) -> dict:
    if oos.empty:
        print(f"\n### {titulo}: sem resultado OOS")
        return {}
    eq = oos.cumsum()
    dd = float((eq - eq.cummax()).min())
    ds = stats.deflated_sharpe(oos.to_numpy(), n_trials=n_trials, var_trials=var_trials)
    lo, hi = stats.bootstrap_ci(oos.to_numpy())
    ano = oos.groupby(oos.index.year).agg(
        dias="size", pnl="sum",
        sharpe=lambda x: x.mean() / x.std(ddof=1) * np.sqrt(252) if x.std(ddof=1) > 0 else 0.0)
    print(f"\n### {titulo}")
    print(f"  dias OOS ............ {len(oos)}  ({oos.index[0].date()} -> {oos.index[-1].date()})")
    print(f"  PnL liquido ......... R$ {oos.sum():,.2f}")
    print(f"  media/dia ........... R$ {oos.mean():,.2f}   (IC95%: {lo:,.2f} a {hi:,.2f})")
    print(f"  Sharpe anualizado ... {ds['sharpe_annual']:.3f}")
    print(f"  max drawdown ........ R$ {dd:,.2f}   retorno/DD = {oos.sum()/abs(dd):.2f}" if dd < 0 else "")
    print(f"  dias positivos ...... {100*(oos>0).mean():.1f}%")
    print(f"  PSR (vs 0) .......... {ds['psr_vs_zero']:.4f}")
    print(f"  tentativas contadas . {n_trials:,}  ->  limiar SR0 = {ds['sr0_annual']:.3f} anual")
    print(f"  DSR ................. {ds['dsr']:.4f}   {'APROVADO' if ds['dsr'] >= 0.95 else 'nao passa o corte de 0,95'}")
    print("  por ano:")
    print("    " + ano.to_string().replace("\n", "\n    "))
    return {"sharpe": ds["sharpe_annual"], "dsr": ds["dsr"], "pnl": float(oos.sum()),
            "max_dd": dd, "n_days": len(oos)}

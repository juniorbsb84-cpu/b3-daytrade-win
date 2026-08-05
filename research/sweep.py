"""Varredura paralela de hipoteses com validacao walk-forward + DSR.

Fluxo por (simbolo, familia):
  1. calcula as features M5 uma vez;
  2. simula TODAS as combinacoes da grade sobre o historico inteiro;
  3. walk-forward ancorado: em cada dobra escolhe a combinacao pelo Sharpe de
     TREINO e guarda so os trades de TESTE;
  4. mede o resultado OOS concatenado e aplica o Deflated Sharpe usando como
     numero de tentativas o tamanho da grade.

Nada aqui reporta resultado in-sample como se fosse desempenho esperado.
"""
from __future__ import annotations

import sys
import traceback
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

OOS_DIR = paths.RESULTS / "oos"
OOS_DIR.mkdir(parents=True, exist_ok=True)

FUTURES = {"WIN$N", "WDO$N"}


def session_for(symbol: str) -> tuple[str, str]:
    return ("09:00", "18:20") if symbol in FUTURES else ("10:00", "17:55")


def qty_for(symbol: str, d: pd.DataFrame, risk_brl: float, stop_dist: np.ndarray,
            idx: np.ndarray, inst) -> np.ndarray:
    """Tamanho por risco constante: risco_alvo / (distancia_do_stop x valor_do_ponto).

    Isso normaliza a volatilidade entre ativos e no tempo -- sem isso, o Sharpe
    do backtest mede sobretudo variacao de volatilidade, nao qualidade do sinal.
    """
    per_unit = stop_dist * inst.point_value
    per_unit = np.where(per_unit > 0, per_unit, np.nan)
    q = risk_brl / per_unit
    if inst.kind == "stock":
        q = np.floor(q / 100.0) * 100.0
    else:
        q = np.floor(q)
    return np.clip(np.nan_to_num(q, nan=0.0), 0, None)


def run_symbol(symbol: str, risk_brl: float = 300.0, n_folds: int = 5,
               min_train_frac: float = 0.40, save_oos: bool = True) -> list[dict]:
    raw = load_bars(symbol, "M5")
    if raw.empty or len(raw) < 20_000:
        return [{"symbol": symbol, "family": "-", "erro": "sem dados"}]

    ss, se = session_for(symbol)
    d = features.build(raw, session_start=ss, session_end=se)
    if len(d) < 20_000:
        return [{"symbol": symbol, "family": "-", "erro": "sessao curta"}]

    inst = instruments.get(symbol)
    days = pd.DatetimeIndex(sorted(set(d["date"])))
    out = []

    for strat in families.ALL:
        try:
            g = strat.grid()
            trades_by_param = {}
            for p in g:
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
                trades_by_param[strat.key(p)] = tr

            if len(trades_by_param) < 2:
                out.append({"symbol": symbol, "family": strat.name,
                            "erro": "grade vazia", "n_trials": len(g)})
                continue

            wf = walkforward.walk_forward(trades_by_param, days, n_folds=n_folds,
                                          min_train_frac=min_train_frac)
            if wf.oos_trades.empty:
                out.append({"symbol": symbol, "family": strat.name,
                            "erro": "sem trades OOS", "n_trials": len(g)})
                continue

            first_test = min(c["test_start"] for c in wf.chosen)
            oos_days = days[days >= first_test]
            dp = metrics.daily_pnl(wf.oos_trades, oos_days)
            s = metrics.summarize(wf.oos_trades, oos_days, label=f"{symbol}:{strat.name}")
            ds = stats.deflated_sharpe(dp.to_numpy(), n_trials=len(g),
                                       trial_sharpes=wf.trial_sharpes / np.sqrt(252))
            rec = {"symbol": symbol, "family": strat.name, "erro": "",
                   "n_trials": len(g), "n_valid": len(trades_by_param),
                   **{k: v for k, v in s.items() if k != "label"},
                   "dsr": ds["dsr"], "psr": ds["psr_vs_zero"],
                   "sr0_annual": ds["sr0_annual"],
                   "oos_start": first_test,
                   "params_por_dobra": " || ".join(str(c["param"]) for c in wf.chosen)}
            out.append(rec)

            if save_oos:
                safe = symbol.replace("$", "_")
                wf.oos_trades.to_parquet(OOS_DIR / f"{safe}__{strat.name}.parquet")
        except Exception:  # noqa: BLE001
            out.append({"symbol": symbol, "family": strat.name,
                        "erro": traceback.format_exc(limit=3)})
    return out


def _worker(args):
    symbol, risk_brl, n_folds = args
    try:
        return run_symbol(symbol, risk_brl=risk_brl, n_folds=n_folds)
    except Exception:  # noqa: BLE001
        return [{"symbol": symbol, "family": "-", "erro": traceback.format_exc(limit=3)}]

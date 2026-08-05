"""Passo 4: carteira de familias no mini indice, com deflacao estatistica honesta.

Contagem de tentativas usada no DSR: TODAS as combinacoes avaliadas em toda a
pesquisa -- 6 familias x grade x 61 ativos. Nao e a contagem que favorece o
resultado, e a que corresponde ao que de fato foi vasculhado antes de o WIN ser
escolhido.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from research.win_deep import (portfolio_walk_forward, report, simulate_all,
                               _daily, _sharpe)  # noqa: E402
from src.core import paths  # noqa: E402
from src.strategies import families  # noqa: E402

GRID_TOTAL = sum(len(s.grid()) for s in families.ALL)


def trial_variance(all_trades, days, oos_start) -> tuple[float, int]:
    """Variancia dos Sharpes DIARIOS de todas as tentativas no periodo OOS."""
    oos_days = days[days >= oos_start]
    shs = []
    for fam, per in all_trades.items():
        for key, tr in per.items():
            sub = tr[pd.to_datetime(tr["entry_time"]) >= oos_start]
            dp = _daily(sub, oos_days)
            shs.append(_sharpe(dp) / np.sqrt(252))   # por observacao
    shs = np.array([s for s in shs if np.isfinite(s)])
    return (float(np.var(shs, ddof=1)) if len(shs) > 2 else 0.0), len(shs)


def main():
    resumo = {}
    for symbol in ("WIN$N", "WDO$N"):
        for tf in ("M5", "M15"):
            print("\n" + "=" * 78)
            print(f"  {symbol}  {tf}   (custo pessimista padrao)")
            print("=" * 78)
            all_trades, days, _ = simulate_all(symbol, timeframe=tf)
            if not all_trades:
                print("  nenhuma familia gerou trades")
                continue
            res = portfolio_walk_forward(all_trades, days)
            oos = res["oos_daily"]
            if oos.empty:
                print("  sem OOS")
                continue
            var_t, n_local = trial_variance(all_trades, days, oos.index[0])
            n_trials_global = GRID_TOTAL * 61       # tudo que foi vasculhado
            r = report(oos, n_trials_global, var_t,
                       f"{symbol} {tf} -- carteira de familias, custo pessimista")
            print(f"\n  composicao escolhida por dobra (so com dados de treino):")
            for row in res["folds"].to_dict("records"):
                if row.get("familias"):
                    print(f"    dobra {row['fold']} a partir de "
                          f"{pd.Timestamp(row['test_start']).date()}: {row['familias']}")
            resumo[f"{symbol}|{tf}"] = r
            oos.to_frame("pnl").to_parquet(
                paths.RESULTS / f"portfolio_oos_{symbol.replace('$','_')}_{tf}.parquet")
            res["folds"].to_csv(
                paths.RESULTS / f"portfolio_folds_{symbol.replace('$','_')}_{tf}.csv",
                index=False)

    # teste de estresse: custo ainda mais pesado, so no vencedor
    print("\n" + "=" * 78)
    print("  TESTE DE ESTRESSE -- custo 'brutal' (slippage +1 tick por lado, taxas +50%)")
    print("=" * 78)
    for symbol in ("WIN$N",):
        for tf in ("M5", "M15"):
            all_trades, days, _ = simulate_all(symbol, timeframe=tf, cost_mode="brutal")
            if not all_trades:
                continue
            res = portfolio_walk_forward(all_trades, days)
            oos = res["oos_daily"]
            if oos.empty:
                print(f"  {symbol} {tf}: sem OOS")
                continue
            var_t, _ = trial_variance(all_trades, days, oos.index[0])
            report(oos, GRID_TOTAL * 61, var_t, f"{symbol} {tf} -- custo BRUTAL")

    print("\n" + "=" * 78)
    print("  RESUMO")
    print("=" * 78)
    print(pd.DataFrame(resumo).T.to_string())


if __name__ == "__main__":
    main()

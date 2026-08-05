"""Passo 8: M5 e M15 sao a mesma aposta ou duas?

Se as duas series OOS forem pouco correlacionadas, rodar as duas ao mesmo tempo
melhora o Sharpe sem inventar edge novo. Se forem quase a mesma coisa, manter
as duas so dobra o custo e o risco de execucao.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.core import paths  # noqa: E402
from src.validation import stats  # noqa: E402


def carregar(tf: str) -> pd.Series:
    f = paths.RESULTS / f"portfolio_oos_WIN_N_{tf}.parquet"
    return pd.read_parquet(f)["pnl"]


def desc(nome: str, x: pd.Series) -> dict:
    sh = x.mean() / x.std(ddof=1) * np.sqrt(252) if x.std(ddof=1) > 0 else 0.0
    eq = x.cumsum()
    dd = float((eq - eq.cummax()).min())
    lo, hi = stats.bootstrap_ci(x.to_numpy())
    print(f"  {nome:<22} dias={len(x):>4}  PnL=R${x.sum():>10,.0f}  "
          f"Sharpe={sh:>5.2f}  DD=R${dd:>9,.0f}  ret/DD={x.sum()/abs(dd) if dd<0 else np.inf:>5.2f}  "
          f"dias+={100*(x>0).mean():>4.1f}%  IC95 media=[{lo:.1f}, {hi:.1f}]")
    return {"nome": nome, "dias": len(x), "pnl": float(x.sum()), "sharpe": float(sh),
            "dd": dd, "ret_dd": float(x.sum() / abs(dd)) if dd < 0 else np.inf}


def main():
    m5, m15 = carregar("M5"), carregar("M15")
    comum = m5.index.intersection(m15.index)
    print(f"periodo comum: {comum[0].date()} -> {comum[-1].date()} ({len(comum)} dias)")

    a, b = m5.reindex(comum).fillna(0.0), m15.reindex(comum).fillna(0.0)
    corr = float(np.corrcoef(a, b)[0, 1])
    print(f"correlacao diaria entre as duas pernas: {corr:.3f}\n")

    linhas = [desc("WIN M5 sozinho", a), desc("WIN M15 sozinho", b)]
    # metade do risco em cada, para manter o mesmo risco total do caso individual
    comb = 0.5 * a + 0.5 * b
    linhas.append(desc("M5+M15 (50/50)", comb))

    sh_a = a.mean() / a.std(ddof=1) if a.std(ddof=1) > 0 else 0
    sh_c = comb.mean() / comb.std(ddof=1) if comb.std(ddof=1) > 0 else 0
    ganho = (sh_c / sh_a - 1) * 100 if sh_a else 0
    print(f"\n  ganho de Sharpe da combinacao sobre o M5 sozinho: {ganho:+.1f}%")
    if corr > 0.7:
        print("  >>> correlacao alta: as duas pernas sao praticamente a mesma aposta.")
    else:
        print("  >>> correlacao baixa o suficiente para as duas pernas somarem.")

    pd.DataFrame(linhas).to_csv(paths.RESULTS / "combinacao_timeframes.csv", index=False)
    comb.to_frame("pnl").to_parquet(paths.RESULTS / "portfolio_oos_WIN_combinado.parquet")


if __name__ == "__main__":
    main()

"""O ganho 'overnight' do WIN$N e real ou artefato de rolagem do continuo?

A serie continua WIN$N emenda contratos. Como o futuro negocia com premio de
carrego sobre o a vista, cada troca de vencimento cria um SALTO artificial que
aparece exatamente como 'overnight'. O jeito de separar e medir o mesmo efeito
em instrumentos SEM rolagem: o indice a vista (IBOV) e o ETF (BOVA11).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from src.ingest.bars import load_bars

pd.set_option("display.width", 200)


def decompose(sym: str, tf: str = "D1"):
    d = load_bars(sym, tf)
    if d.empty:
        print(f"{sym}: sem dados"); return None
    d = d[["open", "high", "low", "close"]].dropna()
    d = d[d["close"] > 0]
    intr = (d["close"] - d["open"]) / d["open"]
    over = (d["open"] - d["close"].shift(1)) / d["close"].shift(1)
    tot = (d["close"] - d["close"].shift(1)) / d["close"].shift(1)
    df = pd.DataFrame({"intradia": intr, "overnight": over, "total": tot}).dropna()
    n = len(df)
    print(f"\n=== {sym} ({tf}) -- {n} pregoes, {d.index[0].date()} -> {d.index[-1].date()} ===")
    for c in ("intradia", "overnight", "total"):
        x = df[c]
        acum = float((1 + x).prod() - 1)
        t = x.mean() / (x.std(ddof=1) / np.sqrt(n))
        sh = x.mean() / x.std(ddof=1) * np.sqrt(252)
        print(f"  {c:<10} acumulado {100*acum:>8.1f}%   media {1e4*x.mean():>6.2f} bps   "
              f"dp {100*x.std(ddof=1):>5.2f}%   t={t:>5.2f}   Sharpe={sh:>5.2f}")
    return df


for s in ["IBOV", "BOVA11", "WIN$N"]:
    decompose(s)

print("\n=== maiores saltos overnight do WIN$N (candidatos a rolagem) ===")
d = load_bars("WIN$N", "D1")
o = (d["open"] - d["close"].shift(1))
big = o.abs().nlargest(18).sort_index()
print(pd.DataFrame({"salto_pts": o.loc[big.index].round(0),
                    "mes": big.index.month, "dia": big.index.day}).to_string())
meses_par = o.loc[o.abs().nlargest(30).index].index.month
print(f"\ndos 30 maiores saltos, {sum(m % 2 == 0 for m in meses_par)} caem em mes PAR "
      f"(WIN vence em meses pares) -- rolagem cai em fev/abr/jun/ago/out/dez")
print(f"soma dos 30 maiores saltos: {o.loc[o.abs().nlargest(30).index].sum():,.0f} pts "
      f"| soma de TODOS os overnights: {o.sum():,.0f} pts")

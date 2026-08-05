"""Passo 3: varredura paralela de todas as familias sobre todo o universo."""
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from research.sweep import _worker  # noqa: E402
from src.core import paths  # noqa: E402
from src.ingest import universe  # noqa: E402

EXCLUDE = {"IBOV11"}
RISK_BRL = 300.0
N_FOLDS = 5


def main():
    liq = universe.load_liquid()
    stocks = [s for s in liq["symbol"].tolist() if s not in EXCLUDE]
    symbols = ["WIN$N", "WDO$N"] + stocks
    args = [(s, RISK_BRL, N_FOLDS) for s in symbols]

    t0 = time.time()
    rows = []
    with Pool(processes=12) as pool:
        for i, res in enumerate(pool.imap_unordered(_worker, args), 1):
            rows.extend(res)
            sym = res[0]["symbol"] if res else "?"
            print(f"[{i}/{len(args)}] {sym}  ({time.time()-t0:.0f}s)", flush=True)

    df = pd.DataFrame(rows)
    out = paths.RESULTS / "sweep_oos.parquet"
    df.to_parquet(out)
    df.to_csv(paths.RESULTS / "sweep_oos.csv", index=False)
    print(f"\nsalvo em {out}  ({len(df)} linhas, {time.time()-t0:.0f}s)")

    ok = df[df["erro"] == ""].copy()
    print("\n=== por familia (mediana entre ativos, OOS) ===")
    agg = ok.groupby("family").agg(
        n_ativos=("symbol", "nunique"),
        sharpe_med=("sharpe", "median"),
        sharpe_p75=("sharpe", lambda x: x.quantile(0.75)),
        pct_positivo=("net_brl", lambda x: (x > 0).mean()),
        dsr_max=("dsr", "max"),
        n_dsr095=("dsr", lambda x: (x >= 0.95).sum()),
        trades_med=("n_trades", "median"),
    ).sort_values("sharpe_med", ascending=False)
    print(agg.to_string())

    print("\n=== top 25 combinacoes (ativo x familia) por Sharpe OOS ===")
    cols = ["symbol", "family", "n_trades", "net_brl", "avg_net_trade", "sharpe",
            "dsr", "psr", "max_dd_brl", "win_rate", "profit_factor"]
    print(ok.sort_values("sharpe", ascending=False)[cols].head(25).to_string(index=False))


if __name__ == "__main__":
    main()

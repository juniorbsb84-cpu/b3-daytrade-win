"""Passo 5: distribuicao nula do resultado final, com a busca inteira dentro."""
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from research.montecarlo import _worker  # noqa: E402
from src.core import paths  # noqa: E402

N_REPS = 240
N_WORKERS = 12


def run(symbol: str, timeframe: str, cost_mode: str = "base"):
    t0 = time.time()
    obs = _worker((symbol, timeframe, cost_mode, [None]))[0]

    seeds = list(range(1, N_REPS + 1))
    batches = [seeds[i::N_WORKERS] for i in range(N_WORKERS)]
    args = [(symbol, timeframe, cost_mode, b) for b in batches if b]
    null = []
    with Pool(processes=N_WORKERS) as pool:
        for res in pool.imap_unordered(_worker, args):
            null.extend(res)
    null = np.array([x for x in null if np.isfinite(x)])

    p = float((null >= obs).mean()) if len(null) else np.nan
    print(f"\n### {symbol} {timeframe} custo={cost_mode}")
    print(f"  Sharpe OOS observado ........ {obs:.3f}")
    print(f"  replicas nulas validas ...... {len(null)}")
    print(f"  nulo: media {null.mean():.3f} | dp {null.std(ddof=1):.3f} | "
          f"p50 {np.median(null):.3f} | p95 {np.quantile(null, 0.95):.3f} | "
          f"max {null.max():.3f}")
    print(f"  p-valor (P[nulo >= observado]) = {p:.4f}   "
          f"{'SIGNIFICATIVO a 5%' if p < 0.05 else 'nao significativo'}")
    print(f"  ({time.time()-t0:.0f}s)")
    pd.DataFrame({"null_sharpe": null}).to_parquet(
        paths.RESULTS / f"mc_null_{symbol.replace('$','_')}_{timeframe}_{cost_mode}.parquet")
    return {"symbol": symbol, "tf": timeframe, "cost": cost_mode,
            "obs": obs, "p": p, "null_p95": float(np.quantile(null, 0.95)),
            "null_max": float(null.max()), "n_null": len(null)}


if __name__ == "__main__":
    rows = []
    for sym, tf in [("WIN$N", "M5"), ("WIN$N", "M15")]:
        rows.append(run(sym, tf, "base"))
    rows.append(run("WIN$N", "M15", "brutal"))
    df = pd.DataFrame(rows)
    df.to_csv(paths.RESULTS / "montecarlo_summary.csv", index=False)
    print("\n" + df.to_string(index=False))

"""Passo 2: baixar todo o historico disponivel e persistir em parquet."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.core import paths  # noqa: E402
from src.ingest import bars, universe  # noqa: E402

EXCLUDE = {"IBOV11"}  # nivel do indice, nao e instrumento negociavel

if __name__ == "__main__":
    liq = universe.load_liquid()
    stocks = [s for s in liq["symbol"].tolist() if s not in EXCLUDE]

    print(f"=== futuros e indice ===")
    rep1 = bars.download(["WIN$N", "WDO$N", "IBOV"],
                         timeframes=("M5", "M15", "M30", "H1", "D1"))

    print(f"\n=== {len(stocks)} acoes ===")
    rep2 = bars.download(stocks, timeframes=("M5", "D1"))

    rep = pd.concat([rep1, rep2], ignore_index=True)
    rep.to_csv(paths.RAW / "download_report.csv", index=False)
    ok = rep[rep["erro"] == ""]
    print(f"\nOK: {len(ok)}/{len(rep)} series")
    print(ok.groupby("tf")["n"].agg(["count", "sum", "min", "max"]).to_string())
    falhas = rep[rep["erro"] != ""]
    if len(falhas):
        print("\nfalhas:")
        print(falhas.to_string(index=False))

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.core import paths
from src.backtest import metrics

pd.set_option("display.width", 220)
df = pd.read_parquet(paths.RESULTS / "sweep_oos.parquet")
w = df[df["symbol"].isin(["WIN$N", "WDO$N"])]
print(w[["symbol", "family", "n_trades", "net_brl", "sharpe", "dsr", "psr",
         "max_dd_brl", "win_rate", "profit_factor", "pct_stop", "pct_target",
         "pct_time", "cost_frac_of_gross", "oos_start"]].to_string(index=False))

print("\n=== parametros escolhidos por dobra (WIN) ===")
for r in w[w["symbol"] == "WIN$N"].itertuples():
    print(f"\n{r.family}:")
    for p in str(r.params_por_dobra).split(" || "):
        print("   ", p)

print("\n=== quebra por ano, WIN VolBreak (OOS) ===")
t = pd.read_parquet(paths.RESULTS / "oos" / "WIN_N__VolBreak.parquet")
print(metrics.yearly_breakdown(t).to_string())
print("\n=== quebra por ano, WIN ORB (OOS) ===")
t2 = pd.read_parquet(paths.RESULTS / "oos" / "WIN_N__ORB.parquet")
print(metrics.yearly_breakdown(t2).to_string())
print("\n=== quebra por ano, WIN VWAPRevert (OOS) ===")
t3 = pd.read_parquet(paths.RESULTS / "oos" / "WIN_N__VWAPRevert.parquet")
print(metrics.yearly_breakdown(t3).to_string())

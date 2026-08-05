import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import MetaTrader5 as mt5
import numpy as np, pandas as pd
from src.core.mt5session import mt5_session
from src.core import paths
from src.execution.broker import resolve_front

with mt5_session():
    ai = mt5.account_info()
    print("margin_mode:", ai.margin_mode,
          "(0=RETAIL_NETTING, 1=EXCHANGE, 2=RETAIL_HEDGING)")
    print("margin_so_mode:", ai.margin_so_mode, "| limit_orders:", ai.limit_orders)
    for root in ("WIN", "WDO"):
        s = resolve_front(root)
        print(f"contrato vigente {root}: {s}")
        if s:
            i = mt5.symbol_info(s)
            print(f"   margem inicial={i.margin_initial} manutencao={i.margin_maintenance} "
                  f"expira={pd.Timestamp(i.expiration_time, unit='s')} "
                  f"modo_exec={i.trade_exemode} filling={i.filling_mode} "
                  f"stops_level={i.trade_stops_level}")

# pior dia da serie OOS escolhida
x = pd.read_parquet(paths.RESULTS / "portfolio_oos_WIN_N_M15.parquet")["pnl"]
print(f"\nserie OOS M15 (risco total R$900/trade), {len(x)} dias:")
for q in (0.001, 0.01, 0.05, 0.5, 0.95, 0.99):
    print(f"   quantil {q:>5}: R$ {x.quantile(q):>9,.2f}")
print(f"   pior dia: R$ {x.min():,.2f} | melhor: R$ {x.max():,.2f}")
print(f"   dias abaixo de -900: {(x < -900).sum()} ({100*(x<-900).mean():.1f}%)")
print(f"   dias abaixo de -1500: {(x < -1500).sum()} ({100*(x<-1500).mean():.1f}%)")
print(f"   dias abaixo de -2500: {(x < -2500).sum()} ({100*(x<-2500).mean():.1f}%)")

"""Relatorio diario: PnL, trades e conferencia de custo realizado vs assumido."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.monitor.report import daily_report  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    a = ap.parse_args()
    print(daily_report(days_back=a.days))

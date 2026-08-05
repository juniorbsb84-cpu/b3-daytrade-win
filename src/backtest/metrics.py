"""Metricas de desempenho a partir da lista de trades."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def daily_pnl(trades: pd.DataFrame, all_days: pd.DatetimeIndex | None = None) -> pd.Series:
    """PnL liquido agregado por dia (dias sem trade contam como zero)."""
    if trades.empty:
        return pd.Series(dtype="float64")
    s = trades.groupby(pd.to_datetime(trades["exit_time"]).dt.normalize())["net_brl"].sum()
    if all_days is not None:
        s = s.reindex(pd.DatetimeIndex(all_days).normalize().unique(), fill_value=0.0)
    return s.sort_index()


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity - peak))


def summarize(trades: pd.DataFrame, all_days: pd.DatetimeIndex | None = None,
              label: str = "") -> dict:
    if trades is None or trades.empty:
        return {"label": label, "n_trades": 0, "net_brl": 0.0, "sharpe": 0.0,
                "dsr_ready": False}

    net = trades["net_brl"].to_numpy()
    gross = trades["gross_brl"].to_numpy()
    cost = trades["cost_brl"].to_numpy()

    dp = daily_pnl(trades, all_days)
    n_days = max(len(dp), 1)
    mu, sd = float(dp.mean()), float(dp.std(ddof=1)) if len(dp) > 1 else 0.0
    sharpe = (mu / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0
    downside = dp[dp < 0]
    dsd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mu / dsd * np.sqrt(TRADING_DAYS)) if dsd > 0 else 0.0

    equity = dp.cumsum().to_numpy()
    mdd = max_drawdown(equity)

    wins = net[net > 0]
    losses = net[net < 0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.inf

    t_stat = (net.mean() / (net.std(ddof=1) / np.sqrt(len(net)))) if len(net) > 1 and net.std(ddof=1) > 0 else 0.0

    return {
        "label": label,
        "n_trades": int(len(trades)),
        "n_days": int(n_days),
        "trades_per_day": len(trades) / n_days,
        "net_brl": float(net.sum()),
        "gross_brl": float(gross.sum()),
        "cost_brl": float(cost.sum()),
        "cost_frac_of_gross": float(cost.sum() / abs(gross.sum())) if gross.sum() != 0 else np.nan,
        "avg_net_trade": float(net.mean()),
        "median_net_trade": float(np.median(net)),
        "win_rate": float((net > 0).mean()),
        "profit_factor": float(pf),
        "t_stat_trade": float(t_stat),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_dd_brl": float(mdd),
        "ret_over_dd": float(net.sum() / abs(mdd)) if mdd < 0 else np.inf,
        "avg_bars_held": float(trades["bars_held"].mean()),
        "pct_stop": float((trades["reason"].str.startswith("stop")).mean()),
        "pct_target": float((trades["reason"] == "target").mean()),
        "pct_time": float((trades["reason"] == "time").mean()),
        "first": trades["entry_time"].min(),
        "last": trades["entry_time"].max(),
        "dsr_ready": True,
    }


def yearly_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    """Quebra por ano -- serve para detectar estrategia que so funciona num regime."""
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["year"] = pd.to_datetime(t["exit_time"]).dt.year
    g = t.groupby("year").agg(
        n=("net_brl", "size"),
        net=("net_brl", "sum"),
        avg=("net_brl", "mean"),
        win_rate=("net_brl", lambda x: (x > 0).mean()),
    )
    dp = t.groupby(["year", pd.to_datetime(t["exit_time"]).dt.normalize()])["net_brl"].sum()
    sh = dp.groupby(level=0).apply(
        lambda x: x.mean() / x.std(ddof=1) * np.sqrt(TRADING_DAYS) if x.std(ddof=1) > 0 else 0.0)
    g["sharpe"] = sh
    return g

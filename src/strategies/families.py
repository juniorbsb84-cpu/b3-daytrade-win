"""Familias de hipoteses testadas na varredura.

Cada familia e uma IDEIA economica diferente, nao uma variacao cosmetica da
mesma coisa. A grade de cada uma e deliberadamente pequena: o custo estatistico
de cada tentativa extra e pago no DSR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.base import Strategy, grid


def _nan_to_zero_side(cond_long: np.ndarray, cond_short: np.ndarray) -> np.ndarray:
    side = np.zeros(len(cond_long), dtype="int64")
    side[np.nan_to_num(cond_long, nan=0).astype(bool)] = 1
    side[np.nan_to_num(cond_short, nan=0).astype(bool)] = -1
    return side


def _trend_filter(d: pd.DataFrame, mode: str) -> tuple[np.ndarray, np.ndarray]:
    """Devolve (permite_long, permite_short)."""
    if mode == "none":
        ones = np.ones(len(d), dtype=bool)
        return ones, ones
    if mode == "d20":
        t = d["trend20"].to_numpy()
    elif mode == "d50":
        t = d["trend50"].to_numpy()
    elif mode == "d5":
        t = d["trend5"].to_numpy()
    elif mode == "vwap":
        t = np.sign(d["close"].to_numpy() - d["vwap"].to_numpy())
    else:
        raise ValueError(mode)
    return (t > 0), (t < 0)


# ---------------------------------------------------------------- ORB --------

class ORB(Strategy):
    """Rompimento do range de abertura.

    Ideia: o range formado nos primeiros minutos define o equilibrio inicial;
    romper esse equilibrio com convicao tende a atrair fluxo na mesma direcao.
    """

    def __init__(self):
        super().__init__(name="ORB")

    def grid(self):
        return grid(or_min=[15, 30, 60],
                    buf_atr=[0.0, 0.25],
                    stop_mode=["or", "atr2"],
                    rr=[0.0, 1.5, 3.0],
                    trend=["none", "d20", "vwap"],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60],
                    max_per_day=[1])

    def signals(self, d, p):
        k = p["or_min"]
        hi = d[f"or{k}_high"].to_numpy()
        lo = d[f"or{k}_low"].to_numpy()
        size = hi - lo
        atr = d["atr"].to_numpy()
        c = d["close"].to_numpy()
        buf = p["buf_atr"] * atr

        long_ok, short_ok = _trend_filter(d, p["trend"])
        cl = (c > hi + buf) & long_ok
        cs = (c < lo - buf) & short_ok
        side = _nan_to_zero_side(cl, cs)

        if p["stop_mode"] == "or":
            stop = np.where(side > 0, c - lo, hi - c)
            stop = np.maximum(stop, 0.4 * size)      # piso: nao aceita stop absurdo
        else:
            stop = 2.0 * atr
        tgt = np.where(p["rr"] > 0, stop * p["rr"], np.nan)
        return side, stop, tgt


# ------------------------------------------------------- volatility break ----

class VolBreak(Strategy):
    """Rompimento da abertura por k x range do dia anterior (Larry Williams).

    Ideia: uma extensao alem de uma fracao da amplitude tipica indica que o dia
    'escolheu lado'.
    """

    def __init__(self):
        super().__init__(name="VolBreak")

    def grid(self):
        return grid(k=[0.25, 0.5, 0.8],
                    stop_atr=[1.5, 2.5],
                    rr=[0.0, 2.0],
                    trend=["none", "d20"],
                    start_min=[10 * 60 + 15],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60],
                    max_per_day=[1])

    def signals(self, d, p):
        c = d["close"].to_numpy()
        op = d["day_open"].to_numpy()
        pr = d["pd_range"].to_numpy()
        atr = d["atr"].to_numpy()
        mod = d["mod"].to_numpy()
        thr = p["k"] * pr
        long_ok, short_ok = _trend_filter(d, p["trend"])
        active = mod >= p["start_min"]
        cl = (c > op + thr) & long_ok & active
        cs = (c < op - thr) & short_ok & active
        side = _nan_to_zero_side(cl, cs)
        stop = p["stop_atr"] * atr
        tgt = np.where(p["rr"] > 0, stop * p["rr"], np.nan)
        return side, stop, tgt


# ---------------------------------------------------------- VWAP revert ------

class VWAPRevert(Strategy):
    """Reversao a media intradiaria: preco muito esticado do VWAP volta.

    Ideia oposta as de rompimento -- serve tambem como teste de simetria: se as
    duas familias 'funcionam', provavelmente e overfitting.
    """

    def __init__(self):
        super().__init__(name="VWAPRevert")

    def grid(self):
        return grid(k_sd=[1.5, 2.0, 2.5],
                    stop_atr=[1.5, 2.5],
                    exit_mode=["vwap", "rr1"],
                    trend=["none", "d20"],
                    start_min=[10 * 60 + 30],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60 + 30],
                    max_per_day=[2])

    def signals(self, d, p):
        c = d["close"].to_numpy()
        v = d["vwap"].to_numpy()
        sd = d["vwap_sd"].to_numpy()
        atr = d["atr"].to_numpy()
        mod = d["mod"].to_numpy()
        dist = c - v
        thr = p["k_sd"] * sd
        active = (mod >= p["start_min"]) & np.isfinite(sd) & (sd > 0)
        # esticado pra baixo -> compra (reversao)
        long_ok, short_ok = _trend_filter(d, p["trend"])
        cl = (dist < -thr) & active & long_ok
        cs = (dist > thr) & active & short_ok
        side = _nan_to_zero_side(cl, cs)
        stop = p["stop_atr"] * atr
        if p["exit_mode"] == "vwap":
            tgt = np.abs(dist)          # alvo = voltar ao VWAP
        else:
            tgt = stop * 1.0
        return side, stop, tgt


# -------------------------------------------------------------- momentum -----

class BarMomentum(Strategy):
    """Continuacao de impulso: barra(s) grande(s) na mesma direcao com volume."""

    def __init__(self):
        super().__init__(name="BarMomentum")

    def grid(self):
        return grid(lookback=[3, 6, 12],
                    k_atr=[1.0, 1.75],
                    vol_min=[0.0, 1.5],
                    stop_atr=[1.5, 2.5],
                    rr=[0.0, 2.0],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60 + 30],
                    max_per_day=[2])

    def signals(self, d, p):
        c = d["close"]
        n = p["lookback"]
        move = (c - c.shift(n)).to_numpy()
        atr = d["atr"].to_numpy()
        vr = d["vol_ratio"].to_numpy()
        # nao pode olhar antes de ter n barras no mesmo dia
        valid = d["bar_of_day"].to_numpy() >= n
        thr = p["k_atr"] * atr
        volok = np.nan_to_num(vr, nan=0.0) >= p["vol_min"]
        cl = (move > thr) & valid & volok
        cs = (move < -thr) & valid & volok
        side = _nan_to_zero_side(cl, cs)
        stop = p["stop_atr"] * atr
        tgt = np.where(p["rr"] > 0, stop * p["rr"], np.nan)
        return side, stop, tgt


# ------------------------------------------------------------------ gap ------

class GapPlay(Strategy):
    """Gap de abertura: continuacao ou desvanecimento, decidido pela grade."""

    def __init__(self):
        super().__init__(name="GapPlay")

    def grid(self):
        return grid(min_gap_atr=[0.3, 0.6, 1.0],
                    direction=["fade", "follow"],
                    entry_min=[10 * 60 + 15, 10 * 60 + 30],
                    stop_atr=[1.5, 3.0],
                    rr=[0.0, 1.5],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60 + 30],
                    max_per_day=[1])

    def signals(self, d, p):
        gap = d["gap"].to_numpy()
        atr = d["atr"].to_numpy()
        mod = d["mod"].to_numpy()
        ref = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
        big = np.abs(gap) > p["min_gap_atr"] * ref
        at = mod == p["entry_min"]
        up = gap > 0
        if p["direction"] == "fade":
            cl = big & at & ~up
            cs = big & at & up
        else:
            cl = big & at & up
            cs = big & at & ~up
        side = _nan_to_zero_side(cl, cs)
        stop = p["stop_atr"] * atr
        tgt = np.where(p["rr"] > 0, stop * p["rr"], np.nan)
        return side, stop, tgt


# ------------------------------------------------------------ ema trend ------

class EmaTrend(Strategy):
    """Cruzamento de medias intradiario com filtro de tendencia diaria."""

    def __init__(self):
        super().__init__(name="EmaTrend")

    def grid(self):
        return grid(fast=[9, 21],
                    slow=[21, 50],
                    stop_atr=[1.5, 2.5],
                    rr=[0.0, 2.0],
                    trend=["none", "d20"],
                    start_min=[10 * 60 + 30],
                    exit_min=[17 * 60 + 30],
                    last_entry_min=[16 * 60 + 30],
                    max_per_day=[2])

    def signals(self, d, p):
        if p["fast"] >= p["slow"]:
            z = np.zeros(len(d), dtype="int64")
            return z, np.full(len(d), np.nan), np.full(len(d), np.nan)
        f = d[f"ema{p['fast']}"].to_numpy()
        s = d[f"ema{p['slow']}"].to_numpy()
        atr = d["atr"].to_numpy()
        mod = d["mod"].to_numpy()
        above = f > s
        cross_up = above & ~np.roll(above, 1)
        cross_dn = (~above) & np.roll(above, 1)
        cross_up[0] = cross_dn[0] = False
        same_day = d["bar_of_day"].to_numpy() >= 3
        active = (mod >= p["start_min"]) & same_day
        long_ok, short_ok = _trend_filter(d, p["trend"])
        cl = cross_up & active & long_ok
        cs = cross_dn & active & short_ok
        side = _nan_to_zero_side(cl, cs)
        stop = p["stop_atr"] * atr
        tgt = np.where(p["rr"] > 0, stop * p["rr"], np.nan)
        return side, stop, tgt


ALL = [ORB(), VolBreak(), VWAPRevert(), BarMomentum(), GapPlay(), EmaTrend()]


def by_name(name: str) -> Strategy:
    for s in ALL:
        if s.name == name:
            return s
    raise KeyError(name)

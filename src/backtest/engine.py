"""Motor de backtest intradiario baseado em barras -- honesto por construcao.

Regras anti-ilusao (todas obrigatorias, nao opcionais):

1.  Sinal e calculado com informacao ATE O FECHAMENTO da barra t.
    A entrada acontece na ABERTURA da barra t+1. Nunca no proprio close.
2.  Entrada e a mercado: paga `entry_slip_ticks` contra si.
3.  Se stop e alvo caberiam na mesma barra, assume-se STOP (pior caso).
4.  Alvo e ordem limite: so conta como executado se o preco PENETRAR o alvo
    (high estritamente acima, para compra), nunca se apenas encostar.
5.  Stop e ordem stop: executa no preco do stop MAIS `stop_extra_slip_ticks`
    contra si. Se a barra abrir ja alem do stop, executa na abertura (gap).
6.  Saida por tempo e a mercado no fechamento da barra limite, pagando
    `exit_slip_ticks`.
7.  Uma posicao por vez, por simbolo. Sem overnight: toda posicao e fechada
    ate o horario limite do dia.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.core.instruments import Instrument


@dataclass
class EntryIntent:
    """Intencao de entrada gerada por uma estrategia, indexada por barra."""
    bar_idx: np.ndarray      # indice da barra do SINAL (entrada ocorre em bar_idx+1)
    side: np.ndarray         # +1 compra, -1 venda
    stop_dist: np.ndarray    # distancia do stop, em unidades de preco (>0)
    target_dist: np.ndarray  # distancia do alvo (>0) ou NaN para "sem alvo"
    exit_idx: np.ndarray     # indice da barra limite para saida por tempo


TRADE_COLS = ["entry_time", "exit_time", "side", "entry_price", "exit_price",
              "qty", "reason", "gross_brl", "cost_brl", "net_brl", "bars_held",
              "mae_price", "mfe_price"]


def simulate(df: pd.DataFrame, intents: EntryIntent, inst: Instrument,
             qty: np.ndarray | float = 1.0,
             allow_overlap: bool = False) -> pd.DataFrame:
    """Executa as intencoes contra as barras e devolve a lista de trades."""
    o = df["open"].to_numpy(dtype="float64")
    h = df["high"].to_numpy(dtype="float64")
    l = df["low"].to_numpy(dtype="float64")
    c = df["close"].to_numpy(dtype="float64")
    times = df.index.to_numpy()
    n = len(df)

    tick = inst.tick_size
    slip_in = inst.entry_slip_ticks * tick
    slip_out = inst.exit_slip_ticks * tick
    slip_stop = (inst.exit_slip_ticks + inst.stop_extra_slip_ticks) * tick

    if np.isscalar(qty):
        qty_arr = np.full(len(intents.bar_idx), float(qty))
    else:
        qty_arr = np.asarray(qty, dtype="float64")

    rows = []
    busy_until = -1

    for k in range(len(intents.bar_idx)):
        i = int(intents.bar_idx[k])
        e = i + 1                                  # barra de entrada
        if e >= n:
            continue
        if not allow_overlap and e <= busy_until:
            continue
        side = int(intents.side[k])
        if side == 0:
            continue
        last = int(min(intents.exit_idx[k], n - 1))
        if last < e:
            continue

        q = qty_arr[k]
        if q <= 0:
            continue

        entry = o[e] + side * slip_in
        stop_d = float(intents.stop_dist[k])
        tgt_d = float(intents.target_dist[k])
        if not np.isfinite(stop_d) or stop_d <= 0:
            continue
        stop_px = entry - side * stop_d
        tgt_px = entry + side * tgt_d if np.isfinite(tgt_d) and tgt_d > 0 else np.nan

        exit_px = np.nan
        exit_j = last
        reason = "time"
        mae = 0.0
        mfe = 0.0

        for j in range(e, last + 1):
            # excursoes (a partir da barra de entrada, ja com preco de entrada)
            if side > 0:
                mae = min(mae, l[j] - entry)
                mfe = max(mfe, h[j] - entry)
                gap_stop = o[j] <= stop_px
                hit_stop = l[j] <= stop_px
                hit_tgt = np.isfinite(tgt_px) and h[j] > tgt_px
            else:
                mae = min(mae, entry - h[j])
                mfe = max(mfe, entry - l[j])
                gap_stop = o[j] >= stop_px
                hit_stop = h[j] >= stop_px
                hit_tgt = np.isfinite(tgt_px) and l[j] < tgt_px

            if gap_stop:
                exit_px = o[j] - side * slip_out   # ja abriu contra: sai na abertura
                exit_j, reason = j, "stop_gap"
                break
            if hit_stop:                           # regra 3: stop tem prioridade
                exit_px = stop_px - side * slip_stop
                exit_j, reason = j, "stop"
                break
            if hit_tgt:
                exit_px = tgt_px                   # limite: executa no proprio alvo
                exit_j, reason = j, "target"
                break

        if not np.isfinite(exit_px):
            exit_px = c[last] - side * slip_out
            exit_j, reason = last, "time"

        gross = side * (exit_px - entry) * inst.point_value * q
        cost = inst.fees(entry, q) + inst.fees(exit_px, q)
        rows.append((times[e], times[exit_j], side, entry, exit_px, q, reason,
                     gross, cost, gross - cost, exit_j - e,
                     mae, mfe))
        busy_until = exit_j

    if not rows:
        return pd.DataFrame(columns=TRADE_COLS)
    out = pd.DataFrame(rows, columns=TRADE_COLS)
    out["entry_time"] = pd.to_datetime(out["entry_time"])
    out["exit_time"] = pd.to_datetime(out["exit_time"])
    return out


def bar_index_of_time_limit(index: pd.DatetimeIndex, cutoff) -> np.ndarray:
    """Para cada barra, o indice da ultima barra do MESMO dia <= `cutoff`.

    Usado para materializar a regra 'zera tudo antes do fechamento'.
    """
    day = index.normalize()
    minute = index.hour * 60 + index.minute
    cutoff_min = cutoff.hour * 60 + cutoff.minute
    ok = minute <= cutoff_min
    pos = np.arange(len(index))
    limit = np.full(len(index), -1, dtype="int64")
    # ultimo indice valido por dia
    dfl = pd.DataFrame({"day": day, "pos": pos, "ok": ok})
    last_ok = dfl[dfl["ok"]].groupby("day")["pos"].max()
    mapped = pd.Series(day).map(last_ok)
    limit = mapped.to_numpy()
    limit = np.where(np.isnan(limit), pos, limit).astype("int64")
    return limit

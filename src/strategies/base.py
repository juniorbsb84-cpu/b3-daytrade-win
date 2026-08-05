"""Contrato comum das estrategias e utilitarios de construcao de intencoes."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.backtest.engine import EntryIntent


def grid(**kwargs) -> list[dict]:
    """Produto cartesiano nomeado -- cada item vira uma tentativa contada no DSR."""
    keys = list(kwargs)
    return [dict(zip(keys, v)) for v in itertools.product(*(kwargs[k] for k in keys))]


def exit_index_by_minute(d: pd.DataFrame, exit_min: int) -> np.ndarray:
    """Para cada barra, a posicao da ultima barra do mesmo dia com mod <= exit_min."""
    pos = np.arange(len(d))
    ok = d["mod"].to_numpy() <= exit_min
    day = d["date"].to_numpy()
    tmp = pd.DataFrame({"day": day, "pos": pos, "ok": ok})
    last_ok = tmp[tmp["ok"]].groupby("day")["pos"].max()
    mapped = pd.Series(day).map(last_ok).to_numpy(dtype="float64")
    mapped = np.where(np.isnan(mapped), pos, mapped)
    return np.maximum(mapped.astype("int64"), pos)


def make_intents(d: pd.DataFrame, side: np.ndarray, stop_dist: np.ndarray,
                 target_dist: np.ndarray, exit_min: int,
                 last_entry_min: int, max_per_day: int = 1) -> EntryIntent:
    """Converte vetores de sinal em intencoes, aplicando janela e cota diaria."""
    mod = d["mod"].to_numpy()
    ok = (side != 0) & np.isfinite(stop_dist) & (stop_dist > 0) & (mod <= last_entry_min)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return EntryIntent(np.array([], dtype="int64"), np.array([]), np.array([]),
                           np.array([]), np.array([], dtype="int64"))

    if max_per_day > 0:
        day = d["date"].to_numpy()[idx]
        rank = pd.Series(np.ones(len(idx))).groupby(pd.Series(day)).cumcount().to_numpy()
        idx = idx[rank < max_per_day]

    ex = exit_index_by_minute(d, exit_min)
    return EntryIntent(bar_idx=idx.astype("int64"),
                       side=side[idx].astype("int64"),
                       stop_dist=stop_dist[idx].astype("float64"),
                       target_dist=target_dist[idx].astype("float64"),
                       exit_idx=ex[idx].astype("int64"))


@dataclass
class Strategy:
    name: str

    def grid(self) -> list[dict]:
        raise NotImplementedError

    def signals(self, d: pd.DataFrame, p: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Devolve (side, stop_dist, target_dist) alinhados as barras de `d`."""
        raise NotImplementedError

    def intents(self, d: pd.DataFrame, p: dict) -> EntryIntent:
        side, stop, tgt = self.signals(d, p)
        return make_intents(d, side, stop, tgt,
                            exit_min=p.get("exit_min", 17 * 60 + 30),
                            last_entry_min=p.get("last_entry_min", 16 * 60 + 30),
                            max_per_day=p.get("max_per_day", 1))

    def key(self, p: dict) -> str:
        return self.name + "|" + "|".join(f"{k}={p[k]}" for k in sorted(p))

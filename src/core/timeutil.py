"""Regra de tempo do projeto -- ponto unico de verdade.

REGRA (verificada empiricamente em 04/08/2026 contra o terminal Clear MT5):
    O MetaTrader5 devolve `time` / `time_msc` como o horario do SERVIDOR
    codificado como se fosse epoch UTC. O servidor da Clear roda em horario
    de Brasilia. Portanto:

        pd.to_datetime(rates["time"], unit="s")          -> hora de Brasilia (naive)
        pd.to_datetime(ticks["time_msc"], unit="ms")     -> hora de Brasilia (naive)

    NUNCA usar datetime.fromtimestamp() (aplica o fuso local e desloca 3h).
    NUNCA aplicar shift manual de -3h/+3h em cima disso.

Validacao: barra M5 final de PETR4 em 04/08/2026 = 17:55 (fechamento do
after-market da B3) e do WIN = 18:30 (WIN negocia ate 18:25). Bate.

Todo o projeto trabalha em datetime NAIVE representando hora de Brasilia.
Para chamadas de volta ao MT5 (copy_rates_range, copy_ticks_from) usa-se o
mesmo datetime naive -- a biblioteca o interpreta como UTC, o que reproduz
exatamente a convencao acima.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

# Sessoes da B3 (hora de Brasilia). Nao inclui leilao de pre-abertura.
SESSION = {
    "STOCKS": (dt.time(10, 0), dt.time(17, 55)),
    "STOCKS_CONTINUOUS": (dt.time(10, 0), dt.time(16, 55)),  # antes do leilao de fechamento
    "WIN": (dt.time(9, 0), dt.time(18, 25)),
    "WDO": (dt.time(9, 0), dt.time(18, 25)),
}


def mt5_epoch_to_brt(series_or_scalar, unit: str = "s"):
    """Converte campo de tempo do MT5 para datetime naive em hora de Brasilia."""
    return pd.to_datetime(series_or_scalar, unit=unit)


def brt_to_mt5_arg(d: dt.datetime) -> dt.datetime:
    """Datetime naive (hora Brasilia) -> argumento aceito pelas funcoes do MT5."""
    return d.replace(tzinfo=None)


def session_mask(index: pd.DatetimeIndex, kind: str = "STOCKS") -> np.ndarray:
    start, end = SESSION[kind]
    t = index.time
    return (t >= start) & (t <= end)


def add_session_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Anexa colunas derivadas do tempo usadas em praticamente toda a pesquisa."""
    idx = df.index
    out = df.copy()
    out["date"] = idx.normalize()
    out["minute_of_day"] = idx.hour * 60 + idx.minute
    out["dow"] = idx.dayofweek
    return out


def trading_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(set(index.normalize())))

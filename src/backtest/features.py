"""Features intradiarias sobre barras M5.

Regra dura: toda feature em `t` usa exclusivamente informacao ate o FECHAMENTO
de `t`. Nada de `shift(-1)`, nada de estatistica do dia inteiro aplicada ao
comeco do dia. Os testes em tests/test_no_lookahead.py conferem isso.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _true_range(h, l, c_prev):
    return np.maximum(h - l, np.maximum(np.abs(h - c_prev), np.abs(l - c_prev)))


def prepare(df: pd.DataFrame, session_start: str = "10:00",
            session_end: str = "17:55", min_bars_por_dia: int = 20) -> pd.DataFrame:
    """Filtra a sessao, ordena e anexa as colunas de calendario.

    O ULTIMO dia do quadro nunca e descartado por ter poucas barras. Sem essa
    excecao, o motor ao vivo jogaria fora o pregao em andamento durante as
    primeiras ~100 minutos e simplesmente nao geraria sinal nenhum de manha --
    justamente quando os setups de abertura acontecem. O backtest nao muda,
    porque ali o ultimo dia so e parcial no fim do historico.
    """
    d = df.sort_index()
    d = d.between_time(session_start, session_end)
    d = d[~d.index.duplicated(keep="last")]
    d = d.copy()
    idx = d.index
    d["date"] = idx.normalize()
    d["mod"] = idx.hour * 60 + idx.minute          # minuto do dia
    d["dow"] = idx.dayofweek
    if len(d) == 0:
        return d
    # remove dias degenerados (leilao, feriado parcial), preservando o dia corrente
    cnt = d.groupby("date")["close"].transform("size")
    ultimo_dia = d["date"].iloc[-1]
    d = d[(cnt >= min_bars_por_dia) | (d["date"] == ultimo_dia)]
    return d


def add_core(d: pd.DataFrame, atr_len: int = 20) -> pd.DataFrame:
    """ATR intradiario, ranges do dia anterior, acumulados do dia e VWAP."""
    d = d.copy()
    g = d.groupby("date", sort=False)

    c_prev = d["close"].shift(1)
    tr = _true_range(d["high"].to_numpy(), d["low"].to_numpy(), c_prev.to_numpy())
    d["tr"] = tr
    d["atr"] = pd.Series(tr, index=d.index).rolling(atr_len, min_periods=atr_len).mean()

    # --- estatisticas do dia ANTERIOR (constantes dentro do dia) ---
    daily = g.agg(d_open=("open", "first"), d_high=("high", "max"),
                  d_low=("low", "min"), d_close=("close", "last"))
    daily["d_range"] = daily["d_high"] - daily["d_low"]
    prev = daily.shift(1).add_prefix("p")
    dm = d[["date"]].merge(prev, left_on="date", right_index=True, how="left")
    dm.index = d.index
    for col in prev.columns:
        d[col] = dm[col]

    # --- acumulados do dia ATE t (inclusive) ---
    d["day_open"] = g["open"].transform("first")
    d["cum_high"] = g["high"].cummax()
    d["cum_low"] = g["low"].cummin()
    d["bar_of_day"] = g.cumcount()

    # VWAP do dia ate t
    vol = d["real_volume"].replace(0, np.nan)
    vol = vol.fillna(d["tick_volume"]).astype("float64")
    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    pv = tp * vol
    d["_cum_pv"] = pv.groupby(d["date"]).cumsum()
    d["_cum_v"] = vol.groupby(d["date"]).cumsum()
    d["vwap"] = d["_cum_pv"] / d["_cum_v"].replace(0, np.nan)
    dev = (tp - d["vwap"]) ** 2 * vol
    d["_cum_dev"] = dev.groupby(d["date"]).cumsum()
    d["vwap_sd"] = np.sqrt(d["_cum_dev"] / d["_cum_v"].replace(0, np.nan))
    d = d.drop(columns=["_cum_pv", "_cum_v", "_cum_dev"])

    # gap de abertura em unidades de ATR do dia anterior
    d["gap"] = d["day_open"] - d["pd_close"]

    # volume relativo do dia (mesmo horario, media dos ultimos 20 dias)
    d["vol"] = vol
    d["vol_ma"] = (d.groupby("mod", sort=False)["vol"]
                     .transform(lambda s: s.shift(1).rolling(20, min_periods=5).mean()))
    d["vol_ratio"] = d["vol"] / d["vol_ma"].replace(0, np.nan)

    # EMAs intradiarias
    for n in (9, 21, 50):
        d[f"ema{n}"] = d["close"].ewm(span=n, adjust=False).mean()

    # tendencia de prazo mais longo: fechamento do dia anterior vs media de N dias
    dcl = daily["d_close"]
    for n in (5, 20, 50):
        ma = dcl.rolling(n, min_periods=n).mean().shift(1)
        m = d[["date"]].merge(ma.rename(f"dma{n}"), left_on="date",
                              right_index=True, how="left")
        m.index = d.index
        d[f"dma{n}"] = m[f"dma{n}"]
    pc = d["pd_close"]
    d["trend5"] = np.sign(pc - d["dma5"])
    d["trend20"] = np.sign(pc - d["dma20"])
    d["trend50"] = np.sign(pc - d["dma50"])

    # volatilidade diaria relativa (range do dia anterior / preco)
    d["pd_range_pct"] = d["pd_range"] / d["pd_close"]
    d["atr_pct"] = d["atr"] / d["close"]
    return d


def add_opening_range(d: pd.DataFrame, minutes: int, session_start_min: int) -> pd.DataFrame:
    """Range de abertura de `minutes` a partir do inicio da sessao.

    `or_high`/`or_low` so ficam validos DEPOIS que a janela fecha; antes disso
    sao NaN, o que impede qualquer sinal prematuro.
    """
    col_h, col_l = f"or{minutes}_high", f"or{minutes}_low"
    end = session_start_min + minutes
    inwin = d["mod"] < end
    tmp = d[inwin].groupby("date").agg(**{col_h: ("high", "max"), col_l: ("low", "min")})
    m = d[["date"]].merge(tmp, left_on="date", right_index=True, how="left")
    m.index = d.index
    out = d.copy()
    out[col_h] = np.where(d["mod"] >= end, m[col_h], np.nan)
    out[col_l] = np.where(d["mod"] >= end, m[col_l], np.nan)
    out[f"or{minutes}_size"] = out[col_h] - out[col_l]
    return out


def build(df: pd.DataFrame, session_start: str = "10:00", session_end: str = "17:55",
          or_minutes=(15, 30, 60), atr_len: int = 20,
          min_bars_por_dia: int = 20) -> pd.DataFrame:
    d = prepare(df, session_start, session_end, min_bars_por_dia)
    d = add_core(d, atr_len=atr_len)
    ssm = int(session_start[:2]) * 60 + int(session_start[3:5])
    for m in or_minutes:
        d = add_opening_range(d, m, ssm)
    return d

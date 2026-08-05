"""Paridade entre o que o backtest simula e o que o motor ao vivo enxerga.

Este e o teste que pega a familia de bug mais cara do genero: o backtest usa o
historico inteiro para calcular features; o motor ao vivo so tem os dados ate
agora. Se as duas visoes divergirem, a estrategia validada nao e a estrategia
que opera.

Metodo: para varias barras passadas T, monta a visao que o motor teria naquele
instante (so barras <= T) e compara o sinal, o stop e o alvo com o que o
backtest calculou com o historico completo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest import features
from src.ingest.bars import load_bars
from src.strategies import families

SYMBOL, TF = "WIN$N", "M15"  # producao roda M15 -- ver config/live_config.json.
# Auditoria externa de 05/08/2026: este teste testava M5, um timeframe que a
# carteira em producao nao usa (M5 foi reprovado por dado, ver RESULTADOS.md).
# O teste mais caro do projeto nunca exercitou o timeframe que de fato opera.
SS, SE = "09:00", "18:20"
LOOKBACK = 8000          # mesmo valor usado pelo motor (LiveEngine.bars_lookback)


def _live_view(raw: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """Reproduz exatamente o que LiveEngine._closed_bars devolveria em `t`."""
    janela = raw.loc[:t].tail(LOOKBACK)
    return features.build(janela, session_start=SS, session_end=SE)


def test_sinal_ao_vivo_igual_ao_backtest():
    raw = load_bars(SYMBOL, TF)
    assert not raw.empty, "rode scripts/02_download_bars.py primeiro"
    raw = raw.tail(40_000)
    full = features.build(raw, session_start=SS, session_end=SE)

    # 12 instantes espalhados na metade final do historico
    pos_list = np.linspace(len(full) // 2, len(full) - 2, 12).astype(int)
    divergencias = []

    for strat in families.ALL:
        p = dict(strat.grid()[len(strat.grid()) // 2])
        sf, stf, tgf = strat.signals(full, p)
        for pos in pos_list:
            t = full.index[pos]
            d = _live_view(raw, t)
            if d.empty or d.index[-1] != t:
                divergencias.append(f"{strat.name} {t}: barra ausente na visao ao vivo")
                continue
            sl_, stl, tgl = strat.signals(d, p)
            i = len(d) - 1
            if int(sf[pos]) != int(sl_[i]):
                divergencias.append(
                    f"{strat.name} {t}: lado {int(sf[pos])} (backtest) != {int(sl_[i])} (ao vivo)")
                continue
            for nome, a, b in (("stop", stf[pos], stl[i]), ("alvo", tgf[pos], tgl[i])):
                if np.isnan(a) and np.isnan(b):
                    continue
                if not np.isclose(a, b, rtol=1e-6, atol=1e-6):
                    divergencias.append(f"{strat.name} {t}: {nome} {a} != {b}")

    assert not divergencias, "PARIDADE QUEBRADA:\n" + "\n".join(divergencias[:20])


def test_janela_de_8000_barras_e_suficiente():
    """As features de prazo mais longo (media de 50 dias) precisam caber na janela."""
    raw = load_bars(SYMBOL, TF).tail(40_000)
    t = raw.index[-1]
    d = _live_view(raw, t)
    ultimo = d.iloc[-1]
    for col in ("dma50", "dma20", "atr", "vol_ma", "pd_close", "vwap"):
        assert pd.notna(ultimo[col]), f"{col} veio NaN com janela de {LOOKBACK} barras"


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK  {fn.__name__}")
    print(f"{len(fns)} testes de paridade passaram")

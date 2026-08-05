"""Teste de vazamento de futuro nas features.

Metodo: calcula as features sobre o historico inteiro e depois sobre o mesmo
historico TRUNCADO na barra k. Se alguma feature em k mudar, ela esta olhando
para o futuro.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest import features
from src.ingest.bars import load_bars
from src.strategies import families

SYMBOL = "PETR4"


def _cmp(full: pd.DataFrame, trunc: pd.DataFrame, ts, cols) -> list[str]:
    bad = []
    for c in cols:
        a, b = full.loc[ts, c], trunc.loc[ts, c]
        if isinstance(a, str) or isinstance(b, str):
            same = a == b
        else:
            same = (pd.isna(a) and pd.isna(b)) or np.isclose(float(a), float(b),
                                                             rtol=1e-9, atol=1e-9)
        if not same:
            bad.append(f"{c}: full={a} trunc={b}")
    return bad


def test_features_nao_olham_o_futuro():
    raw = load_bars(SYMBOL, "M5")
    assert not raw.empty, "rode scripts/02_download_bars.py primeiro"
    raw = raw.tail(30_000)
    full = features.build(raw)

    cols = [c for c in full.columns if c not in ("date",)]
    # escolhe 5 pontos de corte, sempre no fim de um dia (evita dia parcial)
    days = pd.DatetimeIndex(sorted(set(full["date"])))
    problemas = []
    for dsel in days[np.linspace(len(days) // 2, len(days) - 2, 5).astype(int)]:
        cut = full.index[full["date"] == dsel][-1]
        trunc = features.build(raw.loc[:cut])
        if cut not in trunc.index:
            problemas.append(f"barra {cut} sumiu no truncado")
            continue
        bad = _cmp(full, trunc, cut, cols)
        if bad:
            problemas.append(f"{cut}: " + "; ".join(bad))
    assert not problemas, "VAZAMENTO DE FUTURO:\n" + "\n".join(problemas)


def test_sinais_nao_mudam_com_dados_futuros():
    raw = load_bars(SYMBOL, "M5").tail(30_000)
    full = features.build(raw)
    days = pd.DatetimeIndex(sorted(set(full["date"])))
    dsel = days[-5]
    cut = full.index[full["date"] == dsel][-1]
    trunc = features.build(raw.loc[:cut])

    for strat in families.ALL:
        p = strat.grid()[len(strat.grid()) // 2]
        sf, _, _ = strat.signals(full, dict(p))
        st, _, _ = strat.signals(trunc, dict(p))
        pos_f = full.index.get_loc(cut)
        pos_t = trunc.index.get_loc(cut)
        n = min(pos_t, 500)
        a = sf[pos_f - n:pos_f + 1]
        b = st[pos_t - n:pos_t + 1]
        assert np.array_equal(a, b), f"{strat.name}: sinais mudaram com dado futuro"


def test_entrada_sempre_depois_do_sinal():
    raw = load_bars(SYMBOL, "M5").tail(20_000)
    d = features.build(raw)
    for strat in families.ALL:
        for p in strat.grid()[:3]:
            ii = strat.intents(d, dict(p))
            if len(ii.bar_idx) == 0:
                continue
            assert (ii.exit_idx >= ii.bar_idx + 1).all(), f"{strat.name}: saida antes da entrada"
            # entrada e sempre na barra seguinte ao sinal -> conferido no motor
            assert (ii.bar_idx >= 0).all()


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK  {fn.__name__}")
    print(f"{len(fns)} testes de vazamento passaram")

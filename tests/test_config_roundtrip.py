"""A configuracao operacional tem que reproduzir EXATAMENTE o backtest.

O motor ao vivo reconstroi os parametros a partir da chave em texto. Se a
conversao de tipo errar (um '0.25' virar string, um 'none' virar NaN), a
estrategia que opera nao e a que foi validada -- e ninguem percebe ate o
extrato chegar. Este teste fecha essa porta.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from src.backtest import features
from src.execution.config import parse_param_key
from src.ingest.bars import load_bars
from src.strategies import families


def test_chave_de_parametro_volta_igual():
    for strat in families.ALL:
        for p in strat.grid():
            key = strat.key(p)
            fam, back = parse_param_key(key)
            assert fam == strat.name, f"familia errada: {fam} != {strat.name}"
            assert set(back) == set(p), f"{strat.name}: chaves diferentes"
            for k in p:
                a, b = p[k], back[k]
                if isinstance(a, float):
                    assert isinstance(b, (int, float)) and np.isclose(a, b), \
                        f"{strat.name}.{k}: {a!r} -> {b!r}"
                else:
                    assert a == b, f"{strat.name}.{k}: {a!r} -> {b!r}"


def test_sinais_identicos_apos_roundtrip():
    raw = load_bars("WIN$N", "M5").tail(20_000)
    d = features.build(raw, session_start="09:00", session_end="18:20")
    for strat in families.ALL:
        for p in strat.grid()[::7]:
            key = strat.key(p)
            _, back = parse_param_key(key)
            s1, st1, tg1 = strat.signals(d, dict(p))
            s2, st2, tg2 = strat.signals(d, dict(back))
            assert np.array_equal(s1, s2), f"{strat.name}: sinais diferem apos roundtrip"
            assert np.allclose(np.nan_to_num(st1), np.nan_to_num(st2)), \
                f"{strat.name}: stops diferem"
            assert np.allclose(np.nan_to_num(tg1), np.nan_to_num(tg2)), \
                f"{strat.name}: alvos diferem"


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK  {fn.__name__}")
    print(f"{len(fns)} testes de configuracao passaram")

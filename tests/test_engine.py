"""Testes do motor de execucao -- checam as regras pessimistas uma a uma."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.backtest.engine import EntryIntent, simulate
from src.core.instruments import Instrument

INST = Instrument(symbol="T", kind="future", tick_size=1.0, point_value=1.0, lot_step=1.0,
                  spread_ticks=1.0, entry_slip_ticks=1.0, exit_slip_ticks=1.0,
                  stop_extra_slip_ticks=1.0, fee_per_side_brl=0.0, fee_rate_per_side=0.0,
                  session="WIN")


def bars(rows):
    idx = pd.date_range("2026-01-05 10:00", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def one(bar_idx, side, stop, tgt, exit_idx):
    return EntryIntent(np.array([bar_idx]), np.array([side]), np.array([stop]),
                       np.array([tgt]), np.array([exit_idx]))


def test_entrada_no_open_da_barra_seguinte_com_slippage():
    df = bars([[100, 101, 99, 100], [100, 105, 99, 104], [104, 106, 103, 105]])
    t = simulate(df, one(0, +1, 10, np.nan, 2), INST)
    assert len(t) == 1
    # abre em 100 na barra 1, compra paga +1 tick
    assert t["entry_price"].iloc[0] == 101.0
    assert t["entry_time"].iloc[0] == df.index[1]


def test_stop_tem_prioridade_sobre_alvo_na_mesma_barra():
    # barra 1 toca o alvo (110) e o stop (95): por regra, conta o stop
    df = bars([[100, 100, 100, 100], [100, 115, 90, 100], [100, 100, 100, 100]])
    t = simulate(df, one(0, +1, 6, 9, 2), INST)   # entry 101, stop 95, alvo 110
    assert t["reason"].iloc[0] == "stop"
    # stop 95 com 2 ticks de slippage (exit 1 + extra 1) = 93
    assert t["exit_price"].iloc[0] == 93.0


def test_alvo_exige_penetracao_estrita():
    # high toca exatamente o alvo mas nao penetra -> nao executa, sai por tempo
    df = bars([[100, 100, 100, 100], [100, 110, 100, 105], [105, 105, 105, 105]])
    t = simulate(df, one(0, +1, 20, 9, 2), INST)  # entry 101, alvo 110
    assert t["reason"].iloc[0] == "time"


def test_gap_contra_executa_na_abertura():
    df = bars([[100, 100, 100, 100], [100, 101, 99, 100], [80, 82, 78, 80]])
    t = simulate(df, one(0, +1, 6, np.nan, 2), INST)  # entry 101, stop 95
    assert t["reason"].iloc[0] == "stop_gap"
    assert t["exit_price"].iloc[0] == 79.0            # abertura 80 menos 1 tick


def test_saida_por_tempo_no_close_com_slippage():
    df = bars([[100, 100, 100, 100], [100, 101, 99, 100], [100, 103, 100, 102]])
    t = simulate(df, one(0, +1, 20, np.nan, 2), INST)
    assert t["reason"].iloc[0] == "time"
    assert t["exit_price"].iloc[0] == 101.0           # close 102 menos 1 tick
    assert t["net_brl"].iloc[0] == 0.0                # 101 -> 101


def test_venda_paga_slippage_no_sentido_certo():
    df = bars([[100, 100, 100, 100], [100, 101, 99, 100], [100, 100, 100, 100]])
    t = simulate(df, one(0, -1, 20, np.nan, 2), INST)
    assert t["entry_price"].iloc[0] == 99.0           # vende no bid
    assert t["exit_price"].iloc[0] == 101.0           # recompra no ask
    assert t["net_brl"].iloc[0] == -2.0               # perde o spread inteiro


def test_sem_sobreposicao_de_posicao():
    df = bars([[100, 100, 100, 100]] * 10)
    ii = EntryIntent(np.array([0, 1, 2]), np.array([1, 1, 1]),
                     np.array([20.0] * 3), np.array([np.nan] * 3), np.array([5, 6, 7]))
    t = simulate(df, ii, INST)
    assert len(t) == 1                                # as duas seguintes sao descartadas


def test_custos_reduzem_o_resultado():
    inst = Instrument(**{**INST.__dict__, "fee_per_side_brl": 0.5})
    df = bars([[100, 100, 100, 100], [100, 101, 99, 100], [110, 110, 110, 110]])
    t = simulate(df, one(0, +1, 20, np.nan, 2), inst)
    assert t["cost_brl"].iloc[0] == 1.0
    assert t["net_brl"].iloc[0] == t["gross_brl"].iloc[0] - 1.0


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK  {fn.__name__}")
    print(f"{len(fns)} testes do motor passaram")

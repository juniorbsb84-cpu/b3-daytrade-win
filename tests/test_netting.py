"""Testes do livro de pernas virtuais sobre conta netting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.execution.netting import NetBook, VirtualLeg

PV = 0.20   # valor do ponto do WIN


def leg(magic=1, side=1, qty=2, entry=140000, sl=139500, tp=141000, exit_min=1050):
    return VirtualLeg(magic=magic, family="X", side=side, qty=qty, entry_price=entry,
                      sl=sl, tp=tp, opened_minute=600, exit_minute=exit_min)


def test_posicao_alvo_e_a_soma_assinada():
    b = NetBook([leg(1, +1, 3), leg(2, -1, 5), leg(3, +1, 1)])
    assert b.target_net() == -1.0


def test_stop_de_compra_usa_o_bid():
    b = NetBook([leg(side=+1, sl=139500)])
    # bid ainda acima do stop -> nao sai
    assert b.pop_exits(bid=139505, ask=139510, minute_of_day=700) == []
    # bid tocou o stop -> sai, mesmo com ask acima
    saem = b.pop_exits(bid=139500, ask=139520, minute_of_day=700)
    assert len(saem) == 1 and saem[0][1] == "stop"
    assert b.target_net() == 0.0


def test_stop_de_venda_usa_o_ask():
    b = NetBook([leg(side=-1, sl=140500, tp=139000)])
    assert b.pop_exits(bid=140480, ask=140495, minute_of_day=700) == []
    saem = b.pop_exits(bid=140480, ask=140500, minute_of_day=700)
    assert len(saem) == 1 and saem[0][1] == "stop"


def test_alvo_e_saida_por_tempo():
    b = NetBook([leg(magic=1, side=+1, tp=141000, exit_min=1050)])
    saem = b.pop_exits(bid=141000, ask=141005, minute_of_day=700)
    assert saem[0][1] == "alvo"

    b = NetBook([leg(magic=2, side=+1, tp=None, exit_min=1000)])
    assert b.pop_exits(bid=140100, ask=140105, minute_of_day=999) == []
    assert b.pop_exits(bid=140100, ask=140105, minute_of_day=1000)[0][1] == "tempo"


def test_stop_tem_prioridade_sobre_tempo():
    b = NetBook([leg(side=+1, sl=139500, tp=None, exit_min=1000)])
    saem = b.pop_exits(bid=139400, ask=139405, minute_of_day=1200)
    assert saem[0][1] == "stop"


def test_pnl_nao_realizado_respeita_o_lado():
    l = leg(side=+1, qty=2, entry=140000)
    assert l.unrealized(140100, PV) == 100 * PV * 2
    s = leg(side=-1, qty=2, entry=140000)
    assert s.unrealized(140100, PV) == -100 * PV * 2


def test_stop_de_catastrofe_fica_abaixo_para_comprado():
    b = NetBook([leg(side=+1, qty=5, entry=140000)])
    sl = b.protective_stop_price(140000, PV, max_open_loss_brl=1000)
    # 1000 / (5 contratos x 0,20) = 1000 pontos abaixo
    assert abs(sl - 139000) < 1e-6

    b2 = NetBook([leg(side=-1, qty=5, entry=140000)])
    sl2 = b2.protective_stop_price(140000, PV, max_open_loss_brl=1000)
    assert abs(sl2 - 141000) < 1e-6


def test_stop_de_catastrofe_encolhe_quando_ja_ha_prejuizo():
    b = NetBook([leg(side=+1, qty=5, entry=140000)])
    # preco caiu 500 pts -> prejuizo aberto = 500 x 5 x 0,20 = R$500
    sl = b.protective_stop_price(139500, PV, max_open_loss_brl=1000)
    # folga restante R$500 -> 500 pontos abaixo do preco atual
    assert abs(sl - 139000) < 1e-6


def test_livro_vazio_nao_tem_stop():
    assert NetBook().protective_stop_price(140000, PV, 1000) is None


def test_serializacao_ida_e_volta():
    b = NetBook([leg(1, +1, 3), leg(2, -1, 2)])
    b2 = NetBook.from_json(b.to_json())
    assert b2.target_net() == b.target_net()
    assert [l.family for l in b2.legs] == [l.family for l in b.legs]


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  OK  {fn.__name__}")
    print(f"{len(fns)} testes de netting passaram")

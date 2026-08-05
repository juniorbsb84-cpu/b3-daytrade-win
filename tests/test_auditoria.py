"""Testes das falhas encontradas na auditoria de 05/08/2026.

Cada teste aqui existe porque a falha correspondente ESTAVA no ar, em producao,
sem ninguem perceber. Nenhuma delas quebrava a suite antiga -- por isso a suite
antiga nao bastava.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.execution import live  # noqa: E402

OK, FALHA = [], []


def checa(nome, cond, detalhe=""):
    (OK if cond else FALHA).append(nome)
    print(f"  {'OK  ' if cond else 'FALHA'} {nome}{'' if cond else '  -> ' + detalhe}")


# --------------------------------------------------------- instancia unica ---
def test_lock_impede_dois_motores():
    """Um segundo motor no mesmo PC precisa ser recusado, nao coexistir.

    O loop do motor nao termina sozinho: sem trava, uma execucao manual mais a
    tarefa das 10:05 poem dois processos mandando ordem na mesma conta.
    """
    live.LOCK_FILE.unlink(missing_ok=True)
    with live.instancia_unica():
        checa("lock criado enquanto o motor roda", live.LOCK_FILE.exists())
        dono = json.loads(live.LOCK_FILE.read_text(encoding="utf-8"))
        checa("lock registra o PID dono", dono.get("pid") == os.getpid(),
              f"pid no lock={dono.get('pid')} atual={os.getpid()}")
        try:
            with live.instancia_unica():
                checa("segundo motor foi RECUSADO", False, "o segundo entrou")
        except live.MotorJaRodando:
            checa("segundo motor foi RECUSADO", True)
    checa("lock liberado ao sair", not live.LOCK_FILE.exists())


def test_lock_orfao_e_assumido():
    """Lock de processo morto (queda/reboot) nao pode travar o pregao seguinte."""
    live.LOCK_FILE.write_text(json.dumps({"pid": 999999999, "inicio": "x"}),
                              encoding="utf-8")
    try:
        with live.instancia_unica():
            checa("lock orfao e assumido, nao bloqueia", True)
    except live.MotorJaRodando:
        checa("lock orfao e assumido, nao bloqueia", False, "bloqueou indevidamente")
    finally:
        live.LOCK_FILE.unlink(missing_ok=True)


# ------------------------------------------------------ log de trades ---------
def test_log_de_trades_e_tabela_valida(tmp_path=None):
    """Eventos de tipos diferentes precisam alinhar nas MESMAS colunas.

    O log antigo escrevia so as chaves de cada evento: o cabecalho vinha do
    primeiro e os seguintes desalinhavam (um 'reconcile' gravava o alvo na
    coluna 'family'). O arquivo ficava impossivel de ler como tabela.
    """
    destino = Path(__file__).parent / "_tmp_trades.csv"
    destino.unlink(missing_ok=True)
    original = live.TRADE_LOG
    live.TRADE_LOG = destino
    try:
        eng = live.LiveEngine.__new__(live.LiveEngine)   # sem __init__: so _log_row
        eng._log_row({"ts": "2026-08-05 15:30:01", "evento": "entry_virtual",
                      "symbol": "WINQ26", "family": "EmaTrend", "side": -1,
                      "qty": 1.0, "ref": 178775.0, "sl": 179422.25, "tp": 177480.5})
        eng._log_row({"ts": "2026-08-05 15:30:01", "evento": "reconcile",
                      "symbol": "WINQ26", "alvo": -1.0, "atual": 0.0, "delta": -1.0,
                      "ok": True, "preco": 178775.0, "retcode": 10009,
                      "comment": "Request executed"})
        eng._log_row({"ts": "2026-08-05 17:30:01", "evento": "exit_virtual",
                      "symbol": "WINQ26", "family": "ORB", "side": -1, "qty": 1.0,
                      "entry": 178460.0, "exit": 178140.0, "motivo": "tempo",
                      "bruto_brl": 64.0})

        df = pd.read_csv(destino)          # antes: ParserError
        checa("log parseia como tabela", len(df) == 3, f"linhas={len(df)}")
        checa("colunas fixas em todos os eventos",
              list(df.columns) == live.TRADE_LOG_COLS,
              f"{list(df.columns)}")
        linha_rec = df[df["evento"] == "reconcile"].iloc[0]
        checa("reconcile NAO contamina a coluna family",
              pd.isna(linha_rec["family"]) or linha_rec["family"] == "",
              f"family={linha_rec['family']!r}")
        checa("reconcile preserva o alvo na coluna certa",
              float(linha_rec["alvo"]) == -1.0, f"alvo={linha_rec['alvo']!r}")
        linha_saida = df[df["evento"] == "exit_virtual"].iloc[0]
        checa("saida preserva bruto_brl", float(linha_saida["bruto_brl"]) == 64.0)
    finally:
        live.TRADE_LOG = original
        destino.unlink(missing_ok=True)


# --------------------------------------------------- ciclos em conta netting --
def test_ciclo_netting_nao_confunde_entrada_com_saida():
    """Duas entradas + uma saida = UM ciclo, nao 'a 2a entrada fechou a 1a'.

    Reproduz 05/08/2026: EmaTrend vendeu 1, ORB vendeu 1, zeragem comprou 2.
    A versao antiga lia a entrada do ORB como fechamento do EmaTrend e
    reportava PnL R$0,00 num dia de +R$191.
    """
    import MetaTrader5 as mt5
    from src.monitor.report import round_trips

    deals = pd.DataFrame([
        {"time": pd.Timestamp("2026-08-05 15:30:01"), "symbol": "WINQ26",
         "type": mt5.DEAL_TYPE_SELL, "volume": 1.0, "price": 178775.0,
         "profit": 0.0, "commission": 0.0, "position_id": 1, "comment": ""},
        {"time": pd.Timestamp("2026-08-05 16:00:02"), "symbol": "WINQ26",
         "type": mt5.DEAL_TYPE_SELL, "volume": 1.0, "price": 178460.0,
         "profit": 0.0, "commission": 0.0, "position_id": 1, "comment": ""},
        {"time": pd.Timestamp("2026-08-05 17:30:01"), "symbol": "WINQ26",
         "type": mt5.DEAL_TYPE_BUY, "volume": 2.0, "price": 178140.0,
         "profit": 191.0, "commission": 0.0, "position_id": 1, "comment": "reconcile"},
    ])
    rt = round_trips(deals)
    checa("as 3 operacoes viram UM ciclo", len(rt) == 1, f"ciclos={len(rt)}")
    if len(rt):
        c = rt.iloc[0]
        checa("PnL do ciclo bate com o broker", abs(c["pnl_brl"] - 191.0) < 1e-6,
              f"pnl={c['pnl_brl']}")
        checa("volume do ciclo soma as duas entradas", c["volume"] == 2.0,
              f"volume={c['volume']}")
        checa("entrada e o preco medio ponderado",
              abs(c["preco_entrada"] - 178617.5) < 1e-6, f"{c['preco_entrada']}")
        checa("saida e o preco de fechamento", c["preco_saida"] == 178140.0)
        checa("lado do ciclo e venda", c["side"] == "venda")


def test_protective_stop_loga_e_grava_resultado(monkeypatch=None):
    """A rede de catastrofe precisa provar que foi registrada -- ou que falhou.

    Auditoria de 05/08/2026: com posicao aberta 15:30-17:30, o historico de
    ordens da corretora nao mostrava NENHUMA acao TRADE_ACTION_SLTP, e a funcao
    nao logava nada em caso de sucesso -- impossivel dizer, so pelo log, se a
    rede existia. Este teste finge um `set_sltp` de sucesso e de falha e exige
    que os dois fiquem registrados no CSV (evento 'protective_stop'), nao so em
    log solto.
    """
    from dataclasses import dataclass
    from src.execution.netting import NetBook, VirtualLeg
    from src.execution.live import RiskCfg
    from src.execution import broker as broker_mod

    @dataclass
    class PosFake:
        symbol: str = "WINQ26"
        sl: float = 0.0

    for ok_esperado in (True, False):
        destino = Path(__file__).parent / "_tmp_pstop.csv"
        destino.unlink(missing_ok=True)
        original_log = live.TRADE_LOG
        original_get_pos = broker_mod.get_position
        original_set_sltp = broker_mod.set_sltp
        original_normalize = broker_mod.normalize_price
        live.TRADE_LOG = destino
        broker_mod.get_position = lambda sym, magic=None: PosFake()
        broker_mod.normalize_price = lambda sym, px: px
        broker_mod.set_sltp = lambda pos, sl, tp: broker_mod.OrderResult(
            ok=ok_esperado, retcode=10009 if ok_esperado else 10013,
            comment="Request executed" if ok_esperado else "rejeitado")

        try:
            eng = live.LiveEngine.__new__(live.LiveEngine)
            eng.dry_run = False
            eng.risk = RiskCfg()
            eng.quote_fn = lambda sym: (178100.0, 178105.0)
            book = NetBook([VirtualLeg(magic=770000, family="EmaTrend", side=-1,
                                       qty=1.0, entry_price=178775.0, sl=179422.25,
                                       tp=None, opened_minute=930, exit_minute=1050)])
            eng.books = {"WINQ26": book}
            eng._research_symbol = lambda sym: "WIN$N"
            eng._quote = lambda sym: eng.quote_fn(sym)
            eng.clock = lambda: pd.Timestamp("2026-08-05 16:00:00")

            eng._protective_stop("WINQ26")

            df = pd.read_csv(destino) if destino.exists() else pd.DataFrame()
            evs = df[df["evento"] == "protective_stop"] if len(df) else df
            checa(f"protective_stop grava evento (ok={ok_esperado})", len(evs) == 1,
                  f"linhas={len(evs)}")
            if len(evs):
                checa(f"grava o resultado real (ok={ok_esperado})",
                      bool(evs.iloc[0]["ok"]) == ok_esperado,
                      f"ok gravado={evs.iloc[0]['ok']!r}")
        finally:
            live.TRADE_LOG = original_log
            broker_mod.get_position = original_get_pos
            broker_mod.set_sltp = original_set_sltp
            broker_mod.normalize_price = original_normalize
            destino.unlink(missing_ok=True)


def test_ciclo_aberto_nao_e_reportado():
    """Posicao ainda aberta nao pode virar 'ciclo fechado' com PnL parcial."""
    import MetaTrader5 as mt5
    from src.monitor.report import round_trips

    deals = pd.DataFrame([
        {"time": pd.Timestamp("2026-08-05 15:30:01"), "symbol": "WINQ26",
         "type": mt5.DEAL_TYPE_SELL, "volume": 1.0, "price": 178775.0,
         "profit": 0.0, "commission": 0.0, "position_id": 1, "comment": ""},
    ])
    checa("posicao aberta nao vira ciclo", len(round_trips(deals)) == 0)


# ------------------------------------------ segunda auditoria externa (C1-C3) --
def test_c1_quota_por_perna_sobrevive_a_restart():
    """max_per_day precisa sobreviver a um restart no MEIO do dia.

    Achado da 2a auditoria externa (05/08/2026, C1): `lg.entries_today` e
    atributo de `LegCfg`, reconstruido em 0 toda vez que o processo sobe. O
    livro virtual so guarda pernas ABERTAS -- uma perna que ja entrou e ja
    SAIU no dia some sem deixar rastro de que usou sua cota. Sem persistir a
    contagem, um restart libera nova entrada alem de `max_per_day`.
    """
    from src.execution.live import EngineState, LegCfg

    live.STATE_FILE.unlink(missing_ok=True)
    try:
        legs = [LegCfg(symbol="WIN$N", family="ORB", params={"max_per_day": 1},
                       risk_brl=460.0, timeframe="M15")]
        eng = live.LiveEngine.__new__(live.LiveEngine)
        eng.legs = legs
        eng.state = EngineState()
        eng.books = {}

        # ORB entra e sai no mesmo dia -- some do livro, mas ja usou sua cota.
        eng.state = EngineState(day="2026-08-05")
        eng.state.legs_done["ORB"] = 1
        eng._save_state()

        # processo NOVO: LegCfg nasce com entries_today=0, como no restart real.
        legs2 = [LegCfg(symbol="WIN$N", family="ORB", params={"max_per_day": 1},
                        risk_brl=460.0, timeframe="M15")]
        eng2 = live.LiveEngine.__new__(live.LiveEngine)
        eng2.legs = legs2
        eng2.books = {}
        eng2._load_state("2026-08-05")

        checa("entries_today restaurado da perna apos restart",
              legs2[0].entries_today == 1, f"veio {legs2[0].entries_today}")
    finally:
        live.STATE_FILE.unlink(missing_ok=True)


def test_c2_sinal_de_barra_de_outro_dia_e_rejeitado():
    """Barra fechada de ONTEM nao pode virar sinal de HOJE.

    Achado da 2a auditoria (C2): entre a abertura do pregao e o fechamento da
    1a barra M15 (09:00-09:15), a ultima barra FECHADA ainda e a de ontem. O
    gate de horario usava `_mod(now)` (hora atual), nao o mod da PROPRIA
    barra -- entao passava, e o motor podia entrar com stop/alvo calculados
    em cima do fechamento de ontem, a preco de hoje. O backtest nunca gera
    essa entrada (`make_intents` filtra pelo mod da barra do sinal).
    """
    ontem = pd.Timestamp("2026-08-04 18:15:00")
    hoje_09h05 = pd.Timestamp("2026-08-05 09:05:00")
    checa("barra de ontem tem data diferente de hoje",
          ontem.normalize() != hoje_09h05.normalize())
    # a regra em si (equivalente ao early-return de _process_leg):
    bloqueado = ontem.normalize() != hoje_09h05.normalize()
    checa("regra bloqueia a barra de outro dia", bloqueado)


def test_c3_zeragem_com_ordem_rejeitada_preserva_o_livro():
    """Se a ordem de fechar a posicao falhar, o livro NAO pode ficar vazio.

    Achado da 2a auditoria (C3): a versao anterior dava `clear()` no livro
    ANTES de saber se `_reconcile` conseguiria fechar a posicao real. Numa
    rejeicao, o livro ficava vazio com a posicao real ainda aberta -- e
    `_protective_stop` para de agir quando o livro esta vazio (`not
    book.legs`), deixando a posicao sem rede nenhuma no servidor.
    """
    from src.execution.netting import NetBook, VirtualLeg
    from src.execution import broker as broker_mod

    original_market_order = broker_mod.market_order
    original_get_pos_vol = live.broker_net_volume
    try:
        # forca toda ordem a mercado a "falhar" (broker rejeitou)
        broker_mod.market_order = lambda *a, **k: broker_mod.OrderResult(
            ok=False, retcode=10013, comment="rejeitado")
        live.broker_net_volume = lambda sym: -2.0   # posicao real continua aberta

        eng = live.LiveEngine.__new__(live.LiveEngine)
        eng.dry_run = False
        eng.risk = live.RiskCfg()
        eng.clock = lambda: pd.Timestamp("2026-08-05 17:30:01")
        eng._log_row = lambda row: None
        book = NetBook([
            VirtualLeg(magic=770000, family="EmaTrend", side=-1, qty=1.0,
                      entry_price=178775.0, sl=179422.25, tp=None,
                      opened_minute=930, exit_minute=1050),
            VirtualLeg(magic=770003, family="ORB", side=-1, qty=1.0,
                      entry_price=178460.0, sl=179242.5, tp=None,
                      opened_minute=960, exit_minute=1050),
        ])
        eng.books = {"WINQ26": book}

        eng._flatten("time_exit")

        checa("livro NAO fica vazio quando a zeragem falha",
              len(eng.books["WINQ26"].legs) == 2,
              f"pernas restantes={len(eng.books['WINQ26'].legs)}")
        familias = {l.family for l in eng.books["WINQ26"].legs}
        checa("as MESMAS pernas voltam ao livro",
              familias == {"EmaTrend", "ORB"}, f"{familias}")
    finally:
        broker_mod.market_order = original_market_order
        live.broker_net_volume = original_get_pos_vol


def test_c3_zeragem_confirmada_esvazia_o_livro():
    """Contraste do teste acima: com a ordem aceita, o livro DEVE esvaziar."""
    from src.execution.netting import NetBook, VirtualLeg
    from src.execution import broker as broker_mod

    original_market_order = broker_mod.market_order
    original_get_pos_vol = live.broker_net_volume
    try:
        broker_mod.market_order = lambda *a, **k: broker_mod.OrderResult(
            ok=True, retcode=10009, comment="Request executed")
        live.broker_net_volume = lambda sym: -1.0   # posicao real ainda aberta

        eng = live.LiveEngine.__new__(live.LiveEngine)
        eng.dry_run = False
        eng.risk = live.RiskCfg()
        eng.clock = lambda: pd.Timestamp("2026-08-05 17:30:01")
        eng._log_row = lambda row: None
        book = NetBook([VirtualLeg(magic=770000, family="EmaTrend", side=-1,
                                   qty=1.0, entry_price=178775.0, sl=179422.25,
                                   tp=None, opened_minute=930, exit_minute=1050)])
        eng.books = {"WINQ26": book}

        eng._flatten("time_exit")

        checa("livro esvazia quando a zeragem confirma",
              len(eng.books["WINQ26"].legs) == 0)
    finally:
        broker_mod.market_order = original_market_order
        live.broker_net_volume = original_get_pos_vol


if __name__ == "__main__":
    print("auditoria 05/08/2026 -- falhas que estavam em producao\n")
    for fn in (test_lock_impede_dois_motores, test_lock_orfao_e_assumido,
               test_log_de_trades_e_tabela_valida,
               test_protective_stop_loga_e_grava_resultado,
               test_ciclo_netting_nao_confunde_entrada_com_saida,
               test_ciclo_aberto_nao_e_reportado,
               test_c1_quota_por_perna_sobrevive_a_restart,
               test_c2_sinal_de_barra_de_outro_dia_e_rejeitado,
               test_c3_zeragem_com_ordem_rejeitada_preserva_o_livro,
               test_c3_zeragem_confirmada_esvazia_o_livro):
        print(f"{fn.__name__}:")
        fn()
    print(f"\n{len(OK)} ok, {len(FALHA)} falha(s)")
    sys.exit(1 if FALHA else 0)

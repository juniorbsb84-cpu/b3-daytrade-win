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


if __name__ == "__main__":
    print("auditoria 05/08/2026 -- falhas que estavam em producao\n")
    for fn in (test_lock_impede_dois_motores, test_lock_orfao_e_assumido,
               test_log_de_trades_e_tabela_valida,
               test_ciclo_netting_nao_confunde_entrada_com_saida,
               test_ciclo_aberto_nao_e_reportado):
        print(f"{fn.__name__}:")
        fn()
    print(f"\n{len(OK)} ok, {len(FALHA)} falha(s)")
    sys.exit(1 if FALHA else 0)

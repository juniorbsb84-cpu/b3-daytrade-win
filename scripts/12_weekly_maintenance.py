"""Manutencao semanal: dado novo, revalidacao e reconfiguracao.

Roda sozinha. Se a revalidacao piorar de forma relevante, a configuracao NAO e
substituida e o fato fica registrado -- degradacao silenciosa e o jeito mais
comum de uma estrategia morrer sem ninguem perceber.
"""
import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from research.finalize import build_legs  # noqa: E402
from research.win_deep import portfolio_walk_forward, simulate_all  # noqa: E402
from src.core import instruments, paths  # noqa: E402
from src.execution import config as cfg, sizing  # noqa: E402
from src.execution.live import LegCfg, RiskCfg  # noqa: E402
from src.ingest import bars, universe  # noqa: E402

HIST = paths.RESULTS / "manutencao_semanal.csv"
SHARPE_MINIMO = 0.30          # abaixo disso a configuracao nao e renovada
SYMBOL = "WIN$N"
TIMEFRAMES = ("M15",)         # M5 foi reprovado; ver RESULTADOS.md
# O risco por perna NAO vem daqui: quem dimensiona e src/execution/sizing.py,
# a partir da banca. Este valor so alimenta os pesos relativos de build_legs.
TOTAL_RISK = 900.0


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(paths.LOGS / "manutencao.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])


def main():
    setup_logging()
    log = logging.getLogger()
    log.info("=== manutencao semanal ===")

    log.info("atualizando barras...")
    bars.download([SYMBOL, "WDO$N", "IBOV"], timeframes=("M5", "M15", "M30", "H1", "D1"),
                  verbose=False)
    try:
        liq = universe.load_liquid()
        bars.download(liq["symbol"].tolist()[:60], timeframes=("M5", "D1"), verbose=False)
    except FileNotFoundError:
        log.warning("universo ainda nao construido -- pulando acoes")

    linha = {"data": dt.date.today().isoformat()}
    ok_geral = True
    for tf in TIMEFRAMES:
        all_trades, days, _ = simulate_all(SYMBOL, timeframe=tf)
        res = portfolio_walk_forward(all_trades, days)
        oos = res["oos_daily"]
        if oos.empty:
            log.error("%s %s: sem OOS", SYMBOL, tf)
            ok_geral = False
            continue
        sh = float(oos.mean() / oos.std(ddof=1) * (252 ** 0.5))
        ult = oos.tail(60)
        sh60 = float(ult.mean() / ult.std(ddof=1) * (252 ** 0.5)) if ult.std(ddof=1) > 0 else 0.0
        log.info("%s %s: Sharpe OOS completo=%.2f | ultimos 60 dias=%.2f | PnL total R$%.2f",
                 SYMBOL, tf, sh, sh60, oos.sum())
        linha[f"sharpe_{tf}"] = round(sh, 3)
        linha[f"sharpe60_{tf}"] = round(sh60, 3)
        if sh < SHARPE_MINIMO:
            ok_geral = False

    if not ok_geral:
        log.error("REVALIDACAO ABAIXO DO CORTE -- configuracao operacional MANTIDA "
                  "como estava; revisar antes de continuar operando.")
        linha["acao"] = "config_mantida"
    else:
        todas = []
        for tf in TIMEFRAMES:
            todas.extend(build_legs(SYMBOL, tf, TOTAL_RISK / len(TIMEFRAMES)))
        if todas:
            # A pesquisa diz QUAIS parametros; a banca diz QUANTAS pernas cabem.
            # Sem este passo a config volta a carteira inteira, sem teto de
            # contratos, e uma banca de R$5.000 vai a ruina -- medido.
            try:
                _, risco_atual, _ = cfg.load()
            except FileNotFoundError:
                risco_atual = RiskCfg()
            inst = instruments.get(SYMBOL)
            legs, diag = sizing.aplica_orcamento(
                todas, bars.load_bars(SYMBOL, TIMEFRAMES[0]), inst, log=log)
            if not legs:
                log.error("nenhuma perna coube no orcamento de risco da banca "
                          "-- configuracao MANTIDA como estava")
                linha["acao"] = "sem_pernas_no_orcamento"
                df = pd.DataFrame([linha])
                df.to_csv(HIST, mode="a", header=not HIST.exists(),
                          index=False, encoding="utf-8")
                return
            risco = sizing.limites_de_risco(legs, diag["perfil"], risco_atual,
                                            pnl_diario=diag["pnl_diario"])
            cfg.save(legs, risco, meta={
                "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
                "origem": "manutencao_semanal",
                "banca_brl": diag["banca"],
                "risco_agregado_pct": round(diag["risco_agregado_pct"], 1),
                "cortadas": [l["family"] for l in todas
                             if l["family"] not in [g.family for g in legs]],
                "detalhe": [{k: v for k, v in l.items() if k != "params"} for l in todas]})
            log.info("configuracao renovada: %d de %d pernas cabem na banca de "
                     "R$%.0f (risco agregado %.1f%%) | kill R$%.0f | catastrofe R$%.0f",
                     len(legs), len(todas), diag["banca"],
                     diag["risco_agregado_pct"], risco.daily_loss_limit_brl,
                     risco.max_open_loss_brl)
            linha["acao"] = f"config_renovada({len(legs)} de {len(todas)} pernas)"
        else:
            log.warning("nenhuma perna passou o corte -- config mantida")
            linha["acao"] = "sem_pernas"

    df = pd.DataFrame([linha])
    df.to_csv(HIST, mode="a", header=not HIST.exists(), index=False, encoding="utf-8")
    log.info("registro: %s", json.dumps(linha, ensure_ascii=False))


if __name__ == "__main__":
    main()

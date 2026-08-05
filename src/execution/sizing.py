"""Orcamento de risco da banca -- fonte unica da verdade sobre o capital.

Por que este modulo existe
--------------------------
O contrato do WIN e indivisivel: 1 contrato arrisca R$90 a R$185 por trade,
entre 1,8% e 3,7% de uma banca de R$5.000. Nao existe "meio contrato" para
diluir risco. Logo, a alavanca que sobra nao e QUANTO se arrisca por perna --
e QUANTAS pernas podem estar posicionadas ao mesmo tempo.

Medido no historico de 5 anos com qty=1 e custo pessimista, as 6 pernas
selecionadas pela pesquisa ZERAM uma banca de R$5.000 (patrimonio negativo,
ruina em dez/2021) apesar de terminarem o periodo em R$38.060. O caminho mata
antes do retorno chegar.

Este modulo aplica o corte por risco AGREGADO, e nao por performance: as pernas
sao ordenadas pelo p95 do stop de 1 contrato e admitidas enquanto a soma couber
no orcamento. Selecionar por resultado na mesma janela que gerou o resultado e
o que a regra 7 do CLAUDE.md proibe.

Quem chama: `scripts/06_finalize_config.py` e `scripts/12_weekly_maintenance.py`.
Sem isso a manutencao semanal regrava a config com a carteira inteira e sem
teto de contratos, desfazendo o dimensionamento em silencio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest import features
from src.core.instruments import Instrument
from src.execution.live import LegCfg, RiskCfg
from src.strategies import families

BANCA_BRL = 5000.0            # banca de referencia; muda tudo abaixo
ORCAMENTO_RISCO_PCT = 15.0    # % da banca com TODAS as pernas posicionadas
MAX_QTY_POR_PERNA = 1.0       # teto de contratos por perna
KILL_SWITCH_QUANTIL = 0.01    # kill switch no p1 do PnL diario observado


def perfil_de_stop(d: pd.DataFrame, family: str, params: dict,
                   point_value: float) -> dict:
    """Quanto 1 contrato arrisca nesta perna, em R$, ao longo do historico."""
    sv, stv, _ = families.by_name(family).signals(d, dict(params))
    sel = (np.asarray(sv) != 0) & np.isfinite(stv)
    r1 = np.asarray(stv)[sel] * point_value
    if not len(r1):
        return {"p95": np.inf, "max": np.inf, "mediana": np.inf, "n": 0}
    return {"p95": float(np.percentile(r1, 95)), "max": float(r1.max()),
            "mediana": float(np.median(r1)), "n": int(len(r1))}


def aplica_orcamento(candidatas: list[dict], bars: pd.DataFrame, inst: Instrument,
                     banca: float = BANCA_BRL,
                     orcamento_pct: float = ORCAMENTO_RISCO_PCT,
                     max_qty: float = MAX_QTY_POR_PERNA,
                     log=None) -> tuple[list[LegCfg], dict]:
    """Filtra as pernas que cabem no orcamento e dimensiona cada uma.

    `candidatas` sao dicts no formato de `research.finalize.build_legs`.
    Devolve os LegCfg ja com `risk_brl` e `max_qty`, mais o perfil medido.

    `risk_brl` recebe o MAIOR stop observado da perna: com o teto de `max_qty`
    valendo, um valor generoso nao aumenta posicao -- so evita que sinais de
    stop largo sejam descartados por `qty<1` no motor.
    """
    fut = candidatas[0]["symbol"].startswith(("WIN", "WDO")) if candidatas else True
    d = features.build(bars, session_start="09:00" if fut else "10:00",
                       session_end="18:20" if fut else "17:55")

    perfil = {c["family"]: perfil_de_stop(d, c["family"], c["params"],
                                          inst.point_value)
              for c in candidatas}
    orc = banca * orcamento_pct / 100.0

    aceitas, acum = [], 0.0
    for c in sorted(candidatas, key=lambda x: perfil[x["family"]]["p95"]):
        p95 = perfil[c["family"]]["p95"]
        if acum + p95 > orc:
            if log:
                log.info("perna %s CORTADA: risco agregado iria a %.1f%% da "
                         "banca (teto %.0f%%)", c["family"],
                         100 * (acum + p95) / banca, orcamento_pct)
            continue
        acum += p95
        aceitas.append(LegCfg(symbol=c["symbol"], family=c["family"],
                              params=c["params"], timeframe=c["timeframe"],
                              risk_brl=float(np.ceil(perfil[c["family"]]["max"] / 10) * 10),
                              max_qty=max_qty))
        if log:
            log.info("perna %s mantida: +R$%.0f -> risco agregado %.1f%% da banca",
                     c["family"], p95, 100 * acum / banca)

    diag = {"perfil": perfil, "risco_agregado_brl": acum,
            "risco_agregado_pct": 100 * acum / banca, "banca": banca,
            "pnl_diario": _pnl_diario(d, aceitas, inst) if aceitas else None}
    return aceitas, diag


def _pnl_diario(d: pd.DataFrame, legs: list[LegCfg], inst: Instrument) -> pd.Series:
    """PnL diario da carteira aceita, no tamanho real (qty = max_qty).

    Roda o motor de backtest do projeto, entao o custo pessimista ja esta
    embutido. Serve para calibrar o kill switch a partir do que a carteira
    realmente faz num dia ruim, e nao de um numero redondo.
    """
    from src.backtest import engine
    from src.strategies import base

    tr = []
    for lg in legs:
        p = dict(lg.params)
        sv, stv, tgv = families.by_name(lg.family).signals(d, p)
        ii = base.make_intents(d, sv, stv, tgv,
                               exit_min=int(p.get("exit_min", 1050)),
                               last_entry_min=int(p.get("last_entry_min", 990)),
                               max_per_day=int(p.get("max_per_day", 1)))
        if len(ii.bar_idx):
            tr.append(engine.simulate(d, ii, inst, qty=lg.max_qty or 1.0))
    if not tr:
        return pd.Series(dtype="float64")
    t = pd.concat(tr, ignore_index=True)
    return t.groupby(t["exit_time"].dt.date)["net_brl"].sum()


def limites_de_risco(legs: list[LegCfg], perfil: dict, base: RiskCfg,
                     pnl_diario: pd.Series | None = None,
                     banca: float = BANCA_BRL) -> RiskCfg:
    """Kill switch e rede de catastrofe proporcionais a banca.

    O kill switch sai do p1 do PnL diario observado quando ele existe -- um
    numero redondo dispararia em dia ruim normal ou nunca. A rede de
    catastrofe cobre o pior caso de todas as pernas paradas no stop de uma vez.
    """
    if pnl_diario is not None and len(pnl_diario):
        kill = float(np.ceil(abs(pnl_diario.quantile(KILL_SWITCH_QUANTIL)) / 50) * 50)
    else:
        kill = float(np.ceil(sum(perfil[l.family]["p95"] for l in legs) / 50) * 50)
    catastrofe = float(np.ceil(sum(perfil[l.family]["max"] for l in legs) / 50) * 50)
    return RiskCfg(
        max_legs_abertas=len(legs),
        max_contratos_liquidos=float(sum(l.max_qty or 1.0 for l in legs)),
        daily_loss_limit_brl=min(kill, banca * 0.20),
        daily_profit_lock_brl=base.daily_profit_lock_brl,
        max_open_loss_brl=min(catastrofe, banca * 0.35),
        exit_all_minute=base.exit_all_minute,
        session_start_minute=base.session_start_minute,
    )

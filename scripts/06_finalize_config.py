"""Passo 6: congela config/live_config.json a partir de todo o historico.

Escolhe os parametros com a MESMA regra do walk-forward, so que usando o
historico inteiro como janela de treino -- o proximo pregao e o teste.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from research.finalize import build_legs  # noqa: E402
from src.core import instruments  # noqa: E402
from src.execution import config as cfg, sizing  # noqa: E402
from src.execution.live import LegCfg, RiskCfg  # noqa: E402
from src.ingest import bars  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="WIN$N")
    ap.add_argument("--timeframes", default="M15")
    ap.add_argument("--banca", type=float, default=sizing.BANCA_BRL,
                    help="banca em R$; define quantas pernas cabem e os limites")
    ap.add_argument("--orcamento-pct", type=float, default=sizing.ORCAMENTO_RISCO_PCT,
                    help="%% da banca exposto com todas as pernas posicionadas")
    ap.add_argument("--max-qty", type=float, default=sizing.MAX_QTY_POR_PERNA,
                    help="teto de contratos por perna")
    ap.add_argument("--total-risk", type=float, default=900.0,
                    help="so alimenta os pesos relativos de build_legs; o risco "
                         "efetivo de cada perna vem do orcamento da banca")
    a = ap.parse_args()

    tfs = [t.strip() for t in a.timeframes.split(",") if t.strip()]
    risco_por_tf = a.total_risk / len(tfs)

    todas = []
    for tf in tfs:
        legs = build_legs(a.symbol, tf, risco_por_tf)
        print(f"\n--- {a.symbol} {tf}: {len(legs)} pernas selecionadas ---")
        for l in legs:
            print(f"  {l['family']:<12} peso={l['peso']:.2f} risco=R${l['risk_brl']:.0f} "
                  f"sharpe_treino={l['sharpe_treino']:.2f} n={l['n_trades_treino']}")
            print(f"       {l['chave']}")
        todas.extend(legs)

    if not todas:
        print("\nnenhuma perna passou o corte -- nada foi escrito.")
        return

    inst = instruments.get(a.symbol)
    legcfgs, diag = sizing.aplica_orcamento(
        todas, bars.load_bars(a.symbol, tfs[0]), inst, banca=a.banca,
        orcamento_pct=a.orcamento_pct, max_qty=a.max_qty)
    if not legcfgs:
        print(f"\nnenhuma perna cabe numa banca de R${a.banca:,.0f} -- nada escrito.")
        return
    cortadas = [l["family"] for l in todas
                if l["family"] not in [g.family for g in legcfgs]]
    print(f"\n--- banca R${a.banca:,.0f}: {len(legcfgs)} de {len(todas)} pernas cabem "
          f"(risco agregado {diag['risco_agregado_pct']:.1f}%)")
    if cortadas:
        print(f"    cortadas por orcamento de risco: {', '.join(cortadas)}")

    risk = sizing.limites_de_risco(legcfgs, diag["perfil"], RiskCfg(),
                                   pnl_diario=diag["pnl_diario"], banca=a.banca)
    cfg.save(legcfgs, risk, meta={
        "gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
        "banca_brl": a.banca,
        "risco_agregado_pct": round(diag["risco_agregado_pct"], 1),
        "cortadas": cortadas,
        "detalhe": [{k: v for k, v in l.items() if k != "params"} for l in todas],
    })
    print(f"\nescrito em {cfg.LIVE_CONFIG}")
    print(f"risco agregado R${diag['risco_agregado_brl']:.0f} "
          f"({diag['risco_agregado_pct']:.1f}% da banca)  |  "
          f"kill switch R${risk.daily_loss_limit_brl:.0f}  |  "
          f"catastrofe R${risk.max_open_loss_brl:.0f}  |  "
          f"teto {risk.max_contratos_liquidos:.0f} contratos")


if __name__ == "__main__":
    main()

"""Relatorio operacional e conferencia entre o que foi simulado e o que ocorreu.

A parte mais importante deste modulo nao e o PnL: e a coluna de SLIPPAGE
REALIZADO. O backtest assume 1 tick contra em cada ponta; se na pratica for
pior, todo o resultado esperado muda. Essa comparacao e o unico jeito de
descobrir isso antes de o dinheiro sumir.
"""
from __future__ import annotations

import datetime as dt

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from src.core import instruments, paths
from src.core.mt5session import account_summary, mt5_session
from src.execution.live import MAGIC_BASE

REPORT_DIR = paths.LOGS / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_deals(days_back: int = 5) -> pd.DataFrame:
    end = dt.datetime.now() + dt.timedelta(hours=6)
    start = (dt.datetime.now() - dt.timedelta(days=days_back)).replace(hour=0, minute=0)
    deals = mt5.history_deals_get(start, end)
    if not deals:
        return pd.DataFrame()
    df = pd.DataFrame([d._asdict() for d in deals])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[df.get("magic", 0) >= MAGIC_BASE] if "magic" in df else df


def round_trips(deals: pd.DataFrame) -> pd.DataFrame:
    """Agrupa deals em CICLOS: da posicao zerada ate voltar a zero.

    Nao existe "round trip por position_id" nesta conta. Sendo NETTING, todos os
    deals do simbolo compartilham o mesmo position_id enquanto a posicao liquida
    nao zera -- entradas de pernas diferentes inclusive. A versao anterior
    tratava a primeira linha como entrada e TODAS as seguintes como saida, e por
    isso lia a segunda ENTRADA como se fosse o fechamento da primeira: em
    05/08/2026 reportou "1 trade fechado, PnL R$0,00" num dia de dois trades e
    +R$191 (auditoria).

    O ciclo e a menor unidade que o broker realmente fecha. A atribuicao por
    PERNA nao vem daqui e sim do livro virtual (logs/trades_live.csv): as ordens
    de reconciliacao saem todas com magic=MAGIC_BASE, entao o lado do broker nao
    sabe qual estrategia causou cada fill -- por desenho.
    """
    if deals.empty or "position_id" not in deals:
        return pd.DataFrame()
    d = deals.sort_values("time").copy()
    d["assinado"] = d.apply(
        lambda r: r["volume"] if r["type"] == mt5.DEAL_TYPE_BUY else -r["volume"],
        axis=1)
    cols_pnl = ["profit"] + [c for c in ("commission", "fee", "swap") if c in d]

    rows, atual, net = [], [], 0.0
    for _, r in d.iterrows():
        atual.append(r)
        net += r["assinado"]
        if abs(net) < 1e-9:                      # posicao voltou a zero: ciclo fechou
            g = pd.DataFrame(atual)
            abre = g.iloc[0]
            entradas = g[g["assinado"] * g.iloc[0]["assinado"] > 0]
            saidas = g[g["assinado"] * g.iloc[0]["assinado"] < 0]
            vol = float(entradas["volume"].sum())
            rows.append({
                "position_id": abre["position_id"],
                "symbol": abre["symbol"],
                "abertura": abre["time"],
                "fechamento": g["time"].max(),
                "side": "compra" if abre["type"] == mt5.DEAL_TYPE_BUY else "venda",
                "volume": vol,
                "n_deals": len(g),
                "preco_entrada": float(
                    (entradas["price"] * entradas["volume"]).sum() / vol) if vol else float("nan"),
                "preco_saida": float(
                    (saidas["price"] * saidas["volume"]).sum()
                    / saidas["volume"].sum()) if len(saidas) else float("nan"),
                "pnl_brl": float(g[cols_pnl].sum().sum()),
                "duracao_min": (g["time"].max() - abre["time"]).total_seconds() / 60.0,
                "comment_saida": str(g["comment"].iloc[-1]),
            })
            atual, net = [], 0.0
    return pd.DataFrame(rows)


def slippage_check(rt: pd.DataFrame) -> pd.DataFrame:
    """Compara o custo realizado por trade com o que o backtest assume."""
    if rt.empty:
        return pd.DataFrame()
    out = []
    for r in rt.itertuples():
        root = "WIN$N" if r.symbol.startswith("WIN") else (
            "WDO$N" if r.symbol.startswith("WDO") else r.symbol)
        inst = instruments.get(root)
        assumido = inst.round_trip_cost(r.preco_entrada, r.volume)
        bruto = (r.preco_saida - r.preco_entrada) * inst.point_value * r.volume
        if r.side == "venda":
            bruto = -bruto
        out.append({"position_id": r.position_id, "symbol": r.symbol,
                    "pnl_liquido_broker": r.pnl_brl,
                    "pnl_bruto_precos": bruto,
                    "custo_implicito": bruto - r.pnl_brl,
                    "custo_assumido_backtest": assumido,
                    "diferenca": (bruto - r.pnl_brl) - assumido})
    return pd.DataFrame(out)


def daily_report(days_back: int = 1, save: bool = True) -> str:
    with mt5_session():
        acc = account_summary()
        deals = fetch_deals(days_back=days_back)
        rt = round_trips(deals)
        slip = slippage_check(rt)
        positions = mt5.positions_get() or []

    # O saldo da conta demo (~R$1 milhao) nao e a banca que se pretende usar.
    # Sem esta referencia um prejuizo de R$400 parece irrelevante, quando na
    # verdade e 8% do capital real e quase o kill switch do dia.
    from src.execution import config as _cfg, sizing
    try:
        _, _risk, _meta = _cfg.load()
        banca = float(_meta.get("banca_brl") or sizing.BANCA_BRL)
        kill = _risk.daily_loss_limit_brl
    except Exception:  # noqa: BLE001
        banca, kill = sizing.BANCA_BRL, float("nan")

    hoje = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"RELATORIO OPERACIONAL -- {hoje}",
         "=" * 62,
         f"conta {acc['login']} @ {acc['server']}  |  demo={acc['is_demo']}",
         f"saldo R$ {acc['balance']:,.2f}   patrimonio R$ {acc['equity']:,.2f}",
         f"BANCA DE REFERENCIA: R$ {banca:,.2f}  "
         f"(kill switch do dia: R$ {kill:,.2f})",
         f"posicoes abertas: {len(positions)}"]

    if rt.empty:
        L.append("\nnenhum trade fechado no periodo.")
    else:
        pnl = rt["pnl_brl"].sum()
        L += ["", f"ciclos fechados no broker: {len(rt)}   "
              f"PnL liquido: R$ {pnl:,.2f}   ({100*pnl/banca:+.2f}% da banca)",
              f"acerto por ciclo: {100*(rt['pnl_brl']>0).mean():.1f}%   "
              f"duracao media: {rt['duracao_min'].mean():.0f} min",
              "  (ciclo = da posicao zerada ate zerar de novo; conta NETTING "
              "funde as pernas.",
              "   atribuicao por estrategia: logs/trades_live.csv)"]
        if np.isfinite(kill) and kill > 0:
            L.append(f"consumo do kill switch: {100*max(-pnl, 0)/kill:.0f}% "
                     f"(R$ {max(-pnl, 0):,.2f} de R$ {kill:,.2f})")
        L.append("")
        L.append(rt[["symbol", "abertura", "side", "volume", "preco_entrada",
                     "preco_saida", "pnl_brl", "duracao_min"]].to_string(index=False))
        if not slip.empty:
            d = slip["diferenca"]
            L += ["", "CONFERENCIA DE CUSTO (realizado - assumido no backtest):",
                  f"  media por trade: R$ {d.mean():,.2f}   mediana: R$ {d.median():,.2f}",
                  f"  pior caso: R$ {d.max():,.2f}",
                  "  >>> valor positivo = o mundo real esta MAIS CARO que o backtest"]
            if d.mean() > 0.5:
                L.append("  *** ATENCAO: custo realizado acima do assumido. "
                         "Recalibrar instruments.py antes de aumentar risco. ***")

    txt = "\n".join(L)
    if save:
        f = REPORT_DIR / f"report_{dt.date.today().isoformat()}.txt"
        f.write_text(txt, encoding="utf-8")
        if not rt.empty:
            rt.to_csv(REPORT_DIR / f"trades_{dt.date.today().isoformat()}.csv", index=False)
    return txt

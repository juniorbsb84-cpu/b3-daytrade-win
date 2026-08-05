"""Especificacao de instrumentos e modelo de custo/friccao.

Filosofia: o backtest so vale se a friccao for PESSIMISTA. Todos os defaults
aqui sao carregados para o lado ruim de proposito:

  * entrada e saida a mercado (paga o spread inteiro, nao assume fila passiva);
  * stop sofre 1 tick EXTRA de slippage (stop nunca executa no preco exato);
  * emolumentos da B3 por lado, sem arredondar pra baixo;
  * corretagem configuravel (Clear = zero, mas mantemos um piso > 0 no default
    conservador de pesquisa para nao depender de uma isencao comercial).

Valores de multiplicador conferidos contra o terminal (04/08/2026):
  WIN: tick_size=5 pts, tick_value=R$1,00  -> R$0,20 por ponto de indice
  WDO: tick_size=0,5,   tick_value=R$5,00  -> R$10,00 por ponto de dolar
  acoes: tick R$0,01, R$1,00 por real de preco por acao
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Instrument:
    symbol: str
    kind: str                 # "future" | "stock"
    tick_size: float
    point_value: float        # BRL por 1.0 de preco por contrato/acao
    lot_step: float
    # friccao
    spread_ticks: float       # spread tipico, em ticks
    entry_slip_ticks: float   # ticks pagos ao entrar a mercado (>= spread/2)
    exit_slip_ticks: float    # ticks pagos ao sair a mercado
    stop_extra_slip_ticks: float  # slippage adicional quando a saida e um stop
    fee_per_side_brl: float   # custo fixo por contrato por lado (futuros)
    fee_rate_per_side: float  # custo proporcional ao notional por lado (acoes)
    session: str

    @property
    def tick_brl(self) -> float:
        return self.tick_size * self.point_value

    def notional(self, price: float, qty: float) -> float:
        return abs(price * self.point_value * qty)

    def fees(self, price: float, qty: float) -> float:
        """Custo de UM lado (entrada ou saida) para `qty` contratos/acoes."""
        return self.fee_per_side_brl * qty + self.fee_rate_per_side * self.notional(price, qty)

    def round_trip_cost(self, price: float, qty: float, exit_is_stop: bool = False) -> float:
        """Custo total ida+volta em BRL: slippage + emolumentos."""
        slip_ticks = self.entry_slip_ticks + self.exit_slip_ticks
        if exit_is_stop:
            slip_ticks += self.stop_extra_slip_ticks
        slip = slip_ticks * self.tick_size * self.point_value * qty
        return slip + self.fees(price, qty) * 2

    def cost_in_price(self, exit_is_stop: bool = False) -> float:
        """Parcela de custo expressa em unidades de preco (so a parte de slippage)."""
        t = self.entry_slip_ticks + self.exit_slip_ticks
        if exit_is_stop:
            t += self.stop_extra_slip_ticks
        return t * self.tick_size


# ---------------------------------------------------------------- catalogo ---

WIN = Instrument(
    symbol="WIN$N", kind="future",
    tick_size=5.0, point_value=0.20, lot_step=1.0,
    spread_ticks=1.0, entry_slip_ticks=1.0, exit_slip_ticks=1.0,
    stop_extra_slip_ticks=1.0,
    fee_per_side_brl=0.55,       # emolumento B3 day trade (~0,27) + folga de corretagem
    fee_rate_per_side=0.0,
    session="WIN",
)

WDO = Instrument(
    symbol="WDO$N", kind="future",
    tick_size=0.5, point_value=10.0, lot_step=1.0,
    spread_ticks=1.0, entry_slip_ticks=1.0, exit_slip_ticks=1.0,
    stop_extra_slip_ticks=1.0,
    fee_per_side_brl=1.30,       # emolumento mini dolar day trade + folga
    fee_rate_per_side=0.0,
    session="WDO",
)


def stock(symbol: str) -> Instrument:
    return Instrument(
        symbol=symbol, kind="stock",
        tick_size=0.01, point_value=1.0, lot_step=100.0,
        spread_ticks=1.0, entry_slip_ticks=1.0, exit_slip_ticks=1.0,
        stop_extra_slip_ticks=1.0,
        fee_per_side_brl=0.0,
        fee_rate_per_side=0.000250,   # 2,5 bps por lado (emol + liquidacao B3)
        session="STOCKS",
    )


CATALOG = {"WIN$N": WIN, "WDO$N": WDO}


def get(symbol: str) -> Instrument:
    if symbol in CATALOG:
        return CATALOG[symbol]
    return stock(symbol)


def optimistic(inst: Instrument) -> Instrument:
    """Variante otimista -- usada SO para medir quanto do resultado e friccao."""
    return replace(inst, entry_slip_ticks=0.0, exit_slip_ticks=0.0,
                   stop_extra_slip_ticks=0.0)


def brutal(inst: Instrument) -> Instrument:
    """Variante ainda mais pessimista -- teste de estresse de robustez."""
    return replace(inst,
                   entry_slip_ticks=inst.entry_slip_ticks + 1,
                   exit_slip_ticks=inst.exit_slip_ticks + 1,
                   stop_extra_slip_ticks=inst.stop_extra_slip_ticks + 1,
                   fee_per_side_brl=inst.fee_per_side_brl * 1.5,
                   fee_rate_per_side=inst.fee_rate_per_side * 1.5)

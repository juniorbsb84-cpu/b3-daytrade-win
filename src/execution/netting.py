"""Livro de pernas virtuais sobre uma conta NETTING.

A conta da Clear no MT5 opera em modo netting (margin_mode=0): duas ordens no
mesmo simbolo NAO viram duas posicoes, elas se fundem em uma so. Isso quebra o
desenho ingenuo de "uma posicao por estrategia com stop proprio no servidor".

Solucao: o motor mantem um LIVRO VIRTUAL de pernas, cada uma com seu lado,
tamanho, stop e alvo. A cada ciclo:

    posicao_alvo = soma(lado x tamanho das pernas abertas)
    delta        = posicao_alvo - posicao_real_no_broker
    se |delta| >= 1 -> manda UMA ordem a mercado do tamanho do delta

Os stops e alvos de cada perna sao avaliados localmente contra bid/ask a cada
ciclo. Como protecao contra o motor morrer com posicao aberta, um stop de
CATASTROFE e mantido no servidor sobre a posicao liquida, calculado pelo prejuizo
maximo aberto tolerado -- ele nao substitui os stops das pernas, e uma rede.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import MetaTrader5 as mt5

log = logging.getLogger("netting")


@dataclass
class VirtualLeg:
    magic: int
    family: str
    side: int              # +1 comprado, -1 vendido
    qty: float
    entry_price: float
    sl: float
    tp: float | None
    opened_minute: int
    exit_minute: int
    opened_at: str = ""

    def unrealized(self, price: float, point_value: float) -> float:
        return self.side * (price - self.entry_price) * point_value * self.qty


@dataclass
class NetBook:
    legs: list[VirtualLeg] = field(default_factory=list)

    # ------------------------------------------------------------ estado ---
    def to_json(self) -> str:
        return json.dumps([asdict(l) for l in self.legs], ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "NetBook":
        try:
            return cls([VirtualLeg(**d) for d in json.loads(raw)])
        except Exception:  # noqa: BLE001
            log.exception("livro corrompido -- comecando vazio")
            return cls()

    # ------------------------------------------------------------ leitura --
    def target_net(self) -> float:
        return sum(l.side * l.qty for l in self.legs)

    def has_leg(self, magic: int) -> bool:
        return any(l.magic == magic for l in self.legs)

    def open_count(self) -> int:
        return len(self.legs)

    # ------------------------------------------------------------ escrita --
    def add(self, leg: VirtualLeg) -> None:
        self.legs.append(leg)

    def pop_exits(self, bid: float, ask: float, minute_of_day: int,
                  force: str | None = None) -> list[tuple[VirtualLeg, str]]:
        """Remove e devolve as pernas que devem ser encerradas agora.

        Preco de referencia e sempre o LADO CONTRA: quem esta comprado sai no
        bid, quem esta vendido sai no ask. Isso reproduz o custo de saida que o
        backtest assume.
        """
        saem, ficam = [], []
        for l in self.legs:
            px = bid if l.side > 0 else ask
            motivo = None
            if force:
                motivo = force
            elif (l.side > 0 and px <= l.sl) or (l.side < 0 and px >= l.sl):
                motivo = "stop"
            elif l.tp is not None and ((l.side > 0 and px >= l.tp)
                                       or (l.side < 0 and px <= l.tp)):
                motivo = "alvo"
            elif minute_of_day >= l.exit_minute:
                motivo = "tempo"
            if motivo:
                saem.append((l, motivo))
            else:
                ficam.append(l)
        self.legs = ficam
        return saem

    def clear(self) -> list[VirtualLeg]:
        out, self.legs = self.legs, []
        return out

    # ------------------------------------------------------------- risco ---
    def protective_stop_price(self, price: float, point_value: float,
                              max_open_loss_brl: float) -> float | None:
        """Preco em que o prejuizo ABERTO da posicao liquida bate no teto.

        Devolve None quando a posicao esta zerada. Este stop e a rede de
        seguranca no servidor -- serve para o caso de o motor cair.
        """
        net = self.target_net()
        if abs(net) < 1e-9:
            return None
        # perda ja acumulada nas pernas + margem restante ate o teto
        aberto = sum(l.unrealized(price, point_value) for l in self.legs)
        folga = max_open_loss_brl + min(aberto, 0.0)
        folga = max(folga, 0.0)
        dist = folga / (abs(net) * point_value)
        return price - dist if net > 0 else price + dist


def broker_net_volume(symbol: str) -> float:
    """Volume liquido assinado da posicao no broker (0 se nao houver)."""
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return 0.0
    total = 0.0
    for p in pos:
        s = 1.0 if p.type == mt5.POSITION_TYPE_BUY else -1.0
        total += s * p.volume
    return total

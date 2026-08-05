"""Camada fina sobre o MT5 para envio e gestao de ordens.

Tudo o que fala com a corretora passa por aqui. Duas responsabilidades:
resolver o contrato correto e enviar ordem sem depender de suposicao sobre
o modo de preenchimento aceito.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import MetaTrader5 as mt5
import pandas as pd

log = logging.getLogger(__name__)

RETCODE_OK = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED,
              mt5.TRADE_RETCODE_DONE_PARTIAL}


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    comment: str
    order: int = 0
    deal: int = 0
    price: float = 0.0
    volume: float = 0.0


# ------------------------------------------------------- contrato vigente ---

_ROOT_RE = {"WIN": re.compile(r"^WIN[FGHJKMNQUVXZ]\d{2}$"),
            "WDO": re.compile(r"^WDO[FGHJKMNQUVXZ]\d{2}$")}


def resolve_front(root: str, min_days: int = 1) -> str | None:
    """Contrato mais proximo do vencimento que ainda tem `min_days` de vida.

    Nao usa tabela de vencimentos: le a data de expiracao do proprio terminal.

    `min_days=1` e medido, nao arbitrado: no dado oficial da B3 (PricRpt), em
    17 de 17 rolagens entre out/2023 e ago/2026 o contrato vigente ainda tinha
    18x a 44x o volume do proximo em D-1, e a liquidez so migra NO dia do
    vencimento. Com `min_days=2` o motor trocava em D-1 e passava um pregao
    inteiro no contrato ~25x menos liquido, seis vezes por ano.
    """
    rx = _ROOT_RE.get(root.upper())
    if rx is None:
        return None
    now = pd.Timestamp.utcnow().tz_localize(None)
    best, best_exp = None, None
    for s in mt5.symbols_get(f"{root}*"):
        if not rx.match(s.name):
            continue
        if not s.expiration_time:
            continue
        exp = pd.Timestamp(s.expiration_time, unit="s")
        if (exp - now).days < min_days:
            continue
        if best_exp is None or exp < best_exp:
            best, best_exp = s.name, exp
    return best


def resolve_trade_symbol(research_symbol: str) -> str:
    """Simbolo de PESQUISA -> simbolo NEGOCIAVEL hoje."""
    if research_symbol.startswith("WIN"):
        return resolve_front("WIN") or research_symbol
    if research_symbol.startswith("WDO"):
        return resolve_front("WDO") or research_symbol
    return research_symbol


# ------------------------------------------------------------- utilidades ---

def filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_RETURN
    fm = info.filling_mode
    if fm & 1:      # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if fm & 2:      # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def normalize_price(symbol: str, price: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return price
    ts = info.trade_tick_size or info.point
    if ts <= 0:
        return round(price, info.digits)
    return round(round(price / ts) * ts, info.digits)


def normalize_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return volume
    step = info.volume_step or 1.0
    v = max(info.volume_min, min(info.volume_max, volume))
    return round(round(v / step) * step, 8)


def get_position(symbol: str, magic: int | None = None):
    pos = mt5.positions_get(symbol=symbol)
    if not pos:
        return None
    for p in pos:
        if magic is None or p.magic == magic:
            return p
    return None


def open_positions(magic: int | None = None) -> list:
    pos = mt5.positions_get() or []
    return [p for p in pos if magic is None or p.magic == magic]


# ------------------------------------------------------------------ ordens ---

def market_order(symbol: str, side: int, volume: float, sl: float | None = None,
                 tp: float | None = None, magic: int = 0, comment: str = "",
                 deviation: int = 20, retries: int = 3) -> OrderResult:
    """Ordem a mercado com SL/TP anexados. `side`: +1 compra, -1 venda."""
    if not mt5.symbol_select(symbol, True):
        return OrderResult(False, -1, f"symbol_select falhou: {symbol}")
    volume = normalize_volume(symbol, volume)
    if volume <= 0:
        return OrderResult(False, -1, "volume zero")

    for attempt in range(retries):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            time.sleep(0.3)
            continue
        price = tick.ask if side > 0 else tick.bid
        if price <= 0:
            time.sleep(0.3)
            continue
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "deviation": deviation,
            "magic": magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": filling_mode(symbol),
        }
        if sl:
            req["sl"] = normalize_price(symbol, sl)
        if tp:
            req["tp"] = normalize_price(symbol, tp)

        r = mt5.order_send(req)
        if r is None:
            log.warning("order_send devolveu None (%s): %s", symbol, mt5.last_error())
            time.sleep(0.4)
            continue
        if r.retcode in RETCODE_OK:
            return OrderResult(True, r.retcode, r.comment, r.order, r.deal,
                               r.price, r.volume)
        log.warning("ordem rejeitada %s tentativa %d: retcode=%s comment=%s",
                    symbol, attempt + 1, r.retcode, r.comment)
        # requote / preco invalido -> tenta de novo com preco atualizado
        if r.retcode in (mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_OFF,
                         mt5.TRADE_RETCODE_PRICE_CHANGED):
            time.sleep(0.4)
            continue
        # SL/TP invalido -> tenta sem eles e anexa depois
        if r.retcode == mt5.TRADE_RETCODE_INVALID_STOPS and (sl or tp):
            sl, tp = None, None
            continue
        return OrderResult(False, r.retcode, r.comment)
    return OrderResult(False, -1, "esgotou tentativas")


def set_sltp(position, sl: float | None, tp: float | None) -> OrderResult:
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol,
           "position": position.ticket}
    if sl:
        req["sl"] = normalize_price(position.symbol, sl)
    if tp:
        req["tp"] = normalize_price(position.symbol, tp)
    r = mt5.order_send(req)
    if r is None:
        return OrderResult(False, -1, str(mt5.last_error()))
    return OrderResult(r.retcode in RETCODE_OK, r.retcode, r.comment)


def close_position(position, deviation: int = 30, retries: int = 4,
                   comment: str = "close") -> OrderResult:
    for _ in range(retries):
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            time.sleep(0.3)
            continue
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": float(position.volume),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": float(price),
            "deviation": deviation,
            "magic": position.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": filling_mode(position.symbol),
        }
        r = mt5.order_send(req)
        if r is not None and r.retcode in RETCODE_OK:
            return OrderResult(True, r.retcode, r.comment, r.order, r.deal, r.price, r.volume)
        time.sleep(0.4)
    return OrderResult(False, -1, "falhou ao encerrar")


def deals_today(magic: int | None = None) -> pd.DataFrame:
    now = pd.Timestamp.utcnow().tz_localize(None)
    start = now.normalize()
    deals = mt5.history_deals_get(start.to_pydatetime(),
                                  (now + pd.Timedelta(hours=6)).to_pydatetime())
    if not deals:
        return pd.DataFrame()
    df = pd.DataFrame([d._asdict() for d in deals])
    if magic is not None and "magic" in df:
        df = df[df["magic"] == magic]
    if "time" in df:
        df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

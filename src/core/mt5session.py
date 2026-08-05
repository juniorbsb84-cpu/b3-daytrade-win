"""Conexao unica e reutilizavel com o terminal MetaTrader 5."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

import MetaTrader5 as mt5

log = logging.getLogger(__name__)

_lock = threading.Lock()
_refcount = 0

# Ha mais de um terminal MT5 instalado nesta maquina (Clear e um generico
# logado em outra corretora/demo). mt5.initialize() sem caminho se conecta ao
# que responder primeiro pela IPC -- em 05/08/2026 isso ligou o motor na conta
# errada (XPMT5-DEMO em vez da Clear) sem erro nenhum, porque as duas sao
# demo. O caminho fixa QUAL terminal; CONTA_ESPERADA e a segunda trava, contra
# o caso de a Clear tambem estar logada na conta errada.
TERMINAL_PATH = r"C:\Program Files\Clear Investimentos MT5 Terminal\terminal64.exe"
CONTA_ESPERADA = 1199739157


class MT5Error(RuntimeError):
    pass


def _initialize() -> None:
    if not mt5.initialize(path=TERMINAL_PATH):
        raise MT5Error(f"mt5.initialize({TERMINAL_PATH}) falhou: {mt5.last_error()}")
    ai = mt5.account_info()
    if ai is not None and ai.login != CONTA_ESPERADA:
        mt5.shutdown()
        raise MT5Error(
            f"terminal da Clear esta logado na conta {ai.login} "
            f"(esperado {CONTA_ESPERADA}) -- corrija o login no terminal "
            "antes de rodar o motor."
        )


@contextmanager
def mt5_session():
    """Context manager reentrante. Fecha a conexao so quando o ultimo sai."""
    global _refcount
    with _lock:
        if _refcount == 0:
            _initialize()
        _refcount += 1
    try:
        yield mt5
    finally:
        with _lock:
            _refcount -= 1
            if _refcount == 0:
                mt5.shutdown()


def account_summary() -> dict:
    ai = mt5.account_info()
    ti = mt5.terminal_info()
    if ai is None or ti is None:
        raise MT5Error(f"sem account/terminal info: {mt5.last_error()}")
    return {
        "login": ai.login,
        "server": ai.server,
        "company": ai.company,
        "is_demo": ai.trade_mode == 0,
        "trade_mode": ai.trade_mode,
        "currency": ai.currency,
        "balance": ai.balance,
        "equity": ai.equity,
        "margin_free": ai.margin_free,
        "terminal_connected": ti.connected,
        "trade_allowed": ti.trade_allowed,
        "terminal_path": ti.path,
    }


def assert_demo() -> dict:
    """Trava de seguranca: aborta se a conta conectada NAO for demo.

    Toda execucao automatica deste projeto roda em demo. Dinheiro real exige
    mudanca explicita e consciente de configuracao pelo dono da conta.
    """
    s = account_summary()
    if not s["is_demo"]:
        raise MT5Error(
            f"CONTA REAL DETECTADA (login={s['login']} server={s['server']}). "
            "Execucao automatica bloqueada por seguranca."
        )
    return s


def symbol_spec(symbol: str) -> dict:
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5Error(f"simbolo desconhecido: {symbol}")
    return {
        "symbol": symbol,
        "digits": info.digits,
        "point": info.point,
        "tick_size": info.trade_tick_size,
        "tick_value": info.trade_tick_value,
        "contract_size": info.trade_contract_size,
        "volume_min": info.volume_min,
        "volume_step": info.volume_step,
        "volume_max": info.volume_max,
        "path": info.path,
        "expiration_time": info.expiration_time,
        "filling_mode": info.filling_mode,
    }


def ensure_selected(symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.symbol_select(symbol, True)
    if not info.visible:
        return mt5.symbol_select(symbol, True)
    return True

"""Harvester continuo do L2 (DOM) do mini indice.

O terminal da Clear expoe profundidade REAL para o contrato vigente do WIN:
10 niveis de cada lado, plenamente populados (medido em 05/08/2026 -- bem
melhor que o L2 de acoes deste projeto, onde so 5 de 10 niveis vinham
preenchidos). `market_book_get` e polling, nao evento: o MT5 python nao
oferece callback assincrono, entao o loop consulta o book em alta frequencia
e so grava quando o snapshot muda de verdade (evita linha repetida a cada
poll ocioso).

Formato: uma linha por MUDANCA real do book, larga (10 niveis por lado numa
so linha), nao uma linha por nivel -- mais compacto e mais facil de
reconstruir o estado em qualquer instante (basta pegar a ultima linha
anterior ao timestamp desejado).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from src.core import paths
from src.core.mt5session import mt5_session
from src.execution import broker

log = logging.getLogger(__name__)

N_NIVEIS = 10
POLL_SECONDS = 0.05          # ~20Hz; o book muda poucas vezes por segundo
FLUSH_A_CADA_LINHAS = 200
FLUSH_A_CADA_SEGUNDOS = 2.0

COLS = (["ts_ms", "symbol"]
        + [f"bid{i}_px" for i in range(1, N_NIVEIS + 1)]
        + [f"bid{i}_vol" for i in range(1, N_NIVEIS + 1)]
        + [f"ask{i}_px" for i in range(1, N_NIVEIS + 1)]
        + [f"ask{i}_vol" for i in range(1, N_NIVEIS + 1)])

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS l2_snapshots (
    ts_ms INTEGER, symbol TEXT,
    {', '.join(f'{c} REAL' for c in COLS[2:])},
    PRIMARY KEY (ts_ms)
);
CREATE INDEX IF NOT EXISTS idx_l2_symbol ON l2_snapshots (symbol, ts_ms);
"""


def _snapshot_para_linha(book, ts_ms: int, symbol: str) -> dict | None:
    """BookInfo[] -> dict largo com N_NIVEIS por lado, ou None se book vazio."""
    if not book:
        return None
    bids = sorted((b for b in book if b.type == mt5.BOOK_TYPE_BUY),
                 key=lambda b: -b.price)[:N_NIVEIS]
    asks = sorted((b for b in book if b.type == mt5.BOOK_TYPE_SELL),
                 key=lambda b: b.price)[:N_NIVEIS]
    row = {"ts_ms": ts_ms, "symbol": symbol}
    for i in range(N_NIVEIS):
        row[f"bid{i+1}_px"] = bids[i].price if i < len(bids) else None
        row[f"bid{i+1}_vol"] = bids[i].volume_dbl if i < len(bids) else None
        row[f"ask{i+1}_px"] = asks[i].price if i < len(asks) else None
        row[f"ask{i+1}_vol"] = asks[i].volume_dbl if i < len(asks) else None
    return row


def _assinatura(row: dict) -> tuple:
    """Tupla comparavel para detectar se o book realmente mudou."""
    return tuple(row[c] for c in COLS if c not in ("ts_ms",))


def _db_do_dia(dia: str) -> Path:
    d = paths.DATA / "l2_win"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"l2_win_{dia}.db"


def harvest(symbol_pesquisa: str = "WIN$N",
           inicio_min: int = 9 * 60, fim_min: int = 18 * 60 + 10,
           max_seconds: float | None = None) -> None:
    """Loop principal: assina o book do contrato vigente e grava mudancas.

    Roda ate `fim_min` (minuto do dia) ou `max_seconds`, o que vier primeiro.
    Nao envia ordem nenhuma -- e so leitura de mercado.
    """
    with mt5_session():
        symbol = broker.resolve_trade_symbol(symbol_pesquisa)
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"nao foi possivel selecionar {symbol}")
        if not mt5.market_book_add(symbol):
            raise RuntimeError(f"market_book_add({symbol}) falhou: {mt5.last_error()}")
        log.info("assinado book de %s (contrato vigente de %s)", symbol, symbol_pesquisa)

        dia = pd.Timestamp.now().strftime("%Y%m%d")
        db_path = _db_do_dia(dia)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        placeholders = ",".join("?" * len(COLS))
        insert_sql = f"INSERT OR IGNORE INTO l2_snapshots ({','.join(COLS)}) VALUES ({placeholders})"

        buffer: list[tuple] = []
        ultima_assinatura = None
        t0 = time.time()
        ultimo_flush = t0
        n_capturados = n_polls = 0

        try:
            while True:
                agora = time.time()
                if max_seconds and agora - t0 > max_seconds:
                    log.info("tempo maximo atingido -- encerrando harvester")
                    break
                mod = pd.Timestamp.now()
                minuto = mod.hour * 60 + mod.minute
                if minuto >= fim_min:
                    log.info("fim da janela de captura (%d min) -- encerrando", fim_min)
                    break
                if minuto < inicio_min:
                    time.sleep(5)
                    continue

                book = mt5.market_book_get(symbol)
                n_polls += 1
                row = _snapshot_para_linha(book, int(agora * 1000), symbol)
                if row is not None:
                    assinatura = _assinatura(row)
                    if assinatura != ultima_assinatura:
                        ultima_assinatura = assinatura
                        buffer.append(tuple(row[c] for c in COLS))
                        n_capturados += 1

                if buffer and (len(buffer) >= FLUSH_A_CADA_LINHAS
                              or agora - ultimo_flush >= FLUSH_A_CADA_SEGUNDOS):
                    conn.executemany(insert_sql, buffer)
                    conn.commit()
                    buffer.clear()
                    ultimo_flush = agora

                time.sleep(POLL_SECONDS)
        finally:
            if buffer:
                conn.executemany(insert_sql, buffer)
                conn.commit()
            conn.close()
            mt5.market_book_release(symbol)
            log.info("harvester encerrado: %d snapshots gravados de %d polls "
                     "(%.1f%% mudou o book) -> %s",
                     n_capturados, n_polls,
                     100 * n_capturados / n_polls if n_polls else 0.0, db_path)

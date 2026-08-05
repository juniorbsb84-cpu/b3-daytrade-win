"""Passo 14: harvest diario do PricRpt oficial da B3 -- somente mini indice WIN.

Busca todo dia UTIL desde o ultimo pregao gravado com status='ok' ate ontem.
Nao busca o dia corrente: a B3 costuma publicar o PR{data}.zip com atraso (as
vezes so a noite ou no dia seguinte). Por buscar TODOS os pendentes (nao so
"ontem"), se a B3 atrasar ou o harvest falhar um dia, a proxima execucao
recupera o buraco sozinha.

Trazido para dentro do projeto em 05/08/2026 (antes vivia em
"New OpenCode Project", fora do repositorio). Ver src/ingest/b3_pricrpt.py.

Uso:
    python scripts/14_harvest_win_diario.py
"""
import argparse
import datetime as dt
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import paths  # noqa: E402
from src.ingest.b3_pricrpt import SCHEMA, dias_uteis, pendentes, processar_dia  # noqa: E402

DB_PADRAO = paths.DATA / "oficial_b3" / "win_diario.db"
LOG = paths.LOGS / "harvest_win_diario.log"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PADRAO))
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, encoding="utf-8")])
    log = logging.getLogger()

    db = Path(a.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    ontem = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    todos = dias_uteis("2023-10-02", ontem)
    faltam = pendentes(db, todos)

    if not faltam:
        log.info("nada pendente -- base ja cobre todos os pregoes uteis ate %s", ontem)
        return 0

    log.info("%d pregao(oes) pendente(s): %s", len(faltam),
             ", ".join(faltam) if len(faltam) <= 6 else f"{faltam[0]}..{faltam[-1]}")

    tmp = Path(tempfile.gettempdir()) / "b3_win_diario"
    tmp.mkdir(parents=True, exist_ok=True)

    ok = vazio = erro = 0
    for data in faltam:
        status, n, dur = processar_dia(data, db, tmp)
        if status == "ok":
            ok += 1
            log.info("%s: ok | %d registros WIN | %.1fs", data, n, dur)
        elif status == "vazio":
            vazio += 1
            log.info("%s: vazio (B3 ainda nao publicou ou feriado)", data)
        else:
            erro += 1
            log.warning("%s: %s", data, status)

    log.info("fim: ok=%d vazio=%d erro=%d de %d pendentes", ok, vazio, erro, len(faltam))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

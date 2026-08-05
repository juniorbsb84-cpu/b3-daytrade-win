"""Passo 13: harvester continuo do L2 (DOM) do mini indice, contrato vigente.

So leitura de mercado -- nao envia ordem. Grava em data/l2_win/l2_win_AAAAMMDD.db,
uma linha por mudanca real do book (nao por poll).

Uso:
    python scripts/13_harvest_l2_win.py
    python scripts/13_harvest_l2_win.py --minutes 5     # teste curto
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import paths  # noqa: E402
from src.ingest.l2_harvester import harvest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=None,
                    help="encerra apos N minutos (teste); default: ate fim_min")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(paths.LOGS / "l2_harvest_win.log", encoding="utf-8")])

    harvest(max_seconds=a.minutes * 60 if a.minutes else None)


if __name__ == "__main__":
    main()

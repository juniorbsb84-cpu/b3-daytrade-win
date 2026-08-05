"""Motor ao vivo (conta DEMO). Uso:

    python scripts/10_run_live.py            # opera segundo config/live_config.json
    python scripts/10_run_live.py --dry      # so registra sinais, nao envia ordem
    python scripts/10_run_live.py --minutes 30
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import paths  # noqa: E402
from src.execution import config as cfg  # noqa: E402
from src.execution.live import LiveEngine  # noqa: E402


def setup_logging(dry: bool) -> None:
    logfile = paths.LOGS / "live.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    logging.getLogger().info("=== motor iniciado (dry_run=%s) ===", dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="nao envia ordens")
    ap.add_argument("--minutes", type=float, default=None,
                    help="encerra o loop apos N minutos")
    ap.add_argument("--poll", type=float, default=5.0)
    a = ap.parse_args()

    setup_logging(a.dry)
    legs, risk, meta = cfg.load()
    if not legs:
        logging.error("nenhuma perna configurada -- nada a fazer")
        return
    logging.info("config gerada em %s | %d pernas", meta.get("gerado_em", "?"), len(legs))
    eng = LiveEngine(legs, risk, dry_run=a.dry, poll_seconds=a.poll)
    eng.run(max_seconds=a.minutes * 60 if a.minutes else None)


if __name__ == "__main__":
    main()

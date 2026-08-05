"""Caminhos canonicos do projeto. Tudo relativo a raiz, nada hardcoded fora daqui."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG = ROOT / "config"
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
UNIVERSE = DATA / "universe"
RESEARCH = ROOT / "research"
RESULTS = RESEARCH / "results"
LOGS = ROOT / "logs"
STATE = ROOT / "state"

for _p in (CONFIG, DATA, RAW, PROCESSED, UNIVERSE, RESEARCH, RESULTS, LOGS, STATE):
    _p.mkdir(parents=True, exist_ok=True)


def bars_path(symbol: str, timeframe: str) -> Path:
    """Arquivo parquet de barras. Symbol pode conter '$' (series continuas)."""
    safe = symbol.replace("$", "_").replace("\\", "_").replace("/", "_")
    d = RAW / "bars" / timeframe
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.parquet"


def ticks_path(symbol: str, day: str) -> Path:
    safe = symbol.replace("$", "_")
    d = RAW / "ticks" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{day}.parquet"

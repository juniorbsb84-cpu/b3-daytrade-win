"""Leitura/escrita da configuracao operacional (quais pernas estao no ar)."""
from __future__ import annotations

import json

from src.core import paths
from src.execution.live import LegCfg, RiskCfg

LIVE_CONFIG = paths.CONFIG / "live_config.json"


def save(legs: list[LegCfg], risk: RiskCfg, meta: dict | None = None) -> None:
    payload = {
        "meta": meta or {},
        "risk": risk.__dict__,
        "legs": [{"symbol": l.symbol, "family": l.family, "params": l.params,
                  "risk_brl": l.risk_brl, "timeframe": l.timeframe,
                  "max_qty": l.max_qty} for l in legs],
    }
    LIVE_CONFIG.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                      default=str), encoding="utf-8")


def load() -> tuple[list[LegCfg], RiskCfg, dict]:
    if not LIVE_CONFIG.exists():
        raise FileNotFoundError(
            f"{LIVE_CONFIG} nao existe -- rode scripts/06_finalize_config.py")
    raw = json.loads(LIVE_CONFIG.read_text(encoding="utf-8"))
    legs = [LegCfg(symbol=l["symbol"], family=l["family"], params=l["params"],
                   risk_brl=float(l["risk_brl"]),
                   timeframe=l.get("timeframe", "M5"),
                   max_qty=float(l.get("max_qty", 0.0))) for l in raw["legs"]]
    risk = RiskCfg(**raw["risk"])
    return legs, risk, raw.get("meta", {})


def parse_param_key(key: str) -> tuple[str, dict]:
    """'ORB|buf_atr=0.25|or_min=30|...' -> ('ORB', {...}) com tipos corretos."""
    parts = key.split("|")
    family = parts[0]
    out: dict = {}
    for kv in parts[1:]:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        if v in ("True", "False"):
            out[k] = v == "True"
        else:
            try:
                out[k] = int(v)
            except ValueError:
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
    return family, out

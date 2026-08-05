"""Passo 1: descobrir o universo negociavel e medir liquidez."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.mt5session import assert_demo, mt5_session  # noqa: E402
from src.ingest import universe  # noqa: E402

if __name__ == "__main__":
    with mt5_session():
        acc = assert_demo()
    print(f"conta: {acc['login']} @ {acc['server']} | demo={acc['is_demo']} | "
          f"saldo R${acc['balance']:,.2f}")
    sel = universe.build(top_n=60)
    print(sel.head(60).to_string(index=False))

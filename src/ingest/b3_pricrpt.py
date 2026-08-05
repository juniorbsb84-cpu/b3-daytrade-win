"""Extracao dos precos oficiais diarios da B3 (PR/PricRpt) -- so mini indice WIN.

Copia adaptada de `extrator_win.py`/`backfill_win.py` (originais na engenharia
reversa em `New OpenCode Project`, documentada em EXTRACAO_DADOS_B3.md -- NAO
MEXER la, essa pasta e material historico protegido). Trazida para dentro
deste projeto em 05/08/2026 a pedido explicito, para o harvest diario e seus
dados nao dependerem de nada fora de `E:\\Bolsa de Valores`.

Fonte: https://www.b3.com.br/pesquisapregao/download?filelist=PR{YYMMDD}.zip,
Estrutura: PR.zip -> PR.zip (aninhado) -> 4x XML BVBG.086.01/BVMF.217.01
Namespace dos registros: urn:bvmf.217.01.xsd
Retencao: out/2023 -> hoje (mais antigo retorna arquivo vazio).
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sqlite3
import time
import zipfile
from pathlib import Path

import requests
from lxml import etree

BASE_URL = "https://www.b3.com.br/pesquisapregao/download"
NS = "urn:bvmf.217.01.xsd"
TAIL = f"{{{NS}}}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
RETRIES = 3

MESES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
         "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

CAMPOS_ATTR = [
    "FrstPric", "MinPric", "MaxPric", "TradAvrgPric", "LastPric",
    "OscnPctg", "RglrTxsQty", "RglrTraddCtrcts", "NtlRglrVol",
    "OpnIntrst", "BestBidPric", "BestAskPric", "AdjstdQt",
    "PrvsAdjstdQt", "VartnPts", "AdjstdValCtrct",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS pr_dias (
    data TEXT PRIMARY KEY, status TEXT, n_win INTEGER, duracao REAL
);
CREATE TABLE IF NOT EXISTS win_diario (
    data TEXT, tckr TEXT, contrato TEXT, venc TEXT,
    first REAL, min REAL, max REAL, avg REAL, last REAL,
    oscn_pctg REAL, n_trades INTEGER, n_contratos INTEGER,
    vol_brl REAL, open_interest INTEGER, bid REAL, ask REAL,
    adjstd_qt REAL, prvs_adjstd_qt REAL, vartn_pts REAL,
    PRIMARY KEY (data, tckr)
);
CREATE INDEX IF NOT EXISTS idx_win_tckr ON win_diario (tckr, data);
CREATE INDEX IF NOT EXISTS idx_win_venc ON win_diario (venc, data);
"""


def parse_contrato(ticker: str) -> tuple[str | None, str | None]:
    m = re.fullmatch(r"WIN([FGHJKMNQUVXZ])(\d{1,2})", ticker)
    if not m:
        return None, None
    letra, ano = m.group(1), int(m.group(2))
    if ano < 100:
        ano += 2000
    if not (2020 <= ano <= 2040):
        return None, None
    return f"WIN{letra}{ano % 100}", f"{ano}-{MESES[letra]:02d}"


def baixar_pr(data_yyyymmdd: str, dest: Path | None = None) -> bytes:
    d = data_yyyymmdd.replace("-", "")[2:]
    url = f"{BASE_URL}?filelist=PR{d}.zip,"
    last_err = None
    for tent in range(RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
            r.raise_for_status()
            if dest is not None:
                dest.write_bytes(r.content)
            return r.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (tent + 1))
    raise RuntimeError(f"download PR{d}.zip falhou: {last_err}")


def xml_maior(inner: zipfile.ZipFile) -> str:
    nomes = sorted(inner.namelist())
    if not nomes:
        raise ValueError("zip interno vazio")
    return max(nomes, key=lambda n: inner.getinfo(n).file_size)


def parse_xml_win(xml_bytes: bytes) -> list[dict]:
    """Parse streaming do XML, retorna registros cujo ticker comeca com WIN."""
    out: list[dict] = []
    ctx = etree.iterparse(io.BytesIO(xml_bytes), events=("end",),
                          tag=f"{TAIL}PricRpt")
    for _, el in ctx:
        tckr = el.findtext(f"{TAIL}SctyId/{TAIL}TckrSymb")
        if tckr and tckr.startswith("WIN"):
            rec = {"tckr": tckr}
            data = el.findtext(f"{TAIL}TradDt/{TAIL}Dt")
            if data:
                rec["data"] = data
            attrs = el.find(f"{TAIL}FinInstrmAttrbts")
            if attrs is not None:
                for c in CAMPOS_ATTR:
                    v = attrs.findtext(f"{TAIL}{c}")
                    if v is not None:
                        rec[c] = v
            out.append(rec)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    return out


def numeric(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def inteiro(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def processar_dia(data: str, db_path: Path, tmp_dir: Path,
                  reter_zip: bool = False) -> tuple[str, int, float]:
    """Baixa e processa um pregao. Retorna (status, n_win, duracao)."""
    t0 = time.time()
    zip_tmp = tmp_dir / f"PR{data.replace('-', '')[2:]}.zip"
    conteudo = baixar_pr(data, dest=zip_tmp if reter_zip else None)
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
        if not z.namelist():
            return "vazio", 0, time.time() - t0
        z2 = zipfile.ZipFile(z.open(z.namelist()[0]))
        nome = xml_maior(z2)
        recs = parse_xml_win(z2.open(nome).read())
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        return f"erro:{type(e).__name__}", 0, time.time() - t0
    finally:
        if not reter_zip:
            try:
                zip_tmp.unlink(missing_ok=True)
            except OSError:
                pass

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO win_diario (data, tckr, contrato, venc,"
            " first, min, max, avg, last, oscn_pctg, n_trades, n_contratos,"
            " vol_brl, open_interest, bid, ask, adjstd_qt, prvs_adjstd_qt,"
            " vartn_pts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("data") or data, r["tckr"], *parse_contrato(r["tckr"]),
              numeric(r.get("FrstPric")), numeric(r.get("MinPric")),
              numeric(r.get("MaxPric")), numeric(r.get("TradAvrgPric")),
              numeric(r.get("LastPric")), numeric(r.get("OscnPctg")),
              inteiro(r.get("RglrTxsQty")), inteiro(r.get("RglrTraddCtrcts")),
              numeric(r.get("NtlRglrVol")), inteiro(r.get("OpnIntrst")),
              numeric(r.get("BestBidPric")), numeric(r.get("BestAskPric")),
              numeric(r.get("AdjstdQt")), numeric(r.get("PrvsAdjstdQt")),
              numeric(r.get("VartnPts")))
             for r in recs])
        conn.execute("INSERT OR REPLACE INTO pr_dias VALUES (?,?,?,?)",
                     (data, "ok", len(recs), time.time() - t0))
        conn.commit()
    finally:
        conn.close()
    return "ok", len(recs), time.time() - t0


def dias_uteis(inicio: str, fim: str) -> list[str]:
    out = []
    d = dt.date.fromisoformat(inicio)
    f = dt.date.fromisoformat(fim)
    while d <= f:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def pendentes(db: Path, dias: list[str]) -> list[str]:
    if not db.exists():
        return dias
    conn = sqlite3.connect(db)
    feitos = {r[0] for r in conn.execute("SELECT data FROM pr_dias WHERE status='ok'")}
    conn.close()
    return [d for d in dias if d not in feitos]

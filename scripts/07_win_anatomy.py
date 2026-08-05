"""Passo 7 (diagnostico): onde mora o retorno do mini indice.

Isto NAO propoe estrategia -- mede a anatomia do ativo para saber se as
familias testadas estao olhando para o lugar certo. Toda estatistica e
apresentada separando primeira metade (referencia) e segunda metade
(verificacao), para nao confundir padrao estavel com padrao de um periodo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest import features  # noqa: E402
from src.core import instruments, paths  # noqa: E402
from src.ingest.bars import load_bars  # noqa: E402

PV = instruments.WIN.point_value


def split_stats(df: pd.DataFrame, by: str, val: str) -> pd.DataFrame:
    n = len(df)
    a, b = df.iloc[:n // 2], df.iloc[n // 2:]
    out = pd.DataFrame({
        "n_1a": a.groupby(by)[val].size(),
        "media_1a_pts": a.groupby(by)[val].mean(),
        "n_2a": b.groupby(by)[val].size(),
        "media_2a_pts": b.groupby(by)[val].mean(),
    })
    out["media_total_pts"] = df.groupby(by)[val].mean()
    out["t_stat"] = df.groupby(by)[val].apply(
        lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0)
    out["mesmo_sinal"] = np.sign(out["media_1a_pts"]) == np.sign(out["media_2a_pts"])
    return out


def main():
    raw = load_bars("WIN$N", "M15")
    d = features.build(raw, session_start="09:00", session_end="18:20")
    print(f"WIN M15: {len(d)} barras, {d['date'].nunique()} pregoes, "
          f"{d.index[0].date()} -> {d.index[-1].date()}")

    daily = d.groupby("date").agg(abertura=("open", "first"), maxima=("high", "max"),
                                  minima=("low", "min"), fecha=("close", "last"))
    daily["intradia_pts"] = daily["fecha"] - daily["abertura"]
    daily["overnight_pts"] = daily["abertura"] - daily["fecha"].shift(1)
    daily["amplitude_pts"] = daily["maxima"] - daily["minima"]
    daily["dow"] = daily.index.dayofweek

    print("\n=== 1. de onde vem o retorno: intradia vs overnight ===")
    tot_i, tot_o = daily["intradia_pts"].sum(), daily["overnight_pts"].sum()
    print(f"  soma intradia  : {tot_i:>10,.0f} pts  (R$ {tot_i*PV:>10,.0f} por contrato)")
    print(f"  soma overnight : {tot_o:>10,.0f} pts  (R$ {tot_o*PV:>10,.0f} por contrato)")
    print(f"  amplitude diaria mediana: {daily['amplitude_pts'].median():,.0f} pts "
          f"(R$ {daily['amplitude_pts'].median()*PV:,.0f})")
    print("  >>> comprar e segurar so o pregao capta a parcela intradia; o resto"
          " exige carregar risco durante a noite.")

    print("\n=== 2. dia da semana (retorno intradia, pontos) ===")
    nomes = {0: "segunda", 1: "terca", 2: "quarta", 3: "quinta", 4: "sexta"}
    s = split_stats(daily.reset_index(), "dow", "intradia_pts")
    s.index = [nomes.get(i, i) for i in s.index]
    print(s.round(1).to_string())

    print("\n=== 3. hora do dia (retorno medio por barra M15, pontos) ===")
    d2 = d.copy()
    d2["ret_pts"] = d2["close"] - d2["open"]
    d2["hora"] = (d2["mod"] // 60)
    s = split_stats(d2, "hora", "ret_pts")
    s["soma_total_pts"] = d2.groupby("hora")["ret_pts"].sum()
    print(s.round(2).to_string())

    print("\n=== 4. a primeira hora prediz o resto do dia? ===")
    prim = d[d["mod"] < 10 * 60].groupby("date").agg(
        p_open=("open", "first"), p_close=("close", "last"))
    resto = d[d["mod"] >= 10 * 60].groupby("date").agg(
        r_open=("open", "first"), r_close=("close", "last"))
    j = prim.join(resto, how="inner").dropna()
    j["sinal_1h"] = np.sign(j["p_close"] - j["p_open"])
    j["ret_resto"] = j["r_close"] - j["r_open"]
    j = j.reset_index()
    print(split_stats(j, "sinal_1h", "ret_resto").round(1).to_string())
    print("  >>> media positiva no mesmo sinal = continuacao; negativa = reversao.")

    print("\n=== 5. amplitude do dia anterior prediz a do dia seguinte? ===")
    daily["amp_ant"] = daily["amplitude_pts"].shift(1)
    q = pd.qcut(daily["amp_ant"], 4, labels=["q1 baixa", "q2", "q3", "q4 alta"])
    tab = daily.assign(q=q).dropna(subset=["q"]).groupby("q", observed=True).agg(
        n=("amplitude_pts", "size"),
        amplitude_media=("amplitude_pts", "mean"),
        intradia_medio=("intradia_pts", "mean"),
        intradia_abs_medio=("intradia_pts", lambda x: x.abs().mean()))
    print(tab.round(1).to_string())
    print("  >>> se amplitude tem memoria, o dimensionamento por ATR ja aproveita isso.")

    print("\n=== 6. custo de friccao em perspectiva ===")
    inst = instruments.WIN
    rt = inst.round_trip_cost(140000, 1)
    print(f"  ida+volta com 1 contrato: R$ {rt:.2f} = {rt/PV:.1f} pontos de indice")
    print(f"  amplitude diaria mediana: {daily['amplitude_pts'].median():,.0f} pontos")
    print(f"  friccao consome {100*rt/PV/daily['amplitude_pts'].median():.2f}% da amplitude do dia")
    print("  (para comparar: em acao de R$15 com spread de 1 tick, so o spread ja e ~7 bps,"
          " contra amplitude diaria tipica de ~200 bps -> ~3,5%)")

    daily.to_parquet(paths.RESULTS / "win_daily_anatomy.parquet")


if __name__ == "__main__":
    main()

"""Passo 9: reexecuta um pregao passado passo a passo com o motor DE VERDADE.

Nao e uma simulacao paralela: e o proprio `LiveEngine`, com o relogio trocado,
lendo as barras do terminal e cortando tudo que for posterior ao instante
simulado. Exercita o caminho completo -- resolucao de contrato, features,
sinal, livro de pernas virtuais, saidas por stop/alvo/tempo e reconciliacao --
sem enviar nenhuma ordem.

E o unico jeito honesto de responder "o motor faz o que o backtest diz?" antes
de deixar dinheiro, mesmo de mentira, na mao dele.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.core import instruments, paths  # noqa: E402
from src.core.mt5session import mt5_session  # noqa: E402
from src.execution import broker, config as cfg  # noqa: E402
from src.execution.live import LiveEngine  # noqa: E402


class Replay:
    """Relogio controlado pelo script."""

    def __init__(self, t0: pd.Timestamp):
        self.t = t0

    def __call__(self) -> pd.Timestamp:
        return self.t


class BarQuote:
    """Cotacao sintetica a partir da ultima barra M1 fechada.

    Com o mercado fechado o terminal devolve bid/ask zerados, entao o replay
    precisa de outra fonte. Usa-se M1 (nao o timeframe do sinal) para o preco
    acompanhar o dia de perto e os stops serem avaliados com granularidade
    parecida com a do tempo real. O spread e o do instrumento, aplicado em
    torno do fechamento -- ou seja, o replay tambem paga spread.
    """

    def __init__(self, clock: Replay, spread_ticks: float, tick_size: float):
        self.clock = clock
        self.half = spread_ticks * tick_size / 2.0
        self.cache: dict[str, pd.DataFrame] = {}

    def _bars(self, sym: str) -> pd.DataFrame:
        if sym not in self.cache:
            import MetaTrader5 as mt5
            r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 60_000)
            df = pd.DataFrame(r)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            self.cache[sym] = df.set_index("time").sort_index()
        return self.cache[sym]

    def __call__(self, sym: str):
        df = self._bars(sym)
        sub = df[df.index <= self.clock()]
        if sub.empty:
            return None
        c = float(sub["close"].iloc[-1])
        if c <= 0:
            return None
        return c - self.half, c + self.half


def datas_com_barra(sym: str) -> set:
    """Datas que realmente tem barra M1 no terminal.

    Sem esta checagem o replay simula pregoes que nao existem: o `BarQuote`
    corta tudo que for posterior ao relogio e, se nada sobrar do dia pedido,
    cai no ultimo fechamento anterior -- o dia inteiro roda com preco
    congelado e gera entrada fantasma. Isso acontece nas duas pontas da
    janela: o dia corrente antes da abertura, e os dias antigos que ja
    passaram do teto de historico M1 do terminal (~111 pregoes em 60.000
    barras).
    """
    import MetaTrader5 as mt5
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 60_000)
    if r is None or len(r) == 0:
        return set()
    t = pd.to_datetime(pd.DataFrame(r)["time"], unit="s")
    return set(t.dt.date)


def escolhe_dias(com_barra: set, date_arg: str | None, n: int) -> list:
    """Ultimos `n` pregoes REAIS ate `date_arg` (padrao: o ultimo disponivel).

    Trabalha sobre os dias que tem barra, entao feriado e dia corrente sem
    pregao nao entram na conta -- `n` sempre significa n pregoes de verdade.
    """
    if not com_barra:
        return []
    fim = pd.Timestamp(date_arg).date() if date_arg else max(com_barra)
    return [pd.Timestamp(d) for d in sorted(d for d in com_barra if d <= fim)[-n:]]


def replay_day(dia: pd.Timestamp, legs, risk, step: int, log) -> dict:
    clock = Replay(dia + pd.Timedelta(minutes=risk.session_start_minute))
    inst = instruments.get(legs[0].symbol)
    quote = BarQuote(clock, inst.spread_ticks, inst.tick_size)
    eng = LiveEngine(legs, risk, dry_run=True, clock=clock, quote_fn=quote)
    for lg in eng.legs:
        lg.trade_symbol = broker.resolve_trade_symbol(lg.symbol)
        lg.last_bar = None
        lg.entries_today = 0
    eng.state = eng.state.__class__(day=dia.strftime("%Y-%m-%d"))
    eng.books = {}

    bruto = 0.0
    n_saidas = {"stop": 0, "alvo": 0, "tempo": 0}
    orig = eng._log_row
    eventos = []
    eng._log_row = lambda row: eventos.append(row)  # noqa: E731

    m = risk.session_start_minute
    while m <= risk.exit_all_minute:
        clock.t = dia + pd.Timedelta(minutes=m)
        eng._process_exits(clock.t)
        if m >= risk.exit_all_minute:
            eng._flatten("time_exit")
            break
        for leg in eng.legs:
            eng._process_leg(leg, clock.t)
        m += step

    for e in eventos:
        if e.get("evento") == "exit_virtual":
            bruto += float(e.get("bruto_brl", 0.0))
            n_saidas[e.get("motivo", "tempo")] = n_saidas.get(e.get("motivo"), 0) + 1
    entradas = sum(1 for e in eventos if e.get("evento") == "entry_virtual")
    abertas = sum(b.open_count() for b in eng.books.values())
    eng._log_row = orig
    return {"dia": dia.date(), "entradas": entradas, "bruto_brl": bruto,
            "abertas_no_fim": abertas, **n_saidas}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="AAAA-MM-DD (padrao: ultimo pregao)")
    ap.add_argument("--days", type=int, default=1, help="quantos pregoes reexecutar")
    ap.add_argument("--step", type=int, default=5, help="passo do relogio em minutos")
    a = ap.parse_args()

    if a.days > 1:
        logging.basicConfig(level=logging.WARNING,
                            format="%(levelname)s %(message)s",
                            handlers=[logging.StreamHandler(sys.stdout)])
        log = logging.getLogger()
        legs, risk, _ = cfg.load()
        linhas = []
        with mt5_session():
            dias = escolhe_dias(datas_com_barra(broker.resolve_trade_symbol(legs[0].symbol)),
                                a.date, a.days)
            if not dias:
                print("terminal nao devolveu barras M1 -- nada a reexecutar")
                return
            if len(dias) < a.days:
                print(f"AVISO: pedidos {a.days} pregoes, o terminal so tem {len(dias)}")
            for d in dias:
                linhas.append(replay_day(d, legs, risk, a.step, log))
                print(f"  {linhas[-1]['dia']}  entradas={linhas[-1]['entradas']}  "
                      f"bruto=R${linhas[-1]['bruto_brl']:>9,.2f}  "
                      f"stop={linhas[-1]['stop']} alvo={linhas[-1]['alvo']} "
                      f"tempo={linhas[-1]['tempo']}  abertas={linhas[-1]['abertas_no_fim']}",
                      flush=True)
        df = pd.DataFrame(linhas)
        df.to_csv(paths.RESULTS / "replay_multidia.csv", index=False)
        print(f"\n{len(df)} pregoes | entradas={df['entradas'].sum()} | "
              f"bruto total R${df['bruto_brl'].sum():,.2f} | "
              f"dias positivos {100*(df['bruto_brl']>0).mean():.0f}%")
        ruim = df[df["abertas_no_fim"] > 0]
        if len(ruim):
            print(f"ALERTA: {len(ruim)} pregoes terminaram com posicao aberta")
        else:
            print("nenhum pregao terminou com posicao aberta")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(paths.LOGS / "replay.log",
                                                      encoding="utf-8")])
    log = logging.getLogger()

    legs, risk, meta = cfg.load()

    with mt5_session():
        com_barra = datas_com_barra(broker.resolve_trade_symbol(legs[0].symbol))
        if a.date and pd.Timestamp(a.date).date() not in com_barra:
            # data explicita e sem barra: erro, nao cai calado no dia anterior.
            log.error("nao ha barra M1 de %s no terminal -- feriado, fim de "
                      "semana, pregao ainda nao aberto ou fora do teto de "
                      "historico M1", a.date)
            return
        dias = escolhe_dias(com_barra, a.date, 1)
        if not dias:
            log.error("terminal nao devolveu barras M1 -- nada a reexecutar")
            return
        dia = dias[0]

        clock = Replay(dia + pd.Timedelta(minutes=risk.session_start_minute))
        inst = instruments.get(legs[0].symbol)
        quote = BarQuote(clock, inst.spread_ticks, inst.tick_size)
        eng = LiveEngine(legs, risk, dry_run=True, clock=clock, quote_fn=quote)

        for lg in eng.legs:
            lg.trade_symbol = broker.resolve_trade_symbol(lg.symbol)
        eng._load_state(dia.strftime("%Y-%m-%d"))
        log.info("=== replay de %s | %d pernas | contrato %s ===",
                 dia.date(), len(legs), eng.legs[0].trade_symbol)

        m = risk.session_start_minute
        while m <= risk.exit_all_minute:
            clock.t = dia + pd.Timedelta(minutes=m)
            eng._process_exits(clock.t)
            if m >= risk.exit_all_minute:
                eng._flatten("time_exit")
                break
            for leg in eng.legs:
                eng._process_leg(leg, clock.t)
            m += a.step

        abertas = sum(b.open_count() for b in eng.books.values())
        log.info("=== fim do replay: %d entradas geradas, %d pernas ainda abertas ===",
                 eng.state.entries_today, abertas)
        if abertas:
            log.error("PROBLEMA: o motor terminou o dia com posicao aberta")


if __name__ == "__main__":
    main()

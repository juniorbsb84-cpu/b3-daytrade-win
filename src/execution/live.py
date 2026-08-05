"""Motor de execucao ao vivo (conta DEMO), desenhado para conta NETTING.

Fluxo de um ciclo:

    1. resolve a hora do servidor e trata virada de dia;
    2. fora da janela operacional -> zera tudo e dorme;
    3. avalia saidas das pernas VIRTUAIS contra bid/ask (stop, alvo, tempo);
    4. a cada barra fechada, avalia o sinal de cada perna configurada;
    5. reconcilia: manda UMA ordem com a diferenca entre a posicao alvo e a
       posicao real no broker;
    6. reposiciona o stop de catastrofe da posicao liquida no servidor.

Por que perna virtual: a conta e netting (ver src/execution/netting.py) --
seis estrategias no mesmo contrato viram UMA posicao no broker, entao os stops
individuais precisam viver no motor.

Trava dura: `assert_demo()` derruba o motor se a conta nao for de simulacao.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from src.backtest import features
from src.core import instruments, paths
from src.core.mt5session import assert_demo, mt5_session
from src.execution import broker
from src.execution.netting import NetBook, VirtualLeg, broker_net_volume
from src.strategies import families

log = logging.getLogger("live")

MAGIC_BASE = 770000
TRADE_LOG = paths.LOGS / "trades_live.csv"
STATE_FILE = paths.STATE / "live_state.json"
LOCK_FILE = paths.STATE / "live_engine.lock"

# Schema fixo do log de trades. Todo evento grava TODAS estas colunas (as que
# nao se aplicam ficam vazias), senao as linhas desalinham -- ver _log_row.
TRADE_LOG_COLS = [
    "ts", "evento", "symbol", "family", "side", "qty",
    "ref", "sl", "tp", "entry", "exit", "motivo", "bruto_brl",
    "alvo", "atual", "delta", "ok", "preco", "retcode", "comment",
]


class MotorJaRodando(RuntimeError):
    pass


def _processo_vivo(pid: int) -> bool:
    """PID existe nesta maquina? (Windows: sem signal 0, usa OpenProcess.)"""
    if pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:  # noqa: BLE001
        return False


@contextmanager
def instancia_unica():
    """Impede dois motores no ar ao mesmo tempo.

    O loop do motor nunca termina sozinho: fora da janela ele dorme e continua.
    Sem esta trava, uma execucao manual somada a tarefa agendada das 10:05
    coloca DOIS processos gerando sinal na mesma conta, escrevendo o mesmo
    arquivo de estado e mandando ordem cada um -- posicao dobrada em silencio.
    Foi encontrado exatamente assim em 05/08/2026 (auditoria).

    Lock com PID: se o arquivo existir mas o processo tiver morrido (queda,
    reboot), a trava e considerada orfa e liberada -- senao um crash exigiria
    limpeza manual antes do proximo pregao.
    """
    if LOCK_FILE.exists():
        try:
            dono = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            pid = int(dono.get("pid", -1))
        except Exception:  # noqa: BLE001
            pid = -1
        if _processo_vivo(pid):
            raise MotorJaRodando(
                f"ja existe um motor rodando (PID {pid}, iniciado em "
                f"{dono.get('inicio', '?')}). Encerre-o antes de subir outro."
            )
        log.warning("lock orfao de PID %s encontrado -- assumindo o lugar", pid)

    LOCK_FILE.write_text(json.dumps(
        {"pid": os.getpid(), "inicio": str(pd.Timestamp.now())}), encoding="utf-8")
    try:
        yield
    finally:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            log.exception("nao consegui remover o lock %s", LOCK_FILE)

TF_MAP = {"M5": (mt5.TIMEFRAME_M5, "5min"), "M15": (mt5.TIMEFRAME_M15, "15min"),
          "M30": (mt5.TIMEFRAME_M30, "30min"), "H1": (mt5.TIMEFRAME_H1, "60min")}


@dataclass
class LegCfg:
    symbol: str
    family: str
    params: dict
    risk_brl: float
    timeframe: str = "M15"
    max_qty: float = 0.0      # 0 = sem teto; >0 limita contratos por perna
    magic: int = 0
    trade_symbol: str = ""
    last_bar: pd.Timestamp | None = None
    entries_today: int = 0


@dataclass
class RiskCfg:
    max_legs_abertas: int = 6
    max_contratos_liquidos: float = 40
    daily_loss_limit_brl: float = 6000.0
    daily_profit_lock_brl: float = 0.0        # 0 = desligado
    max_open_loss_brl: float = 4000.0         # rede do stop de catastrofe
    exit_all_minute: int = 17 * 60 + 30
    session_start_minute: int = 9 * 60 + 5


@dataclass
class EngineState:
    day: str = ""
    halted: bool = False
    halt_reason: str = ""
    entries_today: int = 0
    legs_done: dict = field(default_factory=dict)
    book: str = "[]"


def _now_server() -> pd.Timestamp:
    """Hora do servidor (= hora de Brasilia). Ver src/core/timeutil.py."""
    for s in ("WIN$N", "WINQ26", "PETR4"):
        t = mt5.symbol_info_tick(s)
        if t is not None and t.time:
            srv = pd.Timestamp(t.time, unit="s")
            if abs((srv - pd.Timestamp.now()).total_seconds()) < 12 * 3600:
                return srv
    return pd.Timestamp.now()


def _mod(ts: pd.Timestamp) -> int:
    return ts.hour * 60 + ts.minute


class LiveEngine:
    def __init__(self, legs: list[LegCfg], risk: RiskCfg, dry_run: bool = False,
                 poll_seconds: float = 5.0, bars_lookback: int = 8000,
                 clock=None, quote_fn=None):
        self.legs = legs
        self.risk = risk
        self.dry_run = dry_run
        self.poll = poll_seconds
        self.bars_lookback = bars_lookback
        # `clock` permite reexecutar um pregao passado passo a passo (replay),
        # exercitando exatamente o mesmo codigo que roda ao vivo.
        self.clock = clock or _now_server
        # fonte de cotacao: ao vivo vem do tick; no replay vem da ultima barra
        # fechada. Trocar a fonte permite exercitar o motor com o mercado
        # fechado sem inventar preco.
        self.quote_fn = quote_fn or self._live_quote
        self.state = EngineState()
        self.books: dict[str, NetBook] = {}
        for i, lg in enumerate(self.legs):
            lg.magic = MAGIC_BASE + i

    # ------------------------------------------------------------ estado ---
    def _load_state(self, today: str) -> None:
        """Carrega o estado do dia -- inclusive QUANTAS VEZES cada perna ja entrou.

        Auditoria externa de 05/08/2026 (C1): `lg.entries_today` e atributo de
        instancia de `LegCfg`, reconstruido do zero (0) toda vez que o processo
        sobe. O livro virtual (`state.book`) so guarda pernas ainda ABERTAS --
        uma perna que ja entrou e ja SAIU no dia some do livro sem deixar
        rastro. Sem persistir a contagem em `legs_done`, um restart no meio do
        pregao zera `max_per_day` de toda perna ja fechada, permitindo nova
        entrada alem da cota diaria.
        """
        if STATE_FILE.exists():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if raw.get("day") == today:
                    self.state = EngineState(**raw)
                    self._restore_books()
                    for lg in self.legs:
                        lg.entries_today = int(self.state.legs_done.get(lg.family, 0))
                    return
            except Exception:  # noqa: BLE001
                log.exception("estado corrompido -- recomecando o dia")
        self.state = EngineState(day=today)
        self.books = {}
        for lg in self.legs:
            lg.entries_today = 0
        self._save_state()

    def _restore_books(self) -> None:
        self.books = {}
        legs = NetBook.from_json(self.state.book).legs
        for l in legs:
            sym = next((c.trade_symbol for c in self.legs if c.magic == l.magic), None)
            if sym is None:
                continue
            self.books.setdefault(sym, NetBook()).add(l)
        if legs:
            log.info("livro recuperado: %d pernas abertas", len(legs))

    def _save_state(self) -> None:
        todas = [l for b in self.books.values() for l in b.legs]
        self.state.book = NetBook(todas).to_json()
        STATE_FILE.write_text(json.dumps(self.state.__dict__, default=str,
                                         ensure_ascii=False, indent=2),
                              encoding="utf-8")

    def _log_row(self, row: dict) -> None:
        """Grava um evento no log de trades, sempre no MESMO conjunto de colunas.

        Antes cada tipo de evento escrevia so as suas chaves: o cabecalho saia
        do primeiro evento e as linhas seguintes desalinhavam (um 'reconcile'
        gravava o alvo na coluna 'family'). O arquivo ficava impossivel de ler
        como tabela -- o pandas nem parseava. Como este e o registro permanente
        do que o motor fez, o schema agora e fixo e as chaves ausentes viram
        vazio.
        """
        completo = {c: row.get(c, "") for c in TRADE_LOG_COLS}
        extras = set(row) - set(TRADE_LOG_COLS)
        if extras:
            log.warning("evento com campo fora do schema do log: %s", sorted(extras))
        pd.DataFrame([completo]).to_csv(
            TRADE_LOG, mode="a", header=not TRADE_LOG.exists(),
            index=False, encoding="utf-8")

    # -------------------------------------------------------------- dados ---
    def _closed_bars(self, sym: str, now: pd.Timestamp, tf: str) -> pd.DataFrame:
        r = mt5.copy_rates_from_pos(sym, TF_MAP[tf][0], 0, self.bars_lookback)
        if r is None or len(r) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(r)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time").sort_index()
        return df[df.index < now.floor(TF_MAP[tf][1])]   # descarta barra em formacao

    # ----------------------------------------------------------- cotacao ---
    def _live_quote(self, sym: str) -> tuple[float, float] | None:
        """(bid, ask) do tick corrente, ou None se a cotacao nao for utilizavel.

        Fora do pregao o terminal devolve bid/ask iguais a zero. Sem esta
        checagem o motor calcularia stop e alvo em cima de preco zero -- foi
        exatamente o que o replay de 04/08/2026 expos.
        """
        t = mt5.symbol_info_tick(sym)
        if t is None or t.bid <= 0 or t.ask <= 0 or t.ask < t.bid:
            return None
        return float(t.bid), float(t.ask)

    def _quote(self, sym: str) -> tuple[float, float] | None:
        q = self.quote_fn(sym)
        if q is None:
            return None
        bid, ask = q
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        return bid, ask

    # ------------------------------------------------------------- risco ---
    def _realized_today(self) -> float:
        d = broker.deals_today()
        if d.empty or "profit" not in d:
            return 0.0
        mine = d[d["magic"] >= MAGIC_BASE] if "magic" in d else d
        cols = [c for c in ("profit", "commission", "fee", "swap") if c in mine]
        return float(mine[cols].sum().sum()) if cols else 0.0

    def _unrealized(self) -> float:
        total = 0.0
        for sym, book in self.books.items():
            q = self._quote(sym)
            if q is None:
                continue
            inst = instruments.get(self._research_symbol(sym))
            mid = (q[0] + q[1]) / 2.0
            total += sum(l.unrealized(mid, inst.point_value) for l in book.legs)
        return total

    def _research_symbol(self, trade_symbol: str) -> str:
        for c in self.legs:
            if c.trade_symbol == trade_symbol:
                return c.symbol
        return trade_symbol

    def _check_halt(self) -> bool:
        if self.state.halted:
            return True
        pnl = self._realized_today() + self._unrealized()
        if pnl <= -abs(self.risk.daily_loss_limit_brl):
            self.state.halted = True
            self.state.halt_reason = f"limite de perda diaria: R${pnl:.2f}"
            log.error("KILL SWITCH -- %s", self.state.halt_reason)
            self._flatten("kill_switch")
            self._save_state()
            return True
        if (self.risk.daily_profit_lock_brl > 0
                and pnl >= self.risk.daily_profit_lock_brl):
            self.state.halted = True
            self.state.halt_reason = f"meta diaria travada: R${pnl:.2f}"
            log.warning("PARADA POR META -- %s", self.state.halt_reason)
            self._flatten("profit_lock")
            self._save_state()
            return True
        return False

    # -------------------------------------------------------- reconciliar ---
    def _reconcile(self, sym: str) -> bool:
        """Manda a ordem de diferenca. Devolve True se a posicao real ja bate
        (ou ficou batendo) com o alvo do livro virtual, False se uma ordem
        necessaria falhou -- para quem chama saber que a posicao real NAO
        mudou como esperado (ver `_flatten`).
        """
        book = self.books.get(sym)
        alvo = book.target_net() if book else 0.0
        atual = broker_net_volume(sym)
        delta = alvo - atual
        if abs(delta) < 1e-9:
            return True
        if abs(alvo) > self.risk.max_contratos_liquidos:
            log.error("posicao alvo %.0f acima do teto %.0f -- nao envia",
                      alvo, self.risk.max_contratos_liquidos)
            return False
        side = 1 if delta > 0 else -1
        vol = abs(delta)
        log.info("reconciliando %s: alvo=%.0f atual=%.0f -> ordem %s %.0f",
                 sym, alvo, atual, "COMPRA" if side > 0 else "VENDA", vol)
        if self.dry_run:
            self._log_row({"ts": self.clock(), "evento": "reconcile_dry", "symbol": sym,
                           "alvo": alvo, "atual": atual, "delta": delta})
            return True
        r = broker.market_order(sym, side, vol, magic=MAGIC_BASE, comment="reconcile")
        self._log_row({"ts": self.clock(), "evento": "reconcile", "symbol": sym,
                       "alvo": alvo, "atual": atual, "delta": delta,
                       "ok": r.ok, "preco": r.price, "retcode": r.retcode,
                       "comment": r.comment})
        if not r.ok:
            log.error("reconciliacao falhou em %s: %s %s", sym, r.retcode, r.comment)
        return r.ok

    def _protective_stop(self, sym: str) -> None:
        """Rede no servidor: stop na posicao liquida pelo prejuizo aberto maximo.

        Esta e a UNICA protecao que sobrevive ao motor cair -- os stops das
        pernas so existem no livro virtual, avaliados em memoria. Por isso todo
        resultado de `set_sltp` e logado e gravado no CSV: em 05/08/2026 uma
        auditoria foi incapaz de confirmar, so pelo log, se esta rede tinha sido
        de fato registrada na corretora (a funcao nao logava nada em caso de
        sucesso). Sem essa confirmacao explicita, ninguem sabe se a rede existe
        ate precisar dela -- tarde demais.
        """
        book = self.books.get(sym)
        if not book or not book.legs or self.dry_run:
            return
        pos = broker.get_position(sym)
        if pos is None:
            return
        q = self._quote(sym)
        if q is None:
            return
        inst = instruments.get(self._research_symbol(sym))
        mid = (q[0] + q[1]) / 2.0
        sl = book.protective_stop_price(mid, inst.point_value, self.risk.max_open_loss_brl)
        if sl is None:
            return
        sl = broker.normalize_price(sym, sl)
        if pos.sl and abs(pos.sl - sl) < inst.tick_size:
            return
        r = broker.set_sltp(pos, sl, None)
        self._log_row({"ts": self.clock(), "evento": "protective_stop", "symbol": sym,
                       "sl": sl, "preco": mid, "ok": r.ok, "retcode": r.retcode,
                       "comment": r.comment})
        if r.ok:
            log.info("stop de catastrofe atualizado em %s: sl=%.0f (rede registrada "
                     "na corretora)", sym, sl)
        else:
            log.error("FALHA ao registrar stop de catastrofe em %s: sl=%.0f "
                      "retcode=%s %s -- a posicao ESTA SEM rede no servidor",
                      sym, sl, r.retcode, r.comment)

    def _flatten(self, motivo: str) -> None:
        """Zera o livro virtual e manda a ordem que fecha a posicao real.

        Auditoria externa de 05/08/2026 (C3): a versao anterior dava `clear()`
        no livro incondicionalmente, ANTES de saber se a ordem de fechamento
        seria aceita. Se `_reconcile` falhasse (rejeicao, desconexao), o livro
        ja estava vazio -- `_protective_stop` para de agir (`not book.legs`
        devolve cedo) e o motor passa a achar que esta zerado, exatamente
        quando a posicao real continua aberta E sem rede nenhuma no servidor.
        Agora so se da `clear()` de fato quando `_reconcile` confirma sucesso;
        na falha, as pernas voltam ao livro para a rede de catastrofe seguir
        protegendo ate a proxima tentativa.
        """
        for sym, book in list(self.books.items()):
            if not book.legs:
                continue
            saem = list(book.legs)
            book.clear()
            if self._reconcile(sym):
                for l in saem:
                    self._log_row({"ts": self.clock(), "evento": "exit", "symbol": sym,
                                   "family": l.family, "side": l.side, "qty": l.qty,
                                   "entry": l.entry_price, "motivo": motivo})
            else:
                for l in saem:
                    book.add(l)
                log.error("zeragem de %s NAO confirmada -- livro restaurado, "
                          "posicao real continua aberta e sob protecao", sym)

    # ------------------------------------------------------------ sinais ---
    def _process_leg(self, leg: LegCfg, now: pd.Timestamp) -> None:
        book = self.books.setdefault(leg.trade_symbol, NetBook())
        if book.has_leg(leg.magic):
            return
        if leg.entries_today >= int(leg.params.get("max_per_day", 1)):
            return
        if sum(b.open_count() for b in self.books.values()) >= self.risk.max_legs_abertas:
            return

        bars = self._closed_bars(leg.trade_symbol, now, leg.timeframe)
        minimo = 500 if leg.timeframe == "M5" else 200
        if bars.empty or len(bars) < minimo:
            return
        ts = bars.index[-1]
        if ts.normalize() != now.normalize():
            # Auditoria externa de 05/08/2026 (C2): entre a abertura do
            # pregao e o fechamento da primeira barra M15 (09:00-09:15), a
            # ultima barra FECHADA ainda e a do dia anterior (ex.: 18:15 de
            # ontem). O gate abaixo usa o horario ATUAL (`now`), nao o da
            # barra do sinal -- entao passava, e o motor podia abrir posicao
            # com stop/alvo calculados em cima do fechamento de ontem, a
            # preco de hoje. O backtest nunca gera essa entrada: `make_intents`
            # filtra por `mod` da PROPRIA barra do sinal, e o mod de 18:15
            # (1095) e sempre maior que qualquer `last_entry_min` usado.
            return
        if leg.last_bar is not None and ts <= leg.last_bar:
            return
        leg.last_bar = ts

        m = _mod(now)
        if m > int(leg.params.get("last_entry_min", 16 * 60 + 30)):
            return
        if m >= self.risk.exit_all_minute - 15:
            return

        fut = leg.symbol.startswith(("WIN", "WDO"))
        d = features.build(bars, session_start="09:00" if fut else "10:00",
                           session_end="18:20" if fut else "17:55")
        if d.empty or d.index[-1] != ts:
            return

        strat = families.by_name(leg.family)
        sv, stv, tgv = strat.signals(d, dict(leg.params))
        i = len(d) - 1
        side = int(sv[i])
        if side == 0:
            return
        stop_dist = float(stv[i])
        if not np.isfinite(stop_dist) or stop_dist <= 0:
            return
        tgt_dist = float(tgv[i]) if np.isfinite(tgv[i]) else None

        inst = instruments.get(leg.symbol)
        q = self._quote(leg.trade_symbol)
        if q is None:
            log.warning("%s sem cotacao utilizavel -- sinal descartado", leg.trade_symbol)
            return
        ref = q[1] if side > 0 else q[0]        # compra no ask, venda no bid
        qty = np.floor(leg.risk_brl / (stop_dist * inst.point_value))
        qty = float(max(qty, 0.0))
        if leg.max_qty > 0:
            # Banca pequena: `risk_brl` precisa ser generoso para a perna nao
            # perder sinal de stop largo (qty<1 descarta), mas sem isso um stop
            # apertado compraria varios contratos e estouraria o risco.
            qty = min(qty, leg.max_qty)
        if qty < 1:
            log.info("%s/%s: sinal ignorado, risco R$%.0f nao paga 1 contrato "
                     "(stop=%.0f pts = R$%.2f)", leg.symbol, leg.family,
                     leg.risk_brl, stop_dist, stop_dist * inst.point_value)
            return

        vl = VirtualLeg(magic=leg.magic, family=leg.family, side=side, qty=qty,
                        entry_price=ref, sl=ref - side * stop_dist,
                        tp=(ref + side * tgt_dist) if tgt_dist else None,
                        opened_minute=m,
                        exit_minute=int(leg.params.get("exit_min", self.risk.exit_all_minute)),
                        opened_at=str(now))
        book.add(vl)
        leg.entries_today += 1
        self.state.entries_today += 1
        self.state.legs_done[leg.family] = leg.entries_today
        log.info("SINAL %s %s %s qty=%.0f ref=%.0f sl=%.0f tp=%s",
                 leg.family, leg.trade_symbol, "COMPRA" if side > 0 else "VENDA",
                 qty, ref, vl.sl, f"{vl.tp:.0f}" if vl.tp else "-")
        self._log_row({"ts": now, "evento": "entry_virtual", "symbol": leg.trade_symbol,
                       "family": leg.family, "side": side, "qty": qty, "ref": ref,
                       "sl": vl.sl, "tp": vl.tp})

    def _process_exits(self, now: pd.Timestamp) -> None:
        m = _mod(now)
        for sym, book in self.books.items():
            if not book.legs:
                continue
            q = self._quote(sym)
            if q is None:
                continue
            bid, ask = q
            for l, motivo in book.pop_exits(bid, ask, m):
                px = bid if l.side > 0 else ask
                inst = instruments.get(self._research_symbol(sym))
                bruto = l.unrealized(px, inst.point_value)
                log.info("SAIDA %s %s por %s: entrada=%.0f saida=%.0f bruto=R$%.2f",
                         l.family, sym, motivo, l.entry_price, px, bruto)
                self._log_row({"ts": now, "evento": "exit_virtual", "symbol": sym,
                               "family": l.family, "side": l.side, "qty": l.qty,
                               "entry": l.entry_price, "exit": px, "motivo": motivo,
                               "bruto_brl": bruto})

    # ------------------------------------------------------------- ciclo ---
    def run(self, max_seconds: float | None = None) -> None:
        t0 = time.time()
        with instancia_unica(), mt5_session():
            acc = assert_demo()
            log.info("conta %s @ %s | demo=%s | saldo R$%.2f | netting=%s",
                     acc["login"], acc["server"], acc["is_demo"], acc["balance"],
                     mt5.account_info().margin_mode == 0)
            for lg in self.legs:
                lg.trade_symbol = broker.resolve_trade_symbol(lg.symbol)
                mt5.symbol_select(lg.trade_symbol, True)
                log.info("perna %-12s %s -> %s | tf=%s | risco R$%.0f | magic=%d",
                         lg.family, lg.symbol, lg.trade_symbol, lg.timeframe,
                         lg.risk_brl, lg.magic)

            while True:
                if max_seconds and time.time() - t0 > max_seconds:
                    log.info("tempo maximo atingido -- encerrando o loop "
                             "(posicoes abertas NAO sao zeradas por isso)")
                    return
                try:
                    now = self.clock()
                    hoje = now.strftime("%Y-%m-%d")
                    if self.state.day != hoje:
                        self._load_state(hoje)
                        for lg in self.legs:
                            lg.last_bar = None
                        log.info("=== pregao %s ===", hoje)

                    m = _mod(now)
                    if now.dayofweek >= 5:
                        time.sleep(60)
                        continue
                    if m >= self.risk.exit_all_minute:
                        if any(b.legs for b in self.books.values()):
                            log.info("horario de zeragem -- encerrando tudo")
                            self._flatten("time_exit")
                            self._save_state()
                            time.sleep(30)
                            continue
                        # Nada aberto e pregao encerrado: sai de vez. Ficar em
                        # loop ate o Task Scheduler matar (12h) so acumula
                        # processo ocioso e aumenta a chance de dois motores
                        # coexistirem no dia seguinte.
                        log.info("pregao encerrado e sem posicao -- motor sai")
                        return
                    if m < self.risk.session_start_minute:
                        time.sleep(20)
                        continue

                    self._process_exits(now)
                    if not self._check_halt():
                        for leg in self.legs:
                            try:
                                self._process_leg(leg, now)
                            except Exception:  # noqa: BLE001
                                log.exception("erro na perna %s/%s", leg.symbol, leg.family)
                    for sym in list(self.books):
                        self._reconcile(sym)
                        self._protective_stop(sym)
                    self._save_state()
                except Exception:  # noqa: BLE001
                    log.exception("erro no ciclo principal")
                time.sleep(self.poll)

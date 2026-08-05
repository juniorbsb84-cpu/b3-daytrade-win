# Projeto B3 -- notas para quem (ou o que) for mexer nisto depois

## O que este projeto e

Day trade sistematico no mini indice (WIN) da B3, do dado bruto ate a execucao
automatica em conta demo. Barras de 15 minutos, zeragem as 17:30, zero
overnight, seis regras mecanicas em carteira. Sem machine learning, sem
discricao.

O que esta no ar e so o que sobreviveu a um teste que podia ter reprovado:
59 acoes, mini dolar, timeframe M5 e a ideia de operar o efeito overnight foram
todos reprovados por dado. A trilha completa esta em `RESULTADOS.md`.

## Regras que nao se negociam

1. **Fuso**: `pd.to_datetime(campo_do_mt5, unit='s'|'ms')` JA devolve hora de
   Brasilia. Nunca aplicar shift de 3h. Nunca usar `datetime.fromtimestamp`.
   Verificacao empirica em `src/core/timeutil.py`.
2. **Nada de dinheiro real por automacao**. `src/core/mt5session.assert_demo()`
   e chamado antes de qualquer ordem e derruba o motor se a conta nao for demo.
3. **Custo pessimista sempre**. O default de `src/core/instruments.py` supoe
   entrada e saida a mercado pagando o spread, mais 1 tick extra no stop.
   Resultado com custo otimista so serve para medir quanto do PnL e friccao.
4. **Sinal em `t`, entrada na abertura de `t+1`**. O motor implementa isso e
   `tests/test_no_lookahead.py` verifica que nenhuma feature enxerga o futuro.
5. **Stop e alvo na mesma barra = stop**. Sempre o pior caso.
6. **Alvo so executa com penetracao estrita** do preco (ordem limite nao
   preenche por encostar).
7. **Selecao de parametro so com dado de treino**. Qualquer numero medido na
   mesma janela em que o parametro foi escolhido e propaganda, nao resultado.
8. **A conta e NETTING**. Ver secao propria abaixo -- ignorar isso quebra a
   execucao em silencio.
9. **A banca manda em quantas pernas entram.** `src/execution/sizing.py` e a
   fonte unica da verdade: `BANCA_BRL`, o orcamento de risco agregado e o teto
   de contratos por perna. Quem gera config (`06_finalize_config.py`,
   `12_weekly_maintenance.py`) passa por ele obrigatoriamente. Montar `LegCfg`
   direto do `build_legs`, como os dois faziam antes de 05/08/2026, devolve a
   carteira inteira sem teto de contratos e leva a banca a ruina -- medido, nao
   suposto.

## A conta e netting (leia antes de mexer em execucao)

`account_info().margin_mode == 0` na Clear: duas ordens no mesmo simbolo NAO
viram duas posicoes, elas se fundem numa posicao liquida unica. Portanto:

* **stop por estrategia no servidor e impossivel** -- o SL de uma perna
  sobrescreveria o da outra;
* `src/execution/netting.py` mantem um livro de pernas VIRTUAIS: cada perna tem
  lado, tamanho, stop e alvo proprios, avaliados localmente contra bid/ask a
  cada ciclo;
* o motor calcula `posicao_alvo = soma(lado x tamanho)`, compara com a posicao
  real e manda **uma unica ordem com a diferenca** (`_reconcile`);
* no servidor fica apenas um **stop de catastrofe** sobre a posicao liquida,
  calculado pelo prejuizo aberto maximo tolerado. Ele nao substitui os stops das
  pernas -- e a rede para o caso de o motor morrer com posicao aberta.

Nao existe mais nenhum "watchdog que reenvia o stop da posicao": esse era o
desenho anterior, valido so em conta hedging, e foi removido.

## Armadilhas que ja custaram bug (nao reintroduzir)

1. **O dia corrente nao pode ser descartado.** `features.prepare()` remove dias
   degenerados por contagem de barras, mas preserva sempre o ULTIMO dia do
   quadro. Sem essa excecao o motor ao vivo joga fora o pregao em andamento e
   fica mudo ate ~10:40 -- justamente nos setups de abertura.
2. **Fora do pregao o terminal devolve bid/ask = 0.** Toda cotacao passa por
   `LiveEngine._quote()`, que rejeita preco nao positivo ou invertido. Sem isso
   o motor calcula stop e alvo em cima de preco zero.
3. **Dois motores no ar = posicao dobrada em silencio.** O loop nao termina
   sozinho: fora da janela ele dorme. Uma execucao manual somada a tarefa das
   10:05 poe dois processos mandando ordem na mesma conta e disputando o mesmo
   `state/live_state.json`. Existe agora `live.instancia_unica()` (lock com PID
   em `state/live_engine.lock`, orfao e assumido) e o motor **sai** quando o
   pregao encerra sem posicao. Achado em producao na auditoria de 05/08/2026.
4. **`mt5.initialize()` sem `path` conecta em terminal qualquer.** Ha dois MT5
   instalados nesta maquina (Clear e um generico logado na XP). Como as duas
   contas sao demo, `assert_demo()` nao pega. `mt5session` fixa o caminho do
   terminal da Clear **e** confere `login == CONTA_ESPERADA`. Em 05/08/2026 o
   motor rodou 3h20 na conta errada sem um unico erro.
5. **Log de trades com schema variavel corrompe o proprio registro.** Cada
   evento tem chaves diferentes; gravar so as chaves de cada um faz o cabecalho
   sair do primeiro evento e as linhas seguintes desalinharem (um `reconcile`
   gravava o alvo na coluna `family`). `_log_row` usa `TRADE_LOG_COLS` fixo.
6. **Nao existe round trip por `position_id` nesta conta.** Sendo netting,
   todos os deals do simbolo compartilham `position_id` enquanto a posicao nao
   zera -- entradas de pernas diferentes inclusive. `report.round_trips()`
   agrupa por CICLO (da posicao zerada ate zerar). A atribuicao por perna nao
   vem do broker: as ordens de reconciliacao saem todas com `magic=MAGIC_BASE`,
   entao so o livro virtual (`logs/trades_live.csv`) sabe qual estrategia
   causou cada fill.
7. **`_protective_stop()` precisa PROVAR que registrou o stop, nao so tentar.**
   E a UNICA protecao que sobrevive ao motor cair -- os stops das pernas so
   existem no livro virtual, avaliados em memoria. Em 05/08/2026, com posicao
   aberta 15:30-17:30, o historico de ordens da corretora nao mostrava NENHUMA
   acao `TRADE_ACTION_SLTP`, e a funcao nao logava nada em caso de sucesso: foi
   impossivel dizer, so pelo log, se a rede de catastrofe (R$1.400) tinha sido
   de fato registrada na Clear. Corrigido: todo resultado de `set_sltp` agora
   vira log explicito (sucesso ou `FALHA ... ESTA SEM rede no servidor`) e
   evento `protective_stop` gravado no CSV com `ok`/`retcode`. **Nao verificado
   com posicao real apos a correcao** -- proxima vez que houver posicao aberta,
   confirmar que o evento aparece em `logs/trades_live.csv`.
8. **`max_per_day` por perna nao sobrevivia a um restart no MEIO do dia.**
   `lg.entries_today` e atributo de `LegCfg`, reconstruido em 0 toda vez que o
   processo sobe. O livro virtual (`state.book`) so guarda pernas ABERTAS --
   uma perna que ja entrou e ja SAIU no dia some sem deixar rastro de que usou
   sua cota. Um restart liberava nova entrada alem de `max_per_day`. Corrigido:
   a contagem por familia agora vive em `EngineState.legs_done` (campo que ja
   existia no schema, nunca conectado) e e restaurada em `_load_state`.
9. **Sinal calculado em cima da barra de ONTEM podia virar entrada de HOJE.**
   Entre a abertura do pregao e o fechamento da 1a barra M15 (09:00-09:15), a
   ultima barra fechada disponivel ainda e a de ontem (~18:15). O gate de
   horario de entrada usava `_mod(now)` (hora ATUAL), nao o mod da PROPRIA
   barra do sinal -- por isso passava. O backtest nunca gera essa entrada
   (`make_intents` filtra pelo mod da barra do sinal, sempre maior que
   qualquer `last_entry_min`). Corrigido com `ts.normalize() != now.normalize()`
   logo apos pegar a ultima barra fechada.
10. **`_flatten()` esvaziava o livro virtual ANTES de saber se a ordem de
    fechar a posicao seria aceita.** Numa rejeicao (desconexao, erro do
    broker), o livro ja ficava vazio -- `_protective_stop` para de agir quando
    `not book.legs`, e o motor passa a achar que esta zerado exatamente quando
    a posicao real continua aberta e SEM rede nenhuma no servidor. Corrigido:
    `_reconcile()` agora devolve sucesso/falha; `_flatten()` so da `clear()`
    quando confirma, e devolve as pernas ao livro na falha.

Os itens 1-7 vieram da 1a auditoria (05/08, tarde); 8-10 de uma 2a auditoria
externa (05/08, noite) que revisou o codigo linha a linha e achou tres bugs
reais que a primeira passagem nao pegou -- inclusive porque `test_live_parity.py`
testava M5 (reprovado por dado) em vez de M15 (producao), entao o teste mais
caro do projeto nunca exercitou o timeframe que de fato roda. Corrigido junto.

Todos foram pegos por teste ou auditoria, nao por leitura casual de codigo.
Mantenha os testes.

## Testes (52, todos devem passar antes de qualquer deploy)

| Arquivo | O que trava |
|---|---|
| `tests/test_engine.py` (8) | as 7 regras pessimistas do backtest, uma a uma |
| `tests/test_netting.py` (10) | livro virtual: posicao alvo, lado do preco no stop, prioridade stop>tempo, stop de catastrofe |
| `tests/test_no_lookahead.py` (3) | features recalculadas com historico truncado batem valor a valor |
| `tests/test_config_roundtrip.py` (2) | a config operacional reproduz o sinal do backtest |
| `tests/test_live_parity.py` (2) | a visao do motor ao vivo == a visao do backtest |
| `tests/test_auditoria.py` (27) | as falhas da auditoria de 05/08: lock de instancia unica, lock orfao, schema do log de trades, stop de catastrofe sem confirmacao, ciclo em conta netting, quota por perna, sinal de barra de outro dia, zeragem sem confirmacao |

Ao mexer em features ou estrategia: rodar `test_live_parity.py` **e** um replay
de 25 pregoes (`scripts/09_replay_session.py --days 25`) antes de considerar
pronto. O replay usa o proprio `LiveEngine`, com relogio e cotacao trocados --
foi ele que expos a armadilha 2 acima.

## Como rodar, na ordem

```
python scripts/01_build_universe.py             # descobre e ranqueia o universo
python scripts/02_download_bars.py              # baixa e persiste todo o historico
python tests/test_engine.py                     # 8 testes do motor
python tests/test_netting.py                    # 10 testes do livro virtual
python tests/test_no_lookahead.py               # 3 testes de vazamento de futuro
python tests/test_config_roundtrip.py           # 2 testes de config
python tests/test_live_parity.py                # 2 testes de paridade ao vivo
python tests/test_auditoria.py                  # 27 testes das falhas da auditoria
python scripts/03_run_sweep.py                  # varredura ampla (6 familias x 61 ativos)
python scripts/04_win_portfolio.py              # carteira no instrumento vencedor
python scripts/05_montecarlo.py                 # distribuicao nula com a busca inteira
python scripts/07_win_anatomy.py                # diagnostico: onde mora o retorno
python scripts/08_combine_timeframes.py         # M5 e M15 sao a mesma aposta?
python scripts/06_finalize_config.py            # congela config/live_config.json
python scripts/09_replay_session.py --days 25   # reexecuta pregoes com o motor real
python scripts/10_run_live.py --dry             # motor sem enviar ordem
python scripts/11_daily_report.py               # relatorio + conferencia de custo
python scripts/12_weekly_maintenance.py         # dado novo, revalida, recongela
python scripts/13_harvest_l2_win.py             # captura o book L2 do contrato vigente
```

`scripts/diag_*.py` sao diagnosticos pontuais (modo da conta e limites de risco,
decomposicao intradia vs overnight, inspecao do resultado da varredura).

## Limites de dado do terminal (medidos, nao supostos)

| Timeframe | Teto por simbolo | Alcance real           |
|-----------|------------------|------------------------|
| M1        | 100.000 barras   | ~9 meses               |
| M5        | 100.000 barras   | WIN/WDO desde jan/2023; acoes desde dez/2021 |
| M15       | sem teto pratico | 5 anos                 |
| M30 / H1 / D1 | sem teto pratico | 5 anos             |
| ticks     | --               | ~120 a 365 dias        |

`copy_rates_from_pos` aceita no maximo 99.999 barras por chamada -- pedir
100.000 devolve vazio. `copy_rates_range` com janela longa em timeframe curto
devolve `Invalid params`; pagine por posicao.

Foi esse teto que motivou usar **M15 como base**: 5 anos contra 3,5 em M5.

## Onde nao pisar

* `research/win_deep.py` e `research/montecarlo.py` sao importados por processos
  filhos do multiprocessing. Editar esses arquivos com uma varredura rodando
  muda o codigo dos workers que ainda vao nascer.
* `print()` redirecionado para arquivo e bufferizado em bloco: script longo com
  `> log.txt` so mostra saida no fim. Para acompanhar progresso, cheque os
  artefatos em `research/results/` em vez do log.
* O universo e derivado do terminal, nao de lista fixa. Se a Clear mudar a
  oferta de simbolos, `01_build_universe.py` reflete sozinho.
* `WIN$N`/`WDO$N` sao series continuas, boas para pesquisa. Para operar,
  `broker.resolve_trade_symbol()` resolve o contrato vigente lendo a data de
  vencimento do proprio terminal -- nunca chute a letra do mes.
* **`resolve_front(min_days=1)` e medido, nao arbitrado.** No dado oficial da
  B3, em 17 de 17 rolagens (out/2023 a ago/2026) o contrato vigente ainda tinha
  18x a 44x o volume do proximo em D-1: a liquidez so migra NO dia do
  vencimento. Com o antigo `min_days=2` o motor trocava em D-1 e passava um
  pregao inteiro no contrato ~25x menos liquido, seis vezes por ano.
* Analise que atravessa a noite em `WIN$N` precisa descontar rolagem: 26 dos 30
  maiores saltos "overnight" caem em mes par, quando o contrato vira. Medido nos
  17 dias de rolagem: gap mediano **7,3x** o normal e ATR de abertura **1,31x**.
  Mas o efeito no PnL da carteira **nao e significativo** (34 trades, p=0,078 em
  teste de permutacao) -- nao vale neutralizar o true range; complexidade sem
  evidencia.
* `src/portfolio/allocator.py` NAO esta no caminho de producao. A alocacao em
  uso vive em `research/win_deep.py` (por dobra) e `research/finalize.py` (para
  congelar a config). O allocator generico so volta a fazer sentido se houver
  mais de um instrumento aprovado.

## Estado operacional atual

* Config congelada: `config/live_config.json` -- WIN$N, M15, **4 pernas**
  (EmaTrend, VolBreak, VWAPRevert, ORB), **1 contrato por perna** (`max_qty`),
  kill switch diario R$450, rede de catastrofe em R$1.400 de prejuizo aberto,
  teto de 4 contratos liquidos, zeragem 17:30.
* **A banca de referencia e R$5.000** (05/08/2026). Isso nao e detalhe de
  sizing, e o que define a carteira: com 1 contrato -- o minimo indivisivel do
  WIN -- as 6 pernas originais somam 22% da banca em risco simultaneo e a serie
  historica ZERA a conta (patrimonio minimo negativo, ruina em dez/2021).
  BarMomentum e GapPlay foram cortadas por orcamento de risco agregado (teto de
  15% da banca, soma do p95 do stop de 1 contrato), nao por performance.
  A config anterior (6 pernas, R$3.000 de risco) esta em
  `config/live_config_6pernas_R3000_backup_2026-08-05.json`.
* **Validacao (05/08/2026).** Monte Carlo re-rodado: M15 base Sharpe OOS 1,028
  com p<0,0001; sob custo brutal 0,326 com p=0,0042 -- significativo nos tres
  cortes. Mas ele valida a BUSCA DE PARAMETROS, nao a alocacao. Para a carteira
  de 4 pernas foi feito walk-forward proprio (selecao de parametro **e** corte
  de banca so no treino, teste com qty=1 discreto, curva partindo de R$5.000)
  com duas janelas de treino inicial. **O ranking de Sharpe INVERTEU entre as
  janelas** (sem corte 0,97/0,60; corte 15% 0,55/0,39; corte 10% 0,43/0,88):
  essa diferenca e ruido, nao sinal. O que e estavel nas duas: **o corte protege
  o piso** -- sem corte o patrimonio chega a R$928 (-81% da banca). Por isso o
  criterio de decisao aqui e o **menor patrimonio da serie**, nunca o Sharpe.
  Nao trocar 15% por 10% porque o 10% ganhou numa rodada: e selecao pos-hoc.
  **Limite conhecido:** a ruina in-sample de dez/2021 cai no treino em qualquer
  janela testada -- o evento que motivou o corte nunca foi testado fora da
  amostra.
* Conta demo Clear **1199739157**, fixada por caminho de terminal + numero da
  conta em `src/core/mt5session.py`. Nenhuma ordem real jamais foi enviada.
* **Tarefas agendadas instaladas** (todas silenciosas, via `pythonw.exe`):

  | Tarefa | Quando | O que faz |
  |---|---|---|
  | `B3_HarvestWINDiario` | diario 08:00 | PricRpt oficial da B3 -> `data/oficial_b3/win_diario.db` |
  | `B3_HarvestL2WIN` | seg-sex 08:55 | book L2 (10 niveis/lado) -> `data/l2_win/` |
  | `B3_MotorAoVivo` | seg-sex 10:05 | motor, **sem `--dry`** (envia ordem em demo) |
  | `B3_RelatorioDiario` | seg-sex 18:30 | relatorio + conferencia de custo |
  | `B3_ManutencaoSemanal` | sabado 09:00 | dado novo, revalida, recongela config |

  Harvester L2 e motor rodam simultaneamente no mesmo terminal -- verificado que
  coexistem sem conflito. O L2 **nao alimenta nada** hoje: e acumulo de dado
  para um eventual modelo futuro.

## Divida tecnica conhecida

* **Controle de versao: resolvido em 05/08/2026.** Repositorio privado no
  GitHub (`b3-daytrade-win`), `.git`/`.gitignore`/`.gitattributes` na raiz.
  `data/`, `research/results/` e `logs/` ficam de fora por serem regeneraveis;
  `state/live_state.json` e o lock ficam de fora por serem estado de processo,
  nao do projeto. Uma segunda auditoria externa (05/08, a noite) encontrou esta
  mesma secao ainda dizendo "nao ha .git" horas depois do repo criado -- prova
  viva de que atualizar o CLAUDE.md apos mexer em algo relevante nao e opcional.
* `state.entries_today` (agregado) conta entradas do dia inclusive de
  execucoes descartadas (ex.: as da conta errada em 05/08). O gate real por
  perna e `legs_done[family]` (persistido desde a correcao do item C1 abaixo),
  nao o agregado -- que serve so para exibicao.
* O motor entra em loop de 60s nos fins de semana em vez de sair. So importa se
  alguem subir manualmente no sabado -- a tarefa so roda em dia util.
* **Harvest diario da B3: resolvido em 05/08/2026.** Ate entao o pipeline
  (`harvest_diario_win.py`) rodava fora do repositorio (`New OpenCode Project`),
  quebrando reproducibilidade -- apontado pela 2a auditoria externa (C5). Agora
  `src/ingest/b3_pricrpt.py` + `scripts/14_harvest_win_diario.py` sao copia
  adaptada, dentro do projeto, escrevendo em `data/oficial_b3/win_diario.db`
  (fora do git, regeneravel, como o resto de `data/`). Os originais
  (`extrator_win.py`, `backfill_win.py`, `win_diario.db`) continuam intocados
  em `New OpenCode Project` -- so a copia de trabalho mudou de lugar, nao o
  material historico protegido em `EXTRACAO_DADOS_B3.md`.

# Sistema de day-trade sistematico na B3

Projeto completo, do dado bruto a execucao automatica: descoberta de universo,
ingestao, motor de backtest, validacao estatistica e motor de execucao em conta
demo da Clear via MetaTrader 5.

> **Conta demo.** `assert_demo()` roda antes de qualquer ordem e derruba o motor
> se a conta conectada nao for de simulacao. Operar dinheiro real exige mudanca
> deliberada de configuracao por quem e dono da conta -- nao acontece sozinho.

## Decisao final

| | |
|---|---|
| **Instrumento** | mini indice (WIN), contrato vigente resolvido pelo vencimento |
| **Prazo** | day trade, barras de 15 minutos, zeragem as 17:30, zero overnight |
| **Abordagem** | 6 regras mecanicas em carteira -- **sem machine learning** |
| **Resultado fora da amostra** | Sharpe **1,03** em 812 pregoes, retorno/drawdown **4,1** |
| **Teste de significancia** | Monte Carlo com a busca inteira dentro: **p < 0,004** |
| **Sobrevive a custo dobrado?** | sim, Sharpe cai para 0,33 e continua significativo |
| **Reprovados no caminho** | 59 acoes, mini dolar, timeframe M5, efeito overnight |

Por que regra mecanica e nao IA: com 812 dias uteis de amostra efetiva, um
modelo com muitos graus de liberdade nao consegue ser distinguido de ruido. O
gargalo deste problema nao e capacidade de modelo, e **friccao** -- e friccao se
ataca escolhendo o instrumento certo, nao aumentando a complexidade. O criterio
foi o retorno ajustado a evidencia, e ele apontou para o lado simples.

A trilha completa de evidencia, com todos os numeros e os testes que
reprovaram cada alternativa, esta em [RESULTADOS.md](RESULTADOS.md).

---

## 1. Como as decisoes foram tomadas

Nenhuma escolha de instrumento, prazo ou setup foi feita por preferencia. Cada
uma saiu de um teste que podia ter reprovado a ideia -- e varias reprovaram.

### O que foi testado

| Dimensao | Alternativas avaliadas |
|---|---|
| Instrumento | 59 acoes/ETFs liquidos, mini indice (WIN), mini dolar (WDO) |
| Prazo | intradia M5, intradia M15, decomposicao intradia vs overnight em D1 |
| Setup | 6 familias independentes: rompimento de range de abertura, rompimento por volatilidade, reversao ao VWAP, momento de barra, gap de abertura, cruzamento de medias |
| Combinacoes | 284 por ativo -> **17.324 configuracoes avaliadas** |

### Como foi medido

* **Custo pessimista por padrao**: entrada e saida a mercado pagando o spread
  inteiro, mais 1 tick extra de slippage no stop, mais emolumentos da B3 por
  lado. Nenhum resultado deste README supoe execucao passiva.
* **Sem olhar o futuro**: sinal fechado em `t`, entrada na abertura de `t+1`.
  Verificado por teste automatico que recalcula as features com o historico
  truncado e compara valor a valor (`tests/test_no_lookahead.py`).
* **Stop tem prioridade**: se stop e alvo caberiam na mesma barra, conta o stop.
* **Alvo exige penetracao estrita**: ordem limite nao preenche so por encostar.
* **Parametro escolhido so no treino**: walk-forward ancorado, 5 a 6 dobras.
  Todo numero reportado aqui e de janela nunca vista pela escolha.

---

## 2. O que os testes disseram

### 2.1 Acoes intradia estao mortas na friccao

Varredura completa das 6 familias sobre 59 acoes liquidas, todas com
walk-forward:

| Familia | Sharpe OOS mediano entre ativos | % de ativos com PnL positivo |
|---|---|---|
| ORB | -1,64 | 3,3% |
| GapPlay | -1,73 | 1,6% |
| VolBreak | -1,77 | 4,9% |
| BarMomentum | -2,66 | 3,3% |
| VWAPRevert | -2,79 | 1,6% |
| EmaTrend | -3,09 | 1,6% |

Isso nao e azar de parametro: e aritmetica de custo. Em uma acao de R$15, um
unico tick de spread ja vale ~7 bps, contra uma amplitude diaria tipica de
~200 bps -- a friccao come **~3,5% da amplitude do dia** so na ida e volta.

### 2.2 O mini indice e uma ordem de grandeza mais barato

No WIN, ida e volta com custo pessimista completo custa R$3,10 por contrato =
15,5 pontos de indice, contra uma amplitude diaria mediana de **1.865 pontos**.
A friccao consome **0,83% da amplitude** -- quatro vezes menos que em acao.

Por isso o WIN foi o unico ativo positivo em varias familias ao mesmo tempo,
com parametros estaveis entre as dobras. Nao e o setup que muda: e o custo.

### 2.3 O mini dolar foi reprovado

WDO deu Sharpe OOS -1,40 em M5 e -1,41 em M15, negativo em todos os anos.
Descartado.

### 2.4 A anatomia do indice: o retorno esta fora do pregao

Decomposicao de 5 anos (BOVA11, que nao tem rolagem de contrato):

| Componente | Acumulado 5 anos | Sharpe |
|---|---|---|
| Intradia (abertura -> fechamento) | **-11,2%** | -0,08 |
| Overnight (fechamento -> abertura) | **+68,1%** | 1,06 |
| Total | +49,3% | 0,53 |

Duas consequencias diretas:

1. **Day trade no indice nao tem vento a favor.** A deriva do pregao e nula ou
   levemente negativa, entao qualquer resultado positivo vem de tempo de
   entrada, nao de beta disfarcado. Isso e bom: o resultado nao e "estar
   comprado com outro nome".
2. **O efeito overnight e real mas nao e negociavel.** No BOVA11 rende 4,4 bps
   por dia contra ~7 bps de custo de ida e volta. No WIN$N ele parece maior,
   mas **55% do ganho aparente e artefato de rolagem** -- 26 dos 30 maiores
   saltos "overnight" caem em mes par, exatamente quando o contrato vira.
   Descontado o artefato, tambem nao paga o custo.

### 2.5 O veredito: Monte Carlo, nao DSR

Aplicando o Deflated Sharpe com 17.324 tentativas, o limiar fica em 3,0-3,4 de
Sharpe anual e **nada passa**. Isso nao e veredito: a formula supoe que a
dispersao dos Sharpes entre tentativas e ruido amostral, e aqui ela e em boa
parte deterministica -- acoes perdem por friccao, nao por azar. Usar essa
dispersao como ruido reprovaria ate uma estrategia genuina.

O teste que vale foi empirico: 240 replicas em que a direcao de cada entrada e
embaralhada, com a **selecao de parametros e a montagem da carteira acontecendo
dentro de cada replica** -- ou seja, a distribuicao nula ja embute o ganho por
garimpo.

| Configuracao | Sharpe observado | nulo: media | nulo: max | p-valor |
|---|---:|---:|---:|---:|
| WIN M5, custo base | 0,822 | -0,368 | 1,113 | **0,017** |
| WIN M15, custo base | 1,028 | -0,504 | 0,957 | **< 0,004** |
| WIN M15, custo brutal | 0,326 | -0,913 | 0,350 | **0,004** |

Em M15 o observado supera o **maximo** das 240 replicas nulas. A media do nulo
perto de -0,5 mostra o tamanho do buraco que a friccao cava: bater zero ja e o
resultado.

### 2.6 M5 e M15 sao a mesma aposta

Correlacao diaria entre as duas pernas: **0,71**. Combinar 50/50 da Sharpe 0,96,
pior que o M15 sozinho (1,02) com o dobro de execucao. Decisao: **so M15**.

### 2.7 Dois bugs reais foram pegos por teste, nao por revisao

1. O filtro de dias curtos descartava o **pregao em andamento** -- o motor
   ficaria sem gerar sinal nenhum ate ~10:40, justamente nos setups de abertura.
2. Com o mercado fechado o terminal devolve **bid/ask zerados**; o motor
   calculava stop e alvo sobre preco zero.

O segundo so apareceu porque `scripts/09_replay_session.py` reexecuta pregoes
passados com o **proprio** `LiveEngine`, relogio e cotacao trocados. 25 sessoes
reexecutadas: 89 entradas, nenhuma terminou com posicao aberta.

---

## 3. Arquitetura

```
src/core/         fuso, caminhos, sessao MT5, instrumentos e modelo de custo
src/ingest/       descoberta de universo e download de barras
src/backtest/     features sem vazamento, motor de execucao, metricas
src/validation/   PSR, Deflated Sharpe, walk-forward ancorado
src/strategies/   as 6 familias de hipotese
src/portfolio/    allocator generico multi-ativo (fora do caminho de producao)
src/execution/    broker (MT5), livro netting, motor ao vivo, configuracao
src/monitor/      relatorio diario e conferencia de custo realizado
research/         varredura, painel, Monte Carlo, congelamento de config
scripts/          pontos de entrada numerados, na ordem de uso
tests/            52 testes: motor, netting, vazamento, config, paridade ao vivo, auditoria
```

### Peca central: a conta e NETTING

A conta da Clear no MT5 funde posicoes do mesmo simbolo (`margin_mode=0`). Seis
estrategias no mesmo contrato viram UMA posicao no broker, entao stop por
estrategia no servidor e impossivel. `src/execution/netting.py` resolve isso com
um livro de pernas virtuais: o motor calcula a posicao alvo, compara com a real
e manda so a diferenca. Uma rede no servidor -- stop de catastrofe sobre a
posicao liquida -- cobre o caso de o motor cair com posicao aberta.

### Peca central: o modelo de custo

`src/core/instruments.py` e o arquivo que decide se o projeto e honesto. Cada
instrumento declara spread, slippage de entrada, slippage de saida, slippage
EXTRA do stop e emolumentos. Existem tres variantes: `base` (pessimista, usada
em tudo), `brutal` (mais 1 tick por lado e taxas 50% maiores, para estresse) e
`optimistic` (so para medir quanto do PnL e friccao -- nunca para decidir).

### Peca central: o motor

`src/backtest/engine.py` implementa 7 regras pessimistas, cada uma com teste
proprio. O motor devolve lista de trades com MAE/MFE, motivo da saida e custo
separado do bruto.

> A alocacao de risco que vale em producao vive em `research/win_deep.py`
> (pesos por dobra) e `research/finalize.py` (congelamento). O
> `src/portfolio/allocator.py` e um allocator generico multi-ativo que so volta
> a fazer sentido se houver mais de um instrumento aprovado -- hoje nao esta no
> caminho de producao.

---

## 4. Como rodar

```bash
python scripts/01_build_universe.py     # descobre e ranqueia o universo na B3
python scripts/02_download_bars.py      # baixa tudo que o terminal oferece
python tests/test_engine.py             # 8 testes do motor
python tests/test_netting.py            # 10 testes do livro de pernas virtuais
python tests/test_no_lookahead.py       # 3 testes de vazamento de futuro
python tests/test_config_roundtrip.py   # 2 testes de fidelidade da config
python tests/test_live_parity.py        # 2 testes de paridade backtest x ao vivo (M15)
python tests/test_auditoria.py          # 27 testes das falhas achadas em auditoria
python scripts/03_run_sweep.py          # varredura ampla (6 familias x 61 ativos)
python scripts/04_win_portfolio.py      # carteira no instrumento vencedor
python scripts/05_montecarlo.py         # distribuicao nula com a busca inteira
python scripts/07_win_anatomy.py        # diagnostico: onde mora o retorno
python scripts/08_combine_timeframes.py # M5 e M15 sao a mesma aposta?
python scripts/06_finalize_config.py    # congela config/live_config.json
python scripts/09_replay_session.py --days 25   # reexecuta pregoes com o motor real
python scripts/10_run_live.py --dry     # motor sem enviar ordem
python scripts/11_daily_report.py       # relatorio + conferencia de custo
python scripts/12_weekly_maintenance.py # dado novo, revalida e recongela
```

Para ligar a operacao diaria automatica (nao e feito sozinho, de proposito):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1
```

---

## 5. O que esta configurado

`config/live_config.json` -- WIN$N em M15, **banca de referencia R$5.000**
(desde 05/08/2026), 1 contrato por perna (`max_qty`), 4 pernas cortadas por
orcamento de risco agregado (teto de 15% da banca, nao por performance):

| Familia | risk_brl | max_qty |
|---|---:|---:|
| ORB | R$ 460 | 1 |
| VolBreak | R$ 350 | 1 |
| VWAPRevert | R$ 300 | 1 |
| EmaTrend | R$ 300 | 1 |

BarMomentum e GapPlay ficaram de fora: com 1 contrato -- o minimo indivisivel
do WIN -- as 6 pernas originais somam risco agregado acima do orcamento e a
serie historica ZERA uma banca de R$5.000 (ver `CLAUDE.md`). A config de 6
pernas com R$3.000 de risco esta preservada em
`config/live_config_6pernas_R3000_backup_2026-08-05.json`.

Limites: kill switch diario R$450 (9% da banca), rede de stop de catastrofe em
R$1.400 de prejuizo aberto (28%), teto de 4 contratos liquidos, zeragem as
17:30. Dimensionamento e fonte unica da verdade em `src/execution/sizing.py`.

**As tarefas agendadas estao instaladas** (`scripts/install_scheduled_tasks.ps1`):
motor ao vivo (seg-sex 10:05), relatorio diario (18:30), manutencao semanal
(sabado 09:00) e o harvester L2 do book (seg-sex 08:55). O motor envia ordem em
conta demo -- ver `CLAUDE.md` para o estado operacional atualizado e a divida
de validacao (o Monte Carlo OOS existente vale para a carteira de 6 pernas, nao
para esta de 4).

---

## 6. Limites conhecidos

* **Significancia nao e expectativa robusta.** O p-valor do Monte Carlo responde
  "existe direcao previsivel". O t-stat da media diaria fora da amostra e ~1,85
  em 812 dias -- positivo, com margem de erro larga. Isso justifica operar em
  demo com tamanho pequeno, nao escalar risco.
* **O resultado depende de a execucao custar ~1 tick por ponta.** No cenario
  brutal o Sharpe cai de 1,03 para 0,33. A conferencia em
  `src/monitor/report.py` compara, a cada pregao, o custo realizado com o
  assumido; se a diferenca media por trade passar de ~R$0,50, recalibrar
  `src/core/instruments.py` antes de qualquer aumento de risco.
* **Teto de historico.** O terminal entrega no maximo 100.000 barras por
  simbolo em M5 -- WIN alcanca jan/2023. M15 e H1 nao tem esse teto e chegam a
  5 anos. Foi por isso que M15 virou a base: 43% mais amostra.
* **Serie continua.** A pesquisa usa `WIN$N`, que emenda contratos. Trades
  intradiarios nunca atravessam a virada, entao o backtest nao e afetado --
  mas qualquer analise que atravesse a noite precisa descontar a rolagem.
* **Livro de ofertas nao foi usado.** Toda a friccao e modelada por parametro,
  nao reconstruida a partir da fila real.
* **Stop de perna vive no motor, nao no servidor** -- consequencia direta da
  conta ser netting. Se o processo morrer com posicao aberta, o que resta e o
  stop de catastrofe sobre a posicao liquida, que e mais largo que os stops
  individuais. Ele nunca foi acionado em fill real.
* **Nunca houve fill real.** Tudo aqui e simulacao ou conta demo.

# Trilha de evidencia

Todo numero aqui vem de um script deste repositorio e pode ser reproduzido.
Nada foi medido na mesma janela em que o parametro foi escolhido.

---

## Etapa 1 -- universo e dados

`scripts/01_build_universe.py` + `scripts/02_download_bars.py`

* 1.209 candidatos com formato de acao/unit/ETF no terminal da Clear
* 60 aprovados no corte de liquidez (volume financeiro mediano >= R$20 mi/dia)
* **6,2 milhoes de barras M5** persistidas em parquet, 133 series no total
* acoes: M5 desde dez/2021 (~4,6 anos) | WIN/WDO: M5 desde jan/2023, M15 desde ago/2021

---

## Etapa 2 -- varredura ampla (`scripts/03_run_sweep.py`)

6 familias x 284 combinacoes x 61 ativos = **17.324 configuracoes**, cada uma
com walk-forward ancorado de 5 dobras e custo pessimista.

Resultado por familia (mediana entre os 61 ativos, fora da amostra):

| Familia | Sharpe mediano | % ativos com PnL > 0 | melhor DSR |
|---|---:|---:|---:|
| ORB | -1,64 | 3,3% | 0,313 |
| GapPlay | -1,73 | 1,6% | 0,038 |
| VolBreak | -1,77 | 4,9% | 0,680 |
| BarMomentum | -2,66 | 3,3% | 0,061 |
| VWAPRevert | -2,79 | 1,6% | 0,325 |
| EmaTrend | -3,09 | 1,6% | 0,009 |

**Nenhuma acao passou.** O unico ativo positivo em varias familias ao mesmo
tempo foi o WIN, com parametros estaveis entre as dobras:

| Ativo x familia | trades OOS | PnL | Sharpe | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|
| WIN VolBreak | 469 | R$ 20.055 | 1,53 | +0,76 | +2,34 | +1,08 |
| WIN VWAPRevert | 300 | R$ 8.015 | 1,18 | +2,31 | +1,58 | +1,62 |
| WIN ORB | 417 | R$ 4.613 | 0,66 | +0,82 | +0,78 | +0,50 |

Tres ideias economicas diferentes, positivas em **todos** os anos fora da
amostra, no mesmo instrumento.

---

## Etapa 3 -- carteira de familias no WIN (`scripts/04_win_portfolio.py`)

A hipotese testada deixa de ser "qual o melhor parametro" e passa a ser
"uma carteira de familias, montada so com dados de treino, entrega resultado
fora da amostra". A composicao tambem e decidida dobra a dobra.

| Ativo | TF | dias OOS | PnL (risco R$900/trade) | Sharpe | max DD | retorno/DD |
|---|---|---:|---:|---:|---:|---:|
| WIN | M5 | 583 | R$ 15.544 | 0,82 | R$ -10.139 | 1,53 |
| WIN | M15 | 812 | R$ 16.151 | **1,03** | R$ -3.912 | **4,13** |
| WDO | M5 | 385 | R$ -14.073 | -1,40 | R$ -16.149 | -0,87 |
| WDO | M15 | 812 | R$ -24.706 | -1,41 | R$ -27.664 | -0,89 |

**WDO reprovado.** WIN aprovado nos dois timeframes, com M15 claramente melhor
em risco: mesmo PnL com um quarto do drawdown.

Teste de estresse com custo `brutal` (+1 tick de slippage por lado, taxas +50%):
WIN M5 cai para Sharpe 0,66 e WIN M15 para 0,33 -- **continuam positivos**.

---

## Etapa 4 -- o DSR classico reprova, e por que ele nao serve aqui

Aplicando o Deflated Sharpe com 17.324 tentativas, o limiar `SR0` fica em
**3,0 a 3,4 de Sharpe anual** e nada passa.

Esse resultado nao deve ser lido como "reprovado". A formula do DSR supoe que a
dispersao dos Sharpes entre tentativas e ruido amostral. Aqui ela e em boa
parte **deterministica**: acoes perdem porque a friccao relativa e alta, nao
porque tiveram azar. Usar essa dispersao como ruido reprovaria ate uma
estrategia genuina.

Por isso o veredito foi transferido para um teste empirico.

---

## Etapa 5 -- Monte Carlo com a busca inteira dentro (`scripts/05_montecarlo.py`)

Hipotese nula: *a direcao das entradas nao carrega informacao*. Cada replica
embaralha o lado de cada entrada preservando horario, tamanho do stop, regra de
saida e custo. A selecao de parametros e a montagem da carteira acontecem
**dentro** de cada replica -- a distribuicao nula ja embute o ganho por garimpo.

240 replicas por configuracao:

| Configuracao | Sharpe observado | nulo: media | nulo: p95 | nulo: max | p-valor |
|---|---:|---:|---:|---:|---:|
| WIN M5, custo base | 0,822 | -0,368 | 0,608 | 1,113 | **0,017** |
| WIN M15, custo base | 1,028 | -0,504 | 0,332 | 0,957 | **< 0,004** |
| WIN M15, custo brutal | 0,326 | -0,913 | -0,095 | 0,350 | **0,004** |

O Sharpe observado em M15 **supera o maximo das 240 replicas nulas**. E a media
do nulo perto de -0,5 mostra o tamanho do buraco que a friccao cava: bater zero
ja e o resultado.

---

## Etapa 6 -- M5 e M15 sao a mesma aposta (`scripts/08_combine_timeframes.py`)

Correlacao diaria entre as duas pernas: **0,71**. Combinar 50/50 da Sharpe 0,96,
pior que o M15 sozinho (1,02) com o dobro de execucao. **Decisao: so M15.**

---

## Etapa 7 -- anatomia do indice (`scripts/07_win_anatomy.py`)

Decomposicao de 5 anos no BOVA11 (ETF, sem rolagem de contrato):

| Componente | Acumulado | Sharpe |
|---|---:|---:|
| Intradia (abertura -> fechamento) | -11,2% | -0,08 |
| Overnight (fechamento -> abertura) | +68,1% | 1,06 |
| Total | +49,3% | 0,53 |

Duas leituras:

1. **O day trade no indice nao tem vento a favor** -- a deriva do pregao e nula
   ou negativa. Qualquer resultado positivo e tempo de entrada, nao beta
   disfarcado.
2. **O efeito overnight e real mas nao paga o pedagio**: 4,4 bps por dia contra
   ~7 bps de custo de ida e volta no ETF. No WIN$N ele parece maior, mas **26
   dos 30 maiores saltos overnight caem em mes par** -- exatamente quando o
   contrato rola. Cerca de 55% do ganho aparente e artefato.

Friccao em perspectiva: no WIN a ida e volta completa custa 15,5 pontos contra
uma amplitude diaria mediana de 1.865 pontos -- **0,83% da amplitude**. Numa
acao de R$15 o mesmo calculo da ~3,5%. E essa razao, e nao o setup, que decide
onde da para operar.

---

## Etapa 8 -- validacao do motor de execucao

52 testes automatizados, todos passando:

| Arquivo | O que trava |
|---|---|
| `tests/test_engine.py` (8) | as 7 regras pessimistas do backtest, uma a uma |
| `tests/test_netting.py` (10) | livro virtual: posicao alvo, lado do preco no stop, prioridade stop>tempo, stop de catastrofe |
| `tests/test_no_lookahead.py` (3) | features recalculadas com historico truncado batem valor a valor |
| `tests/test_config_roundtrip.py` (2) | a config operacional reproduz o sinal do backtest |
| `tests/test_live_parity.py` (2) | a visao do motor ao vivo == a visao do backtest, em M15 (producao) |
| `tests/test_auditoria.py` (27) | falhas achadas em duas auditorias externas de 05/08/2026 |

**Bugs reais foram pegos por teste e por auditoria**, nao por revisao casual de
codigo -- ver `CLAUDE.md`, secao "Armadilhas que ja custaram bug", para a lista
completa (dia corrente descartado, bid/ask zerado fora do pregao, dois motores
concorrentes, schema do log de trades, ciclo em conta netting, stop de
catastrofe sem confirmacao, `max_per_day` nao persistido, sinal de barra do dia
anterior, livro esvaziado antes de confirmar a zeragem).

### Replay do motor de verdade (`scripts/09_replay_session.py`)

25 pregoes reexecutados passo a passo com o proprio `LiveEngine`, relogio
trocado e cotacao vinda de barra M1:

```
25 pregoes | 89 entradas | bruto R$ 4.706 | 40% de dias positivos
nenhum pregao terminou com posicao aberta
```

---

## Configuracao congelada

`config/live_config.json` -- WIN$N, M15, **banca de referencia R$5.000**
(recalibrado em 05/08/2026), 1 contrato por perna, 4 pernas cortadas por
orcamento de risco agregado:

| Familia | risk_brl | max_qty |
|---|---:|---:|
| ORB | R$ 460 | 1 |
| VolBreak | R$ 350 | 1 |
| VWAPRevert | R$ 300 | 1 |
| EmaTrend | R$ 300 | 1 |

BarMomentum e GapPlay saem por orcamento de risco agregado (teto de 15% da
banca, soma do p95 do stop de 1 contrato) -- com 1 contrato, o minimo
indivisivel do WIN, as 6 pernas originais zeram uma banca de R$5.000 na serie
historica. A tabela antiga (6 pernas, risco R$3.000, pesos por Sharpe de
treino) esta preservada em
`config/live_config_6pernas_R3000_backup_2026-08-05.json`.

Limites: kill switch diario R$450 (9% da banca), rede de stop de catastrofe em
R$1.400 de prejuizo aberto (28%), teto de 4 contratos liquidos, zeragem as
17:30. **Divida de validacao**: o Monte Carlo OOS acima (Sharpe 1,03, p<0,004)
foi rodado para a carteira de 6 pernas -- nao vale automaticamente para esta de
4; ver `CLAUDE.md` para o walk-forward proprio desta carteira e suas
limitacoes conhecidas.

---

## O que estes numeros NAO dizem

* **A significancia estatistica nao e a mesma coisa que expectativa robusta.**
  O p-valor do Monte Carlo mede "existe direcao previsivel". O t-stat da media
  diaria fora da amostra e ~1,85 em 812 dias -- positivo, mas com margem de erro
  larga. Isso justifica operar em demo com tamanho pequeno, nao escalar risco.
* **O resultado depende de a execucao custar ~1 tick por ponta.** No cenario
  brutal o Sharpe cai de 1,03 para 0,33. A conferencia diaria em
  `src/monitor/report.py` existe exatamente para detectar isso cedo.
* **O livro de ofertas nao foi usado.** A friccao e modelada por parametro, nao
  reconstruida da fila real.
* **Nunca houve fill real.** Tudo aqui e simulacao ou demo.

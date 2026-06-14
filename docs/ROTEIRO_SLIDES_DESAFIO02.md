# Roteiro de Slides - GlowWise Skincare - 25 Slides

## Slide 1 - Capa

**GlowWise Skincare**

Automacao com Machine Learning para classificar ofertas de skincare como `vale_comprar` ou `nao_vale_comprar`

Rebecca Souza Xavier  
AX Academy - Junho/2026

## Slide 2 - Sumario

- Problema, objetivo do ML e solucao
- Arquitetura dos bots
- Dataset e EDA
- Modelagem e experimentos
- Resultados, curvas e decisao do modelo
- MLOps e demo

## Slide 3 - Secao 01

**Problema, Objetivo do ML e Solucao**

Da comparacao manual para uma classificacao automatizada e auditavel: decidir se uma oferta de skincare vale ou nao vale comprar.

## Slide 4 - Problema

**Comprar skincare exige comparacao repetitiva**

- Muitas lojas, nomes, marcas, tamanhos e links.
- Precos mudam e produtos similares confundem a decisao.
- Uma regra fixa de preco nao captura contexto de loja, categoria e texto do produto.

## Slide 5 - Como o ML recomenda e classifica

**Da classificacao `vale_comprar` para a recomendacao**

Este projeto usa ML para apoiar a recomendacao de ofertas de skincare. Para isso, o modelo responde uma pergunta central:

`Esta oferta de skincare vale comprar?`

| Pergunta | Resposta |
|---|---|
| O que o ML avalia? | Ofertas coletadas nas lojas, representadas por texto do produto, categoria, loja e preco |
| O que o modelo classifica? | Se cada oferta e `vale_comprar` ou `nao_vale_comprar` |
| Como isso vira recomendacao? | As ofertas classificadas como `vale_comprar` sao priorizadas na planilha e no alerta |
| Qual e o objetivo? | Recomendar boas oportunidades e reduzir decisoes baseadas apenas em regra fixa de preco |
| Como ele decide? | Aprende padroes do historico tratado, combinando preco, categoria, loja e sinais do texto |

Mensagem do slide: o ML faz as duas coisas de forma conectada: primeiro classifica cada oferta como `vale_comprar` ou `nao_vale_comprar`; depois essa classificacao orienta a recomendacao final exibida ao usuario.

## Slide 6 - Solucao Proposta

**RPA + ML + alerta final**

Fluxo:

`Coleta -> Limpeza -> Modelo -> Planilha -> Telegram`

- Coleta de ofertas de skincare na Drogasil e Beleza na Web.
- Limpeza de precos, links, categorias e duplicidades.
- Entrada do modelo: `texto_produto`, `categoria_busca`, `loja` e `preco`.
- Saida do modelo: classificacao binaria `vale_comprar` / `nao_vale_comprar`.
- Uso no bot: priorizar alertas de ofertas com maior chance de serem boas oportunidades.
- Resultado visivel na planilha, na API e no alerta.

## Slide 7 - Evolucao do Projeto

**Do RPA deterministico para decisao com ML**

| Antes | Agora |
|---|---|
| Regra fixa baseada em preco | Classificacao com texto, loja, categoria e preco |
| Pouco contexto | Classe, probabilidade e justificativa |
| Saida apenas operacional | Saida analitica e rastreavel |
| Sem rastreabilidade de modelo | DVC, MLflow, curvas e Evidently no fluxo |

Mensagem do slide: o bot continua automatizando, mas agora a decisao e treinada, versionada, avaliada e monitorada.

## Slide 8 - Secao 02

**Arquitetura**

Bots BotCity, API de ML, Google Sheets e Telegram.

## Slide 9 - Arquitetura Geral

**Fluxo completo da automacao**

Inserir diagrama:

`Bot Coleta -> DataPool/coleta.json -> Bot Analise -> FastAPI ML -> Google Sheets -> Bot Alerta -> Telegram`

- BotCity Framework Web: coleta nas lojas.
- Maestro/DataPool: orquestracao e passagem de dados entre bots.
- FastAPI: inferencia do modelo `glowwise-vale-comprar@production`.
- Google Sheets: saida auditavel.
- Telegram: alerta final ao usuario.

## Slide 10 - Responsabilidade dos Bots

**Responsabilidades por componente**

| Componente | Papel |
|---|---|
| `bot_coleta_skincare` | Acessa lojas e extrai produto, preco, loja, termo e link |
| `bot_analise_skincare` | Limpa dados, seleciona ofertas, chama o modelo e escreve na planilha |
| `api` | Expoe `GET /saude` e `POST /predict` |
| `bot_alerta_skincare` | Le resultados e envia alerta no Telegram |

## Slide 11 - Integracao do ML no Bot

**Onde a predicao acontece**

```python
resposta = requests.post(ML_API_URL, json=payload, timeout=10)
```

Fluxo:

1. O bot de analise monta o payload com campos limpos.
2. A API retorna classe, probabilidade e justificativa.
3. O bot grava `recomendacao_ml` e `probabilidade_vale_comprar` na planilha.
4. O bot de alerta usa a planilha como fonte final.

Inserir print do terminal:

```text
[ML] POST /predict ...
[ML] Resposta do modelo classe=...
[ML] Decisao aplicada ...
```

## Slide 12 - Secao 03

**Dataset e EDA**

Como a coleta virou um dataset confiavel para treino.

## Slide 13 - Origem do Dataset

**Da raspagem ao dataset de ML**

Fluxo:

`data/raw/execucoes -> produtos_coletados_historico.csv -> produtos_sanitizados.csv -> produtos_ml.csv`

- `data/raw/execucoes`: coletas individuais preservadas para auditoria.
- `produtos_coletados_historico.csv`: historico bruto consolidado.
- `produtos_sanitizados.csv`: base limpa para analise.
- `produtos_ml.csv`: base final usada no treino, avaliacao e geracao dos graficos.

Dados principais:

- Dataset ML: 113 instancias apos limpeza.
- Conjunto de treino: 84 registros.
- Conjunto de teste: 29 registros.
- Separacao: holdout estratificado com 25% para teste.

## Slide 14 - Dataset: Atributos, Alvo e Balanceamento

**O que entra no modelo e o que ele aprende**

| Item | Descricao |
|---|---|
| Instancias | 113 produtos coletados e tratados |
| Features | `texto_produto`, `categoria_busca`, `loja`, `preco` |
| Classe alvo | `vale_comprar` |
| Classes | `0 = nao_vale_comprar`, `1 = vale_comprar` |
| Classe positiva | `vale_comprar` |
| Balanceamento | 64 positivos e 49 negativos |

Leitura do balanceamento:

- `vale_comprar`: 64 registros, aproximadamente 56,6%.
- `nao_vale_comprar`: 49 registros, aproximadamente 43,4%.
- A base nao esta perfeitamente balanceada, mas tambem nao tem desbalanceamento extremo.

## Slide 15 - EDA: Distribuicoes e Qualidade

**Como os dados coletados se distribuem**

Inserir graficos, se houver espaco:

- `reports/eda/distribuicao_categorias.png`
- `reports/eda/distribuicao_alvo.png`

| Categoria | Registros | Mediana preco |
|---|---:|---:|
| hidratacao | 47 | R$ 91,76 |
| limpeza | 31 | R$ 82,56 |
| protecao_solar | 30 | R$ 99,90 |
| tratamento | 5 | R$ 129,90 |

Lojas:

- Beleza na Web: 79 registros.
- Drogasil: 34 registros.

Qualidade dos dados:

- Precos foram convertidos para numero.
- Produtos sem campos essenciais foram removidos antes do treino.
- Links promocionais/Criteo foram tratados.
- Duplicidades e itens incompativeis com o termo de busca foram revisados.
- Valores faltantes no dataset ML final: 0.

Mensagem do slide: a maior parte da base veio de hidratacao, limpeza e protecao solar; tratamento ficou com poucas amostras.

Fonte atualizada da EDA:

`reports/eda/resumo_eda.md`

## Slide 16 - Rotulo e Data Leakage

**Como a classe alvo foi definida**

Rotulo:

`vale_comprar` foi gerado por heuristica de preco por categoria e sinais promocionais.

Regra conceitual:

- Oferta abaixo da mediana de preco da categoria tende a ser `vale_comprar`.
- Oferta com sinal promocional e preco abaixo de um teto da categoria tambem pode ser `vale_comprar`.

Threshold x modelo treinado:

- O threshold foi usado para criar o rotulo inicial quando ainda nao havia avaliacao manual das ofertas.
- O modelo treinado nao e apenas um `if preco < limite`: ele aprende a combinar preco, categoria, loja e texto do produto.
- Com novas coletas, o baseline de precos pode ser recalculado e o modelo pode ser retreinado com dados mais recentes.
- Limite da versao atual: o rotulo ainda e uma heuristica, entao a recomendacao aprende esse criterio inicial; uma evolucao seria validar/ampliar os rotulos com avaliacao humana ou historico real de compra.

Campos usados como features:

- `texto_produto`
- `categoria_busca`
- `loja`
- `preco`

Campos evitados para nao vazar a resposta:

- recomendacao final
- probabilidade do modelo
- justificativa
- saidas criadas depois da predicao

## Slide 17 - Secao 04

**Modelagem**

Como os modelos foram escolhidos, treinados e avaliados.

## Slide 18 - Pipeline de Modelagem

**Classificacao binaria com pipeline reproduzivel**

- Problema: classificacao binaria.
- Saida: `0 = nao_vale_comprar`; `1 = vale_comprar`.
- Entrada: `texto_produto`, `categoria_busca`, `loja` e `preco`.
- Texto: `TfidfVectorizer`.
- Categoria e loja: `OneHotEncoder`.
- Preco: `StandardScaler`.
- Pipeline: scikit-learn `Pipeline` + `ColumnTransformer`.
- Rastreio: MLflow Tracking registrou modelos testados, parametros, metricas e artefatos.
- Modelo final: promovido no MLflow Registry com alias `@production`.

## Slide 19 - Configuracao, Treino e Validacao

**Tres algoritmos distintos comparados**

Orientacao para o Prism:

Crie um slide sem imagens. Use uma tabela de hiperparametros ocupando a parte central e um bloco pequeno com a configuracao de avaliacao.

Tabela principal:

| Modelo | Configuracao principal |
|---|---|
| Logistic Regression | `max_iter=1000`, `class_weight=balanced`, `random_state=42` |
| Random Forest | `n_estimators=120`, `class_weight=balanced`, `random_state=42` |
| Gradient Boosting | `random_state=42` |

Por que esses modelos:

- Logistic Regression: baseline simples, rapido e interpretavel para classificacao binaria.
- Random Forest: modelo robusto para capturar relacoes nao lineares entre preco, categoria, loja e texto.
- Gradient Boosting: ensemble sequencial para comparar com Random Forest e testar ganho por correcao progressiva dos erros.

Configuracao de avaliacao:

- Dataset: `data/processed/produtos_ml.csv`.
- Split: 75% treino e 25% teste.
- Estratificacao: preserva a proporcao das classes.
- Seed: `random_state=42`.

Observacao para fala: foram usados algoritmos diferentes, nao apenas variacao de hiperparametros.

## Slide 20 - Metricas de Avaliacao

**Como o modelo foi avaliado**

Orientacao para o Prism:

Crie um slide com 2 colunas, sem imagens. Use uma tabela limpa e blocos de texto curtos.

Coluna esquerda - metricas:

| Metrica | Por que importa no GlowWise |
|---|---|
| Accuracy | mede acerto geral |
| Precision | evita alerta ruim de compra |
| Recall | evita perder boa oportunidade |
| F1-score | equilibra precision e recall |
| AUC-ROC | mede separacao entre classes |
| AUC-PR | avalia a classe positiva `vale_comprar` |

Coluna direita - contexto do teste:

- Dataset ML: 113 instancias.
- Periodo da amostra: coletas entre 04/06/2026 e 05/06/2026.
- Execucoes registradas: 16 horarios de coleta.
- Treino: 84 registros.
- Teste: 29 registros.
- Split: 75% treino / 25% teste.
- Estratificacao: manteve a proporcao das classes.
- Baseline: classe majoritaria = 56,6%.

Mensagem do slide:

Accuracy sozinha nao basta, porque o bot precisa evitar alertas ruins e tambem nao perder oportunidades reais.

## Slide 21 - Experimentos no MLflow

**MLflow e curva de loss**

Orientacao para o Prism:

Crie um slide de 2 colunas. A esquerda deve conter um bloco curto sobre MLflow. A direita deve conter a curva de loss de treino e validacao. Nao repetir a tabela de hiperparametros aqui, pois ela ja esta no Slide 19.

Coluna esquerda - MLflow:

- MLflow Tracking registrou 3 runs.
- Runs: `logistic_regression`, `random_forest`, `gradient_boosting`.
- Parametros registrados: algoritmo, dataset, total_registros, features e rotulo.
- Metricas registradas: accuracy, precision, recall e f1.
- Modelo promovido: `glowwise-vale-comprar@production`.
- Artefatos: dataset e modelo serializado.

Coluna direita - curva de loss:

- Eixo X: epocas / iteracoes.
- Eixo Y: log loss.
- Curvas: treino e validacao.
- Modelo usado na curva: Gradient Boosting, por permitir acompanhar a evolucao a cada iteracao.

Inserir imagem:

`reports/modelo/curva_loss_treino_validacao_gradient_boosting.png`

Mensagem do slide:

O MLflow mostra quais modelos foram treinados, com quais parametros e metricas. A curva de loss mostra como o erro evolui ao longo das iteracoes de treino e validacao.

## Slide 22 - Resultado Final e MLOps

**Resultado final e monitoramento**

Orientacao para o Prism:

Crie um slide limpo com poucas informacoes. Priorize a matriz de confusao, porque o problema e de classificacao binaria. Se nao houver espaco, remova ROC/PR deste slide e deixe as curvas apenas como material de apoio.

Layout:

- Topo esquerdo: tabela compacta com as metricas principais.
- Centro/esquerda: matriz de confusao do Random Forest em destaque.
- Direita: bloco curto de MLOps + conclusao.
- Nao usar blocos grandes, cards, bordas pesadas ou paragrafos longos.

Frase de conclusao:

Random Forest foi promovido por ter o melhor F1, AUC-ROC alto e apenas 2 erros no conjunto de teste.

Tabela compacta:

| Modelo | F1 | AUC-ROC | AUC-PR | Erros |
|---|---:|---:|---:|---:|
| Random Forest | 0.938 | 0.966 | 0.975 | 2/29 |
| Logistic Reg. | 0.909 | 0.962 | 0.972 | 3/29 |
| Grad. Boost. | 0.903 | 0.962 | 0.975 | 3/29 |

Imagem principal:

- `reports/modelo/matriz_confusao_random_forest.png`

Imagens opcionais, somente se couber:

- `reports/modelo/roc_modelos.png`
- `reports/modelo/pr_modelos.png`

Leitura da matriz de confusao:

- Problema binario: `nao_vale_comprar` x `vale_comprar`.
- O Random Forest errou 2 de 29 registros de teste.
- Falso positivo: 1 oferta marcada como `vale_comprar` quando nao valia.
- Falso negativo: 1 oferta que valia comprar e nao foi priorizada.

Observacao de design:

Use fonte pequena na tabela e priorize a matriz de confusao. Para economizar espaco, mostre apenas F1, AUC-ROC, AUC-PR e erros. Deixe `Accuracy`, `Precision` e `Recall` para a fala ou para o slide de metricas.

Fala curta:

Random Forest foi escolhido porque teve F1 0.938, AUC-ROC 0.966, AUC-PR 0.975 e apenas 2 erros em 29 exemplos de teste.

## Slide 23 - Rastreabilidade e Servico de ML

**MLOps aplicado ao ciclo do modelo**

Orientacao para o Prism:

Crie um slide visual em grade 2x2, com prints pequenos e uma legenda curta em cada quadrante. Nao usar paragrafos longos.

Quadrantes:

- MLflow: print do Tracking/Registry mostrando as runs e o alias `@production`.
- DVC: print do `dvc.yaml`, `dvc.lock` ou terminal com `dvc repro`.
- Evidently: print do `reports/evidently/relatorio_drift.html` com o resumo de drift.
- FastAPI/Swagger: print de `/docs` mostrando `GET /saude` e `POST /predict`.

Mensagem do slide:

- MLflow rastreia experimentos, metricas e modelo promovido.
- DVC versiona e reproduz dataset, treino e relatorios.
- Evidently monitora drift entre baseline e dados atuais.
- FastAPI expoe o modelo para o bot consumir via `POST /predict`.

Fala curta:

Essas ferramentas fecham a rastreabilidade do projeto: o treino fica registrado no MLflow, o pipeline e reproduzido pelo DVC, o drift e acompanhado pelo Evidently e o modelo chega ao bot pela API documentada no Swagger.

## Slide 24 - Demonstracao ao Vivo

**Bot decidindo com ML**

Este slide deve ser exclusivo para a demonstracao, sem conteudo tecnico denso.

Roteiro da demo:

1. Subir ou mostrar a API FastAPI ativa.
2. Mostrar `GET /saude` respondendo.
3. Executar o bot ou mostrar o trecho final da execucao.
4. Evidenciar o `POST /predict`.
5. Mostrar a saida com `recomendacao_ml` e `probabilidade_vale_comprar`.
6. Mostrar Google Sheets ou Telegram com a decisao final.

Sugestao visual:

- Print do Swagger ou terminal.
- Print do log do bot.
- Print do Telegram ou Google Sheets.

## Slide 25 - Duvidas?

**Obrigada**

Rebecca Souza Xavier  
GlowWise Skincare  
AX Academy - Junho/2026

Opcional no rodape:

`GitHub privado compartilhado com instrutor e mentor`

## Checklist Visual para o Prism

- Slide 9: diagrama da arquitetura.
- Slide 11: print/log do `POST /predict`.
- Slide 14: tabela de dataset com features, alvo, instancias e balanceamento.
- Slide 15: `reports/eda/distribuicao_categorias.png`.
- Slide 15: `reports/eda/distribuicao_alvo.png`.
- Slide 15: `reports/eda/resumo_eda.md`.
- Slide 19: tabela de hiperparametros dos 3 modelos, sem imagem.
- Slide 21: `reports/modelo/curva_loss_treino_validacao_gradient_boosting.png`.
- Slide 21: print pequeno do MLflow UI, se houver espaco.
- Slide 22: `reports/modelo/matriz_confusao_random_forest.png`.
- Slide 22: `reports/modelo/roc_modelos.png`.
- Slide 22: `reports/modelo/pr_modelos.png`.
- Slide 23: print do MLflow Tracking/Registry.
- Slide 23: print do `dvc.yaml`, `dvc.lock` ou terminal com `dvc repro`.
- Slide 23: print do Evidently em `reports/evidently/relatorio_drift.html`.
- Slide 23: print do Swagger `/docs` com `GET /saude` e `POST /predict`.
- Slide 24: print/log do bot com `POST /predict`.
- Slide 24: print do Telegram ou Google Sheets.

## Frase Guia para a Apresentacao

"O GlowWise saiu de uma automacao baseada em regras fixas para um fluxo em que o bot coleta dados, transforma esses dados em features, consulta um modelo versionado no MLflow e toma uma decisao visivel para o usuario. A avaliacao mostra que o Random Forest teve o melhor equilibrio entre precision, recall, F1, AUC-ROC e AUC-PR, e por isso foi promovido para production."

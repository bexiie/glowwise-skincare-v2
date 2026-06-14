# GlowWise Skincare

O GlowWise Skincare é um sistema de automação com Machine Learning para monitorar precos de produtos de skincare, analisar oportunidades e enviar alertas de compra.

O projeto evolui o bot original de RPA para um fluxo com decisão baseada em ML. Em vez de apenas aplicar regras fixas, o bot coleta dados, prepara um dataset, consulta um modelo classificador **vale_comprar** / **nao_vale_comprar** e usa essa predição para enriquecer a análise e o alerta ao usuário.

## Problema

Acompanhar produtos de skincare em lojas online é uma tarefa repetitiva, sujeita a comparações manuais e decisões impulsivas. O GlowWise automatiza esse processo para:

- coletar produtos e preços em lojas online;
- limpar e organizar os dados coletados;
- identificar melhores ofertas por termo de busca;
- recomendar se uma oferta vale comprar com apoio de ML;
- recomendar produtos complementares para compra combinada, priorizando menor total e possivel economia de frete;
- enviar alerta com resultado da análise.

## Arquitetura

O projeto possui três bots no ecossistema BotCity e uma camada local de Machine Learning:

```text
Bot de Coleta -> DataPool -> Bot de Analise -> FastAPI ML -> Google Sheets -> Bot de Alerta -> Telegram
```

### Bot de Coleta

Pasta: `bot_coleta_skincare/`

Responsável por:

- acessar Drogasil e Beleza na Web com BotCity Framework Web;
- coletar produto, preço, loja, termo de busca e link;
- limpar links de redirecionadores, como Criteo;
- salvar *coleta.json*;
- enviar registros para o DataPool do BotCity Maestro.

### Bot de Análise

Pasta: `bot_analise_skincare/`

Responsável por:

- consumir os registros do DataPool;
- selecionar a melhor oferta por termo de busca;
- montar recomendacoes "compre junto" com produtos diferentes que combinam entre si;
- chamar a API local de ML em *POST /predict*;
- registrar *recomendacao_ml*, *probabilidade_vale_comprar* e *justificativa_ml*;
- escrever as abas *coleta_bruta*, *melhores_precos* e *compre_junto* no Google Sheets;
- publicar *analise_resumo.json* e *compre_junto_resumo.json* como artifacts.

### Bot de Alerta

Pasta: `bot_alerta_skincare/`

Responsável por:

- ler a aba *melhores_precos*;
- ler a aba *compre_junto*;
- montar uma mensagem com preço, link e decisão do modelo;
- incluir recomendacoes de compra combinada na mensagem;
- enviar alerta via Telegram;
- publicar relatório TXT como artifact.

### API de Machine Learning

Pasta: `api/`

Endpoints:

- *GET /saude*: verifica se a API está ativa;
- *POST /predict*: recebe dados de um produto e retorna a recomendação.

Modelo carregado:

```text
models:/glowwise-vale-comprar@production
```

## Machine Learning

O problema foi modelado como uma recomendação baseada em classificação binária:

```text
0 -> nao_vale_comprar
1 -> vale_comprar
```

Features usadas:

- *texto_produto*: produto, marca, termo de busca e texto original;
- *categoria_busca*;
- *loja*;
- *preco*.

Pipeline:

- *TfidfVectorizer* para texto;
- *OneHotEncoder* para variáveis categóricas;
- *StandardScaler* para preço;
- *Pipeline + ColumnTransformer* do scikit-learn.

Modelos comparados no MLflow:

- Logistic Regression;
- Random Forest;
- Gradient Boosting.

O melhor modelo é promovido no MLflow Registry com alias @production.

## DVC e Evidently

O projeto usa DVC para versionar datasets e artefatos principais da pipeline:

- data/raw/produtos_coletados_historico.csv;
- data/processed/produtos_sanitizados.csv;
- data/processed/produtos_ml.csv;
- mlflow.db;
- mlruns/;
- reports/evidently/relatorio_drift.html.

O relatório de drift é gerado com Evidently AI em:

```text
reports/evidently/relatorio_drift.html
```

## Organização dos Dados

O projeto separa os dados em camadas para manter rastreabilidade e evitar misturar coleta bruta com dados prontos para ML:

```text
data/raw/execucoes/
```

Guarda arquivos de coletas individuais. Essa pasta serve para auditoria e comparação entre execuções.

```text
data/raw/produtos_coletados_historico.csv
```

Histórico bruto consolidado. Reúne os registros coletados pelo bot antes da sanitização final.

```text
data/processed/produtos_sanitizados.csv
```

Dataset limpo para análise exploratória e transformações de ML.

```text
data/processed/produtos_ml.csv
```

Dataset usado no treino, já com features auxiliares e rótulo *vale_comprar*.

Na execução do modelo, o arquivo mais importante é o **produtos_ml.csv**. As demais camadas existem para rastrear como o dado chegou até ele.

## Notebooks e Scripts

Os notebooks não são usados para executar o bot em produção. Eles funcionam como documentação técnica da análise:

- notebooks/01_eda.ipynb: documenta a exploração dos dados, problemas de qualidade e decisão de limpeza.
- notebooks/02_modelagem.ipynb: documenta a modelagem, comparação dos algoritmos e escolha do modelo.

Os scripts são a execução oficial e reproduzível:

- `ml/gerar_dataset.py`;
- `ml/treinar_modelo.py`;
- `ml/gerar_relatorio_modelo.py`;
- `ml/gerar_relatorio_drift.py`;
- `api/main.py`;
- bots em `bot_*_skincare/`.

## Estrutura

```text
GlowWise Skincare/
|-- api/
|   `-- main.py
|-- bot_alerta_skincare/
|   |-- bot.py
|   |-- alerta.py
|   `-- requirements.txt
|-- bot_analise_skincare/
|   |-- bot.py
|   |-- analise.py
|   |-- utils.py
|   `-- requirements.txt
|-- bot_coleta_skincare/
|   |-- bot.py
|   |-- coleta.py
|   |-- utils.py
|   `-- requirements.txt
|-- data/
|   |-- raw/
|   `-- processed/
|-- dist_botcity/
|   |-- coleta.zip
|   |-- analise.zip
|   `-- alerta.zip
|-- ml/
|   |-- gerar_dataset.py
|   |-- treinar_modelo.py
|   |-- gerar_relatorio_modelo.py
|   `-- gerar_relatorio_drift.py
|-- notebooks/
|   |-- 01_eda.ipynb
|   `-- 02_modelagem.ipynb
|-- reports/
|   `-- evidently/
|-- dvc.yaml
|-- dvc.lock
|-- LICENSE
|-- REPOSITORIO.md
`-- README.md
```

## Instalação Local

Crie e ative o ambiente virtual:

```powershell
python -m venv .ml-glowwise
.\.ml-glowwise\Scripts\activate
```

Instale as dependencias:

```powershell
pip install -r requirements.txt
```

## Execução da Pipeline de Dados e ML

Gerar datasets:

```powershell
.\.ml-glowwise\Scripts\python.exe ml\gerar_dataset.py --from-execucoes
```

Treinar modelos e registrar no MLflow:

```powershell
.\.ml-glowwise\Scripts\python.exe ml\treinar_modelo.py
```

Gerar relatório Evidently:

```powershell
.\.ml-glowwise\Scripts\python.exe ml\gerar_relatorio_drift.py
```

Gerar graficos de avaliacao do modelo:

```powershell
.\.ml-glowwise\Scripts\python.exe ml\gerar_relatorio_modelo.py
```

Reproduzir tudo com DVC:

```powershell
.\.ml-glowwise\Scripts\python.exe -m dvc repro
```

Verificar status DVC:

```powershell
.\.ml-glowwise\Scripts\python.exe -m dvc status
```

## Subir a API Local

Antes de executar o bot de analise, suba a API:

```powershell
.\.ml-glowwise\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Rotas:

```text
GET  http://127.0.0.1:8000/saude
POST http://127.0.0.1:8000/predict
DOCS http://127.0.0.1:8000/docs
```

## Execução no BotCity

Ordem de execucao:

```text
1. coleta
2. analise
3. alerta
```

O bot de análise depende da API ML em execução. Se o Runner estiver na mesma máquina, use:

```text
http://127.0.0.1:8000/predict
```

Se estiver em outra máquina, use o IP do servidor da API.

## Credentials Vault

Credenciais sensíveis não devem ser versionadas.

## Limitacoes

- O modelo inicial usa uma base pequena, portanto os resultados devem ser interpretados como primeira versão funcional do pipeline.
- O bot não realiza compra automaticamente.
- O projeto não coleta dados pessoais.
- A API ML precisa estar disponível antes da execução do bot de análise.

> **Propriedade Intelectual:** Este projeto e todo o seu código-fonte, automações e artefatos são de propriedade da LG Electronics do Brasil Ltda., desenvolvido no âmbito do projeto AX Academy --- Digital Transformation (Convênio N.º 005/2025 --- INOVA / IFAM). Consulte o arquivo LICENSE para mais detalhes.

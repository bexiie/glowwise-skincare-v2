"""
analise.py - Bot de Análise (GlowWise Skincare)

Responsabilidades:
- Ler dados coletados do DataPool ou do arquivo coleta.json
- Organizar os dados da coleta em estruturas tabulares
- Identificar o melhor preço por termo de busca
- Salvar resultados no Google Sheets
- Gerar um resumo JSON como artifact
- Reportar o status final da task no Runner/Maestro
"""

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from botcity.maestro import BotMaestroSDK, AutomationTaskFinishStatus, ErrorType
from botcity.plugins.googlesheets.plugin import BotGoogleSheetsPlugin

from utils import agora_str, normalizar_preco, limpar_texto


# =========================
# CONFIGURAÇÕES GERAIS
# =========================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")

DATAPOOL_LABEL = "rebecca-skincare-monitoramento"
VAULT_LABEL_GOOGLE = "rebecca-google"

ABA_COLETA_BRUTA = "coleta_bruta"
ABA_MELHORES_PRECOS = "melhores_precos"
ABA_COMPRE_JUNTO = "compre_junto"
ML_API_URL = os.getenv("ML_API_URL", "http://localhost:8000/predict")

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "analise"
OUTPUT_JSON = ARTIFACTS_DIR / "analise_resumo.json"
OUTPUT_COMBOS_JSON = ARTIFACTS_DIR / "compre_junto_resumo.json"
TIMEOUT_DATAPOOL_SEGUNDOS = int(os.getenv("TIMEOUT_DATAPOOL_SEGUNDOS", "60"))
TIMEOUT_SHEETS_SEGUNDOS = int(os.getenv("TIMEOUT_SHEETS_SEGUNDOS", "60"))
TIMEOUT_ARTIFACT_SEGUNDOS = int(os.getenv("TIMEOUT_ARTIFACT_SEGUNDOS", "30"))

# Caminhos candidatos para fallback local da coleta.
COLETA_JSON_CANDIDATOS = [
    PROJECT_DIR / "bot_coleta_skincare" / "artifacts" / "coleta" / "coleta.json",
    BASE_DIR / "artifacts" / "coleta" / "coleta.json",
    PROJECT_DIR / "artifacts" / "coleta" / "coleta.json",
    BASE_DIR / "coleta.json",
]

# Ordem preferencial esperada na saída final.
TERMOS_ESPERADOS = [
    "hidratante facial vitamina c",
    "hidratante facial niacinamida",
    "gel de limpeza facial vitamina c",
    "gel de limpeza facial niacinamida",
]

BotMaestroSDK.RAISE_NOT_CONNECTED = True


def executar_com_timeout(funcao, timeout_segundos, descricao, *args, **kwargs):
    """Executa uma operacao externa com limite para nao prender o Runner."""
    resultado = queue.Queue(maxsize=1)

    def alvo():
        try:
            resultado.put(("ok", funcao(*args, **kwargs)))
        except Exception as e:
            resultado.put(("erro", e))

    thread = threading.Thread(target=alvo, daemon=True)
    thread.start()
    thread.join(timeout_segundos)

    if thread.is_alive():
        raise TimeoutError(f"Timeout em {descricao} apos {timeout_segundos}s.")

    status, valor = resultado.get()
    if status == "erro":
        raise valor
    return valor


# =========================
# UTILITÁRIOS
# =========================
def garantir_pasta(caminho: Path) -> None:
    """Cria a pasta informada caso ela ainda não exista."""
    os.makedirs(caminho, exist_ok=True)


def padronizar_termo(termo: Any) -> str:
    """Normaliza o termo de busca para facilitar agrupamentos e comparações."""
    return limpar_texto(termo).lower()


def identificar_categoria_busca(termo: Any) -> str:
    """Infere a categoria a partir do termo de busca."""
    termo = padronizar_termo(termo)

    if "limpeza" in termo:
        return "limpeza"
    if "hidratante" in termo:
        return "hidratacao"
    if "protetor solar" in termo:
        return "protecao_solar"
    if "serum" in termo or "vitamina c" in termo or "niacinamida" in termo:
        return "tratamento"

    return "outros"


def produto_compativel_com_termo(produto: Any, termo: Any) -> bool:
    """Evita escolher produto barato que nao corresponde ao termo buscado."""
    produto = limpar_texto(produto).lower()
    termo = padronizar_termo(termo)

    if not produto or not termo:
        return False

    regras = [
        ("vitamina c", ["vitamina c"]),
        ("niacinamida", ["niacinamida", "niacinamide"]),
        ("gel de limpeza", ["gel de limpeza", "limpeza facial", "cleanser", "cleansing"]),
        ("hidratante", ["hidratante", "hidratacao", "hidratação", "creme", "aqua-gel"]),
    ]

    for trecho_termo, opcoes_produto in regras:
        if trecho_termo in termo and not any(opcao in produto for opcao in opcoes_produto):
            return False

    return True


# =========================
# MAESTRO
# =========================
def iniciar_maestro():
    """Realiza login no Maestro e recupera a execução atual da task."""
    maestro = BotMaestroSDK.from_sys_args()

    server = os.getenv("MAESTRO_SERVER")
    login = os.getenv("MAESTRO_LOGIN")
    key = os.getenv("MAESTRO_KEY")

    if server and login and key:
        maestro.login(server=server, login=login, key=key)

    execution = maestro.get_execution()
    if execution is None or execution.task_id is None:
        raise RuntimeError("Não foi possível obter a execução atual do Maestro.")

    print(f"[INFO] Execução Runner detectada. Task ID: {execution.task_id}")
    return maestro, execution


def finalizar_task(
    maestro,
    execution,
    status,
    mensagem: str,
    total_items: int = 0,
    processed_items: int = 0,
    failed_items: int = 0,
) -> None:
    """Finaliza a task no Maestro ajustando os contadores para cada cenário."""
    if execution is None or execution.task_id is None:
        raise RuntimeError("Execution inválida ao tentar finalizar a task.")

    total_items = int(total_items or 0)
    processed_items = int(processed_items or 0)
    failed_items = int(failed_items or 0)

    if status.name == "SUCCESS":
        failed_items = 0
        total_items = max(total_items, processed_items)
        processed_items = total_items
    elif status.name == "FAILED":
        total_items = max(total_items, processed_items + failed_items, 1)
        if processed_items + failed_items != total_items:
            processed_items = max(total_items - failed_items, 0)
    else:
        total_items = max(total_items, processed_items + failed_items, 1)
        if processed_items + failed_items != total_items:
            failed_items = max(total_items - processed_items, 0)

    maestro.finish_task(
        task_id=execution.task_id,
        status=status,
        message=mensagem,
        total_items=total_items,
        processed_items=processed_items,
        failed_items=failed_items,
    )
    print(f"[TASK] {status.name}: {mensagem}")


# =========================
# VAULT / CREDENCIAIS
# =========================
def obter_credencial(maestro, label: str, key: str) -> str:
    """Lê uma credencial do Vault e falha explicitamente se ela não existir."""
    valor = maestro.get_credential(label=label, key=key)
    if not valor:
        raise ValueError(f"Credencial não encontrada no Vault. label='{label}', key='{key}'")
    return valor


# =========================
# GOOGLE SHEETS
# =========================
def iniciar_google_sheets(maestro) -> BotGoogleSheetsPlugin:
    """Inicializa o plugin do Google Sheets já apontando para a aba de coleta."""
    google_credentials_path = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "credentials_path")
    google_spreadsheet_id = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "spreadsheet_id")
    credentials_path = Path(google_credentials_path)

    if not credentials_path.is_absolute():
        candidatos = [
            BASE_DIR / credentials_path,
            Path.cwd() / credentials_path,
        ]
        credentials_path = next((caminho for caminho in candidatos if caminho.exists()), credentials_path)

    if not credentials_path.exists():
        raise FileNotFoundError(
            "Arquivo de credenciais do Google Sheets não encontrado. "
            f"Valor recebido no Vault rebecca-google/credentials_path: {google_credentials_path!r}. "
            "No BotCity Runner, use um caminho absoluto para o arquivo no computador do Runner "
            "ou inclua o arquivo no pacote do bot."
        )

    return BotGoogleSheetsPlugin(
        client_secret_path=str(credentials_path),
        spreadsheet_id=google_spreadsheet_id,
        active_sheet=ABA_COLETA_BRUTA,
    )


# =========================
# LEITURA DE ENTRADA
# =========================
def entry_para_dict(entry: Any) -> dict:
    """Converte o item retornado pelo DataPool para um dicionário comum."""
    if isinstance(entry, dict):
        return entry

    try:
        return dict(entry)
    except Exception:
        pass

    if hasattr(entry, "values") and isinstance(entry.values, dict):
        return entry.values

    if hasattr(entry, "__dict__"):
        data = entry.__dict__
        if "values" in data and isinstance(data["values"], dict):
            return data["values"]

    payload = {}
    for campo in [
        "run_id",
        "produto",
        "loja",
        "preco",
        "link",
        "disponivel",
        "data_coleta",
        "termo_busca",
        "categoria_busca",
    ]:
        try:
            payload[campo] = entry[campo]
        except Exception:
            continue

    return payload


def carregar_registros_do_datapool(maestro, execution):
    """Consome todos os itens disponíveis no DataPool e reporta status item a item."""
    print("[INFO] Lendo dados do DataPool...")
    datapool = maestro.get_datapool(label=DATAPOOL_LABEL)

    registros = []
    itens_consumidos = 0
    itens_falhos = 0

    while datapool.has_next():
        item = datapool.next(task_id=execution.task_id)
        if item is None:
            break

        try:
            dados = entry_para_dict(item)
            if not dados:
                raise ValueError("Item do DataPool sem payload valido.")

            registros.append(
                {
                    "produto": limpar_texto(dados.get("produto")),
                    "loja": limpar_texto(dados.get("loja")),
                    "preco": normalizar_preco(dados.get("preco")),
                    "link": limpar_texto(dados.get("link")),
                    "disponivel": str(dados.get("disponivel")).lower() == "true",
                    "data_coleta": limpar_texto(dados.get("data_coleta")),
                    "termo_busca": padronizar_termo(dados.get("termo_busca")),
                    "run_id": limpar_texto(dados.get("run_id")),
                    "categoria_busca": limpar_texto(dados.get("categoria_busca")),  
                }
            )

            item.report_done(finish_message="Item processado com sucesso.")
            itens_consumidos += 1

        except Exception as e:
            print(f"[AVISO] Falha ao processar item do DataPool: {e}")
            try:
                item.report_error(
                    error_type=ErrorType.SYSTEM,
                    finish_message=f"Falha ao processar item: {e}",
                )
            except Exception as erro_report:
                print(f"[AVISO] Não foi possível reportar erro do item no DataPool: {erro_report}")
            itens_falhos += 1

    print(f"[INFO] Itens consumidos do DataPool: {itens_consumidos}")
    if itens_falhos > 0:
        print(f"[AVISO] Itens com falha no DataPool: {itens_falhos}")

    return registros


def localizar_coleta_json():
    """Procura o coleta.json nos caminhos conhecidos do projeto."""
    for caminho in COLETA_JSON_CANDIDATOS:
        if caminho.exists():
            print(f"[INFO] Fallback JSON localizado em: {caminho}")
            return caminho
    return None


def carregar_registros_do_json():
    """Lê o fallback local coleta.json e normaliza os campos encontrados."""
    coleta_json = localizar_coleta_json()
    if not coleta_json:
        print("[AVISO] JSON de coleta não encontrado nos caminhos esperados.")
        return []

    try:
        with open(coleta_json, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except Exception as e:
        print(f"[AVISO] Falha ao ler JSON de coleta: {e}")
        return []

    registros = []
    for item in dados if isinstance(dados, list) else []:
        if not isinstance(item, dict):
            continue

        registros.append(
            {
                "produto": limpar_texto(item.get("produto")),
                "loja": limpar_texto(item.get("loja")),
                "preco": normalizar_preco(item.get("preco")),
                "link": limpar_texto(item.get("link")),
                "disponivel": bool(item.get("disponivel", True)),
                "data_coleta": limpar_texto(item.get("data_coleta")),
                "termo_busca": padronizar_termo(item.get("termo_busca")),
                "categoria_busca": identificar_categoria_busca(item.get("termo_busca")),
            }
        )

    return registros


def carregar_registros(maestro, execution):
    """Tenta carregar os registros pelo DataPool; se falhar, usa o JSON local."""
    try:
        registros = executar_com_timeout(
            carregar_registros_do_datapool,
            TIMEOUT_DATAPOOL_SEGUNDOS,
            "leitura do DataPool",
            maestro,
            execution,
        )
        if registros:
            print(f"[OK] Registros carregados do DataPool: {len(registros)}")
            return registros, "datapool"

        print("[AVISO] DataPool vazio. Tentando fallback pelo coleta.json...")
    except Exception as e:
        print(f"[AVISO] Falha ao ler DataPool: {e}")
        print("[INFO] Tentando fallback pelo coleta.json...")

    registros = carregar_registros_do_json()
    if registros:
        print(f"[OK] Registros carregados do JSON: {len(registros)}")
        return registros, "json"

    return [], "nenhum"


# =========================
# TRANSFORMAÇÃO DOS DADOS
# =========================
def montar_dataframe_coleta(registros) -> pd.DataFrame:
    """Cria o DataFrame da coleta mantendo uma ordem estável de colunas."""
    colunas = [
        "run_id",
        "produto",
        "loja",
        "preco",
        "link",
        "disponivel",
        "data_coleta",
        "termo_busca",
        "categoria_busca",
    ]

    if not registros:
        return pd.DataFrame(columns=colunas)

    df = pd.DataFrame(registros)
    for coluna in colunas:
        if coluna not in df.columns:
            df[coluna] = ""

    return df[colunas]


def montar_dataframe_melhores_por_termo(df: pd.DataFrame) -> pd.DataFrame:
    """Gera um DataFrame com a melhor oferta por termo de busca."""
    colunas_saida = [
        "termo_busca",
        "categoria_busca",
        "produto",
        "loja",
        "preco",
        "link",
        "quantidade_ofertas_no_termo",
        "data_analise",
        "recomendacao_ml",
        "probabilidade_vale_comprar",
        "justificativa_ml",
    ]

    if df.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_validos = df[
        df["termo_busca"].notna()
        & (df["termo_busca"] != "")
        & df["preco"].notna()
        & df["disponivel"].astype(bool)
    ].copy()

    if df_validos.empty:
        return pd.DataFrame(columns=colunas_saida)

    for coluna in ["produto", "loja", "link"]:
        df_validos[coluna] = df_validos[coluna].fillna("").astype(str).str.strip()

    total_antes_filtro = len(df_validos)
    df_validos = df_validos[
        df_validos.apply(
            lambda row: produto_compativel_com_termo(row["produto"], row["termo_busca"]),
            axis=1,
        )
    ].copy()

    removidos = total_antes_filtro - len(df_validos)
    if removidos > 0:
        print(f"[INFO] Produtos removidos por incompatibilidade com o termo: {removidos}")

    if df_validos.empty:
        return pd.DataFrame(columns=colunas_saida)

    termos_presentes = sorted(set(df_validos["termo_busca"].tolist()))
    ordem_preferencial = [termo for termo in TERMOS_ESPERADOS if termo in termos_presentes]
    termos_restantes = [termo for termo in termos_presentes if termo not in ordem_preferencial]

    resultados = []
    for termo in ordem_preferencial + termos_restantes:
        grupo = df_validos[df_validos["termo_busca"] == termo].copy()
        if grupo.empty:
            continue

        grupo = grupo.sort_values(
            by=["preco", "produto", "loja"],
            ascending=[True, True, True],
        ).reset_index(drop=True)

        melhor = grupo.iloc[0]
        resultados.append(
            {
                "termo_busca": termo,
                "categoria_busca": identificar_categoria_busca(termo),
                "produto": melhor["produto"],
                "loja": melhor["loja"],
                "preco": float(melhor["preco"]),
                "link": melhor["link"],
                "quantidade_ofertas_no_termo": int(len(grupo)),
                "data_analise": agora_str(),
                "recomendacao_ml": "",
                "probabilidade_vale_comprar": None,
                "justificativa_ml": "",
            }
        )

    return pd.DataFrame(resultados, columns=colunas_saida)


def montar_dataframe_compre_junto(df_coleta: pd.DataFrame, df_melhores: pd.DataFrame) -> pd.DataFrame:
    """Gera recomendacoes de produtos complementares para comprar junto."""
    colunas_saida = [
        "termo_principal",
        "produto_principal",
        "categoria_principal",
        "loja_principal",
        "preco_principal",
        "link_principal",
        "termo_recomendado",
        "produto_recomendado",
        "categoria_recomendada",
        "loja_recomendada",
        "preco_recomendado",
        "link_recomendado",
        "preco_total_combo",
        "economia_frete",
        "criterio_recomendacao",
        "data_analise",
    ]

    if df_coleta.empty or df_melhores.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_validos = df_coleta[
        df_coleta["termo_busca"].notna()
        & (df_coleta["termo_busca"] != "")
        & df_coleta["preco"].notna()
        & df_coleta["disponivel"].astype(bool)
    ].copy()

    if df_validos.empty:
        return pd.DataFrame(columns=colunas_saida)

    for coluna in ["produto", "loja", "link", "termo_busca"]:
        df_validos[coluna] = df_validos[coluna].fillna("").astype(str).str.strip()

    df_validos = df_validos[
        df_validos.apply(
            lambda row: produto_compativel_com_termo(row["produto"], row["termo_busca"]),
            axis=1,
        )
    ].copy()

    if df_validos.empty:
        return pd.DataFrame(columns=colunas_saida)

    df_validos["categoria_busca"] = df_validos["termo_busca"].apply(identificar_categoria_busca)

    resultados = []
    for _, principal in df_melhores.iterrows():
        termo_principal = limpar_texto(principal.get("termo_busca"))
        produto_principal = limpar_texto(principal.get("produto"))
        loja_principal = limpar_texto(principal.get("loja"))
        categoria_principal = limpar_texto(principal.get("categoria_busca")) or identificar_categoria_busca(termo_principal)

        candidatos = df_validos[
            (df_validos["termo_busca"] != termo_principal)
            & (df_validos["produto"].str.lower() != produto_principal.lower())
        ].copy()

        candidatos_complementares = candidatos[candidatos["categoria_busca"] != categoria_principal].copy()
        if not candidatos_complementares.empty:
            candidatos = candidatos_complementares

        if candidatos.empty:
            continue

        candidatos["mesma_loja"] = (
            candidatos["loja"].str.lower() == loja_principal.lower()
        ) & bool(loja_principal)
        candidatos = candidatos.sort_values(
            by=["mesma_loja", "preco", "produto", "loja"],
            ascending=[False, True, True, True],
        ).reset_index(drop=True)

        recomendado = candidatos.iloc[0]
        mesma_loja = bool(recomendado["mesma_loja"])
        preco_principal = float(principal.get("preco") or 0)
        preco_recomendado = float(recomendado["preco"])
        criterio = (
            "Produto complementar na mesma loja, priorizando menor preço total e possivel economia de frete."
            if mesma_loja
            else "Produto complementar mais barato encontrado em outra loja."
        )

        resultados.append(
            {
                "termo_principal": termo_principal,
                "produto_principal": produto_principal,
                "categoria_principal": categoria_principal,
                "loja_principal": loja_principal,
                "preco_principal": preco_principal,
                "link_principal": limpar_texto(principal.get("link")),
                "termo_recomendado": recomendado["termo_busca"],
                "produto_recomendado": recomendado["produto"],
                "categoria_recomendada": recomendado["categoria_busca"],
                "loja_recomendada": recomendado["loja"],
                "preco_recomendado": preco_recomendado,
                "link_recomendado": recomendado["link"],
                "preco_total_combo": round(preco_principal + preco_recomendado, 2),
                "economia_frete": "sim" if mesma_loja else "nao",
                "criterio_recomendacao": criterio,
                "data_analise": agora_str(),
            }
        )

    return pd.DataFrame(resultados, columns=colunas_saida)


def montar_payload_ml(item: pd.Series) -> dict[str, Any]:
    """Monta o payload esperado pela FastAPI de predicao."""
    return {
        "produto": limpar_texto(item.get("produto")),
        "marca": "",
        "termo_busca": limpar_texto(item.get("termo_busca")),
        "categoria_busca": limpar_texto(item.get("categoria_busca")),
        "loja": limpar_texto(item.get("loja")),
        "preco": float(item.get("preco")),
        "produto_original": limpar_texto(item.get("produto")),
    }


def chamar_api_ml(item: pd.Series) -> dict[str, Any]:
    """Consulta a API local e devolve a decisao do modelo."""
    payload = montar_payload_ml(item)
    print(
        "[ML] POST /predict "
        f"termo={payload.get('termo_busca')!r} "
        f"loja={payload.get('loja')!r} "
        f"preco={payload.get('preco')}"
    )
    resposta = requests.post(ML_API_URL, json=payload, timeout=10)
    resposta.raise_for_status()
    predicao = resposta.json()
    print(
        "[ML] Resposta do modelo "
        f"classe={predicao.get('classe')} "
        f"probabilidade_vale_comprar={predicao.get('probabilidade_vale_comprar')}"
    )
    return predicao


def aplicar_predicoes_ml(df_melhores: pd.DataFrame) -> pd.DataFrame:
    """Adiciona recomendacao do modelo aos melhores precos."""
    if df_melhores.empty:
        return df_melhores

    df = df_melhores.copy()

    for indice, item in df.iterrows():
        try:
            predicao = chamar_api_ml(item)
            df.at[indice, "recomendacao_ml"] = predicao.get("classe", "")
            df.at[indice, "probabilidade_vale_comprar"] = predicao.get("probabilidade_vale_comprar")
            df.at[indice, "justificativa_ml"] = predicao.get("justificativa", "")
            print(
                "[ML] Decisao aplicada "
                f"{item.get('termo_busca')}: "
                f"{predicao.get('classe')} "
                f"prob={predicao.get('probabilidade_vale_comprar')}"
            )
        except Exception as e:
            df.at[indice, "recomendacao_ml"] = "api_indisponivel"
            df.at[indice, "probabilidade_vale_comprar"] = None
            df.at[indice, "justificativa_ml"] = f"Falha ao consultar API ML: {e}"
            print(f"[AVISO] Falha ao consultar API ML para {item.get('termo_busca')}: {e}")

    return df


def imprimir_resumo_predicoes(df_melhores: pd.DataFrame) -> None:
    """Mostra um resumo simples das predicoes geradas pela API ML."""
    if df_melhores.empty or "recomendacao_ml" not in df_melhores.columns:
        print("[RESUMO ML] Nenhuma predicao disponivel para resumir.")
        return

    contagem = df_melhores["recomendacao_ml"].fillna("sem_predicao").value_counts()
    total = int(contagem.sum())

    print("[RESUMO ML] Predicoes por classe:")
    for classe, quantidade in contagem.items():
        print(f"[RESUMO ML] - {classe}: {int(quantidade)}")

    classe_majoritaria = str(contagem.idxmax()) if total else "sem_predicao"
    print(f"[RESUMO ML] Total de itens com decisao: {total}")
    print(f"[RESUMO ML] Predicao majoritaria: {classe_majoritaria}")


# =========================
# ESCRITA NO GOOGLE SHEETS
# =========================
def escrever_aba(gs, nome_aba: str, df: pd.DataFrame, colunas: list[str]) -> None:
    """Limpa a aba informada e reescreve o conteúdo inteiro."""
    try:
        gs.clear(sheet=nome_aba)
    except Exception:
        try:
            gs.create_sheet(nome_aba)
        except Exception:
            pass

    gs.add_rows([colunas], sheet=nome_aba)

    if not df.empty:
        linhas = df[colunas].fillna("").values.tolist()
        gs.add_rows(linhas, sheet=nome_aba)


def escrever_aba_coleta_bruta(gs, df: pd.DataFrame) -> None:
    """Escreve a coleta bruta na planilha."""
    colunas = [
        "run_id",
        "produto",
        "loja",
        "preco",
        "link",
        "disponivel",
        "data_coleta",
        "termo_busca",
        "categoria_busca",
    ]
    escrever_aba(gs, ABA_COLETA_BRUTA, df, colunas)


def escrever_aba_melhores_precos(gs, df: pd.DataFrame) -> None:
    """Escreve o resumo de melhores preços na planilha."""
    colunas = [
        "termo_busca",
        "categoria_busca",
        "produto",
        "loja",
        "preco",
        "link",
        "quantidade_ofertas_no_termo",
        "data_analise",
        "recomendacao_ml",
        "probabilidade_vale_comprar",
        "justificativa_ml",
    ]
    escrever_aba(gs, ABA_MELHORES_PRECOS, df, colunas)


def escrever_aba_compre_junto(gs, df: pd.DataFrame) -> None:
    """Escreve recomendacoes de compra combinada na planilha."""
    colunas = [
        "termo_principal",
        "produto_principal",
        "categoria_principal",
        "loja_principal",
        "preco_principal",
        "link_principal",
        "termo_recomendado",
        "produto_recomendado",
        "categoria_recomendada",
        "loja_recomendada",
        "preco_recomendado",
        "link_recomendado",
        "preco_total_combo",
        "economia_frete",
        "criterio_recomendacao",
        "data_analise",
    ]
    escrever_aba(gs, ABA_COMPRE_JUNTO, df, colunas)


# =========================
# ARTIFACTS / SAÍDAS
# =========================
def salvar_resumo_json(df_melhores: pd.DataFrame) -> None:
    """Salva em disco o resumo da análise que será enviado como artifact."""
    garantir_pasta(ARTIFACTS_DIR)
    dados = df_melhores.fillna("").to_dict(orient="records")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def salvar_combos_json(df_compre_junto: pd.DataFrame) -> None:
    """Salva em disco as recomendacoes de compra combinada."""
    garantir_pasta(ARTIFACTS_DIR)
    dados = df_compre_junto.fillna("").to_dict(orient="records")

    with open(OUTPUT_COMBOS_JSON, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def contar_termos_disponiveis(df_coleta: pd.DataFrame) -> int:
    """Conta quantos termos distintos foram encontrados na coleta."""
    if df_coleta.empty or "termo_busca" not in df_coleta.columns:
        return 0

    return len(
        {
            limpar_texto(valor).lower()
            for valor in df_coleta["termo_busca"].tolist()
            if limpar_texto(valor)
        }
    )


# =========================
# FLUXO PRINCIPAL
# =========================
def main() -> None:
    maestro, execution = iniciar_maestro()
    total_items = 0
    processed_items = 0
    failed_items = 0

    try:
        print("Lendo dados da coleta...")
        registros, origem = carregar_registros(maestro, execution)
        total_items = len(registros)

        print(f"[INFO] Origem dos registros: {origem}")
        print(f"[INFO] Registros lidos: {total_items}")

        df_coleta = montar_dataframe_coleta(registros)
        df_melhores = montar_dataframe_melhores_por_termo(df_coleta)
        df_melhores = aplicar_predicoes_ml(df_melhores)
        imprimir_resumo_predicoes(df_melhores)
        df_compre_junto = montar_dataframe_compre_junto(df_coleta, df_melhores)

        print(f"[INFO] Linhas na coleta_bruta: {len(df_coleta)}")
        print(f"[INFO] Linhas em melhores_precos: {len(df_melhores)}")
        print(f"[INFO] Linhas em compre_junto: {len(df_compre_junto)}")

        gs = executar_com_timeout(
            iniciar_google_sheets,
            TIMEOUT_SHEETS_SEGUNDOS,
            "inicializacao do Google Sheets",
            maestro,
        )

        print("Escrevendo aba coleta_bruta...")
        executar_com_timeout(
            escrever_aba_coleta_bruta,
            TIMEOUT_SHEETS_SEGUNDOS,
            "escrita da aba coleta_bruta",
            gs,
            df_coleta,
        )

        print("Escrevendo aba melhores_precos...")
        executar_com_timeout(
            escrever_aba_melhores_precos,
            TIMEOUT_SHEETS_SEGUNDOS,
            "escrita da aba melhores_precos",
            gs,
            df_melhores,
        )

        print("Escrevendo aba compre_junto...")
        executar_com_timeout(
            escrever_aba_compre_junto,
            TIMEOUT_SHEETS_SEGUNDOS,
            "escrita da aba compre_junto",
            gs,
            df_compre_junto,
        )

        salvar_resumo_json(df_melhores)
        print(f"[OK] Resumo salvo em: {OUTPUT_JSON}")
        salvar_combos_json(df_compre_junto)
        print(f"[OK] Resumo de compre junto salvo em: {OUTPUT_COMBOS_JSON}")

        total_items = contar_termos_disponiveis(df_coleta)
        processed_items = len(df_melhores)
        failed_items = max(total_items - processed_items, 0)

        try:
            executar_com_timeout(
                maestro.post_artifact,
                TIMEOUT_ARTIFACT_SEGUNDOS,
                "envio do artifact analise_resumo.json",
                task_id=execution.task_id,
                artifact_name="analise_resumo.json",
                filepath=str(OUTPUT_JSON),
            )
            print("[OK] Artifact da analise enviado ao Maestro.")
            executar_com_timeout(
                maestro.post_artifact,
                TIMEOUT_ARTIFACT_SEGUNDOS,
                "envio do artifact compre_junto_resumo.json",
                task_id=execution.task_id,
                artifact_name="compre_junto_resumo.json",
                filepath=str(OUTPUT_COMBOS_JSON),
            )
            print("[OK] Artifact de compre junto enviado ao Maestro.")
        except Exception as e:
            print(f"[AVISO] Erro ao enviar artifact da analise: {e}")

        try:
            print(f"Planilha: {gs.get_spreadsheet_link()}")
        except Exception:
            pass

        print("[RESUMO EXECUCAO] Analise concluida.")
        print(f"[RESUMO EXECUCAO] Itens lidos da coleta: {len(registros)}")
        print(f"[RESUMO EXECUCAO] Termos processados: {processed_items}/{total_items}")
        print(f"[RESUMO EXECUCAO] Recomendacoes compre_junto: {len(df_compre_junto)}")
        imprimir_resumo_predicoes(df_melhores)

        if total_items == 0:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.FAILED,
                "Analise finalizada sem dados de entrada da coleta.",
                total_items=1,
                processed_items=0,
                failed_items=1,
            )
        elif processed_items == 0:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.FAILED,
                "Analise executada, mas nenhum melhor valor por termo foi gerado.",
                total_items=total_items,
                processed_items=0,
                failed_items=total_items,
            )
        elif failed_items > 0:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.PARTIALLY_COMPLETED,
                f"Analise finalizada parcialmente. {processed_items} termo(s) com melhor valor definido.",
                total_items=total_items,
                processed_items=processed_items,
                failed_items=failed_items,
            )
        else:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.SUCCESS,
                f"Analise finalizada com sucesso. {processed_items} termo(s) com melhor valor definido.",
                total_items=total_items,
                processed_items=processed_items,
                failed_items=0,
            )

        print("[CONCLUIDO] Bot de analise finalizado.")

    except Exception as e:
        print(f"[ERRO] Falha fatal no bot de analise: {e}")
        finalizar_task(
            maestro,
            execution,
            AutomationTaskFinishStatus.FAILED,
            f"Erro fatal na analise: {e}",
            total_items=max(total_items, 1),
            processed_items=max(total_items - max(failed_items, 1), 0),
            failed_items=max(failed_items, 1),
        )
        raise


if __name__ == "__main__":
    main()

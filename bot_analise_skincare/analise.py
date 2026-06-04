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
from pathlib import Path
from typing import Any

import pandas as pd
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

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "analise"
OUTPUT_JSON = ARTIFACTS_DIR / "analise_resumo.json"

# Caminhos candidatos para fallback local da coleta.
COLETA_JSON_CANDIDATOS = [
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


# =========================
# UTILITÁRIOS
# =========================
def garantir_pasta(caminho: Path) -> None:
    """Cria a pasta informada caso ela ainda não exista."""
    os.makedirs(caminho, exist_ok=True)


def padronizar_termo(termo: Any) -> str:
    """Normaliza o termo de busca para facilitar agrupamentos e comparações."""
    return limpar_texto(termo).lower()


# =========================
# MAESTRO
# =========================
def iniciar_maestro():
    """Realiza login no Maestro e recupera a execução atual da task."""
    maestro = BotMaestroSDK.from_sys_args()

    maestro.login(
        server=os.getenv("MAESTRO_SERVER"),
        login=os.getenv("MAESTRO_LOGIN"),
        key=os.getenv("MAESTRO_KEY"),
    )

    execution = maestro.get_execution()
    if execution is None or execution.task_id is None:
        raise RuntimeError("Nao foi possivel obter a execucao atual do Maestro.")

    print(f"[INFO] Execucao Runner detectada. Task ID: {execution.task_id}")
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
        raise RuntimeError("Execution invalida ao tentar finalizar a task.")

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
        raise ValueError(f"Credencial nao encontrada no Vault. label='{label}', key='{key}'")
    return valor


# =========================
# GOOGLE SHEETS
# =========================
def iniciar_google_sheets(maestro) -> BotGoogleSheetsPlugin:
    """Inicializa o plugin do Google Sheets já apontando para a aba de coleta."""
    google_credentials_path = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "credentials_path")
    google_spreadsheet_id = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "spreadsheet_id")

    return BotGoogleSheetsPlugin(
        client_secret_path=google_credentials_path,
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
                print(f"[AVISO] Nao foi possivel reportar erro do item no DataPool: {erro_report}")
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
        print("[AVISO] JSON de coleta nao encontrado nos caminhos esperados.")
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
            }
        )

    return registros


def carregar_registros(maestro, execution):
    """Tenta carregar os registros pelo DataPool; se falhar, usa o JSON local."""
    try:
        registros = carregar_registros_do_datapool(maestro, execution)
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
    return df[colunas]


def montar_dataframe_melhores_por_termo(df: pd.DataFrame) -> pd.DataFrame:
    """Gera um DataFrame com a melhor oferta por termo de busca."""
    colunas_saida = [
        "termo_busca",
        "produto",
        "loja",
        "preco",
        "link",
        "quantidade_ofertas_no_termo",
        "data_analise",
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
                "produto": melhor["produto"],
                "loja": melhor["loja"],
                "preco": float(melhor["preco"]),
                "link": melhor["link"],
                "quantidade_ofertas_no_termo": int(len(grupo)),
                "data_analise": agora_str(),
            }
        )

    return pd.DataFrame(resultados, columns=colunas_saida)


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
        "produto",
        "loja",
        "preco",
        "link",
        "quantidade_ofertas_no_termo",
        "data_analise",
    ]
    escrever_aba(gs, ABA_MELHORES_PRECOS, df, colunas)


# =========================
# ARTIFACTS / SAÍDAS
# =========================
def salvar_resumo_json(df_melhores: pd.DataFrame) -> None:
    """Salva em disco o resumo da análise que será enviado como artifact."""
    garantir_pasta(ARTIFACTS_DIR)
    dados = df_melhores.fillna("").to_dict(orient="records")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
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

        print(f"[INFO] Linhas na coleta_bruta: {len(df_coleta)}")
        print(f"[INFO] Linhas em melhores_precos: {len(df_melhores)}")

        gs = iniciar_google_sheets(maestro)

        print("Escrevendo aba coleta_bruta...")
        escrever_aba_coleta_bruta(gs, df_coleta)

        print("Escrevendo aba melhores_precos...")
        escrever_aba_melhores_precos(gs, df_melhores)

        salvar_resumo_json(df_melhores)
        print(f"[OK] Resumo salvo em: {OUTPUT_JSON}")

        total_items = contar_termos_disponiveis(df_coleta)
        processed_items = len(df_melhores)
        failed_items = max(total_items - processed_items, 0)

        try:
            maestro.post_artifact(
                task_id=execution.task_id,
                artifact_name="analise_resumo.json",
                filepath=str(OUTPUT_JSON),
            )
            print("[OK] Artifact da analise enviado ao Maestro.")
        except Exception as e:
            print(f"[AVISO] Erro ao enviar artifact da analise: {e}")

        try:
            print(f"Planilha: {gs.get_spreadsheet_link()}")
        except Exception:
            pass

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

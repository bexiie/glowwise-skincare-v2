"""
alerta.py - Bot de Alerta (GlowWise Skincare)

Responsabilidades:
- Ler os melhores preços na aba "melhores_precos" do Google Sheets
- Montar uma mensagem resumida com os produtos encontrados
- Enviar o alerta para o Telegram usando plugin oficial do BotCity
- Salvar um relatório TXT como artifact
- Reportar o status final da task no Runner/Maestro
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from botcity.maestro import BotMaestroSDK, AutomationTaskFinishStatus
from botcity.plugins.googlesheets import BotGoogleSheetsPlugin
from botcity.plugins.telegram import BotTelegramPlugin


# =========================
# CONFIGURAÇÕES GERAIS
# =========================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

ABA_MELHORES_PRECOS = "melhores_precos"
VAULT_LABEL_GOOGLE = "rebecca-google"
VAULT_LABEL_TELEGRAM = "rebecca-telegram"

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "alerta"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

BotMaestroSDK.RAISE_NOT_CONNECTED = True


# =========================
# MAESTRO
# =========================
def iniciar_maestro():
    """Realiza login no Maestro e recupera a execução atual."""
    maestro = BotMaestroSDK.from_sys_args()

    server = os.getenv("MAESTRO_SERVER")
    login = os.getenv("MAESTRO_LOGIN")
    key = os.getenv("MAESTRO_KEY")

    if server and login and key:
        maestro.login(server=server, login=login, key=key)

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
    """Finaliza a task no Maestro com contadores consistentes."""
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
    """Lê uma credencial do Vault e falha se ela não estiver configurada."""
    valor = maestro.get_credential(label=label, key=key)
    if valor is None:
        raise ValueError(f"Credencial nao encontrada no Vault. label='{label}', key='{key}'")

    valor = str(valor).strip()
    if not valor:
        raise ValueError(f"Credencial vazia no Vault. label='{label}', key='{key}'")

    return valor


# =========================
# GOOGLE SHEETS
# =========================
def iniciar_google_sheets(maestro) -> BotGoogleSheetsPlugin:
    """Inicializa acesso à planilha que contém a aba de melhores preços."""
    credentials_path = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "credentials_path")
    spreadsheet_id = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "spreadsheet_id")

    return BotGoogleSheetsPlugin(
        client_secret_path=credentials_path,
        spreadsheet_id=spreadsheet_id,
        active_sheet=ABA_MELHORES_PRECOS,
    )


def ler_melhores_precos(gs) -> list[dict[str, Any]]:
    """Lê a aba de melhores preços e converte as linhas em dicionários."""
    print("[INFO] Lendo aba melhores_precos...")
    linhas = gs.as_list(sheet=ABA_MELHORES_PRECOS)

    if not linhas:
        print("[AVISO] Nenhuma linha retornada da aba melhores_precos.")
        return []

    if len(linhas) == 1:
        print("[AVISO] A aba melhores_precos possui apenas cabecalho.")
        return []

    cabecalho = linhas[0]
    dados = linhas[1:]

    registros = []
    for linha in dados:
        linha_ajustada = list(linha) + [""] * (len(cabecalho) - len(linha))
        registros.append(dict(zip(cabecalho, linha_ajustada[: len(cabecalho)])))

    print(f"[INFO] Registros carregados da planilha: {len(registros)}")
    return registros


# =========================
# MENSAGEM
# =========================
def _obter_campo(item: dict[str, Any], *nomes: str) -> str:
    """Busca um campo por nomes alternativos e devolve string limpa."""
    for nome in nomes:
        if nome in item and item[nome] not in (None, ""):
            return str(item[nome]).strip()
    return ""


def montar_mensagem(itens: list[dict[str, Any]]) -> str:
    """Monta a mensagem que será enviada ao Telegram."""
    if not itens:
        return "Nenhuma oferta encontrada na aba melhores_precos."

    linhas = ["GlowWise Skincare - Melhores Precos", ""]

    for i, item in enumerate(itens, start=1):
        produto = _obter_campo(item, "produto", "nome", "nome_produto", "titulo")
        loja = _obter_campo(item, "loja", "site")
        preco = _obter_campo(item, "preco", "preco_atual", "menor_preco")
        url = _obter_campo(item, "url", "link", "href")
        recomendacao_ml = _obter_campo(item, "recomendacao_ml", "classe_ml")
        probabilidade_ml = _obter_campo(item, "probabilidade_vale_comprar", "probabilidade_ml")

        if not produto:
            produto = "Produto sem nome"

        bloco = [
            f"{i}. {produto}",
            f"Loja: {loja or 'Nao informado'}",
            f"Preco: {preco or 'Nao informado'}",
        ]

        if recomendacao_ml:
            bloco.append(f"ML: {recomendacao_ml}")

        if probabilidade_ml:
            bloco.append(f"Probabilidade vale comprar: {probabilidade_ml}")

        if url:
            bloco.append(f"Link: {url}")

        linhas.append("\n".join(bloco))
        linhas.append("")

    mensagem = "\n".join(linhas).strip()

    return mensagem


# =========================
# TELEGRAM
# =========================
def dividir_mensagem(mensagem: str, limite: int = 3500) -> list[str]:
    """Divide a mensagem em blocos menores para envio seguro."""
    if len(mensagem) <= limite:
        return [mensagem]

    partes = []
    atual = []
    tamanho_atual = 0

    for linha in mensagem.splitlines(keepends=True):
        if tamanho_atual + len(linha) > limite and atual:
            partes.append("".join(atual).strip())
            atual = [linha]
            tamanho_atual = len(linha)
        else:
            atual.append(linha)
            tamanho_atual += len(linha)

    if atual:
        partes.append("".join(atual).strip())

    return [parte for parte in partes if parte.strip()]


def iniciar_telegram(maestro) -> tuple[BotTelegramPlugin, str]:
    """
    Inicializa o plugin oficial do Telegram.

    Espera no Vault:
    - token: token do bot
    - group: nome do grupo/chat OU chat_id numerico
    """
    token = obter_credencial(maestro, VAULT_LABEL_TELEGRAM, "token")
    group = obter_credencial(maestro, VAULT_LABEL_TELEGRAM, "group")

    telegram = BotTelegramPlugin(token=token)
    return telegram, group


def _eh_chat_id(valor: str) -> bool:
    """
    Retorna True quando o valor parece ser um chat_id numerico do Telegram.
    Exemplos validos:
    -1001234567890
    123456789
    """
    valor = str(valor).strip()
    if not valor:
        return False

    if valor.startswith("-"):
        return valor[1:].isdigit()

    return valor.isdigit()


def _enviar_parte_telegram(telegram: BotTelegramPlugin, destino: str, texto: str):
    """
    Envia uma parte da mensagem usando o plugin oficial do BotCity.

    - Se o destino for numerico, envia diretamente pelo bot interno do plugin
      usando chat_id.
    - Caso contrario, usa o fluxo padrao do plugin com group=...
    """
    destino = str(destino).strip()

    if _eh_chat_id(destino):
        print(f"[INFO] Enviando Telegram por chat_id direto: {destino}")
        return telegram.bot.send_message(chat_id=destino, text=texto)

    print(f"[INFO] Enviando Telegram por nome de grupo: {destino!r}")
    return telegram.send_message(text=texto, group=destino)


def enviar_telegram(maestro, mensagem: str) -> None:
    """Envia a mensagem para o Telegram usando o plugin oficial do BotCity."""
    telegram, group = iniciar_telegram(maestro)

    print(f"[INFO] Destino Telegram configurado no Vault: {group!r}")

    partes = dividir_mensagem(mensagem)
    if not partes:
        raise RuntimeError("Nao ha conteudo para enviar ao Telegram.")

    for i, parte in enumerate(partes, start=1):
        print(f"[INFO] Enviando parte {i}/{len(partes)} para o Telegram...")
        resposta = _enviar_parte_telegram(telegram, group, parte)

        if not resposta:
            raise RuntimeError("Falha ao enviar mensagem no Telegram: resposta vazia do plugin.")

    print("[OK] Mensagem enviada para o Telegram com sucesso.")


# =========================
# ARTIFACT
# =========================
def salvar_relatorio_alerta(mensagem: str, quantidade_itens: int) -> Path:
    """Gera um relatório TXT com o conteúdo enviado pelo bot de alerta."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo = ARTIFACTS_DIR / f"alerta_{timestamp}.txt"

    conteudo = [
        "RELATORIO DO BOT DE ALERTA",
        f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        f"Quantidade de itens enviados: {quantidade_itens}",
        "",
        "MENSAGEM ENVIADA:",
        "",
        mensagem,
    ]

    arquivo.write_text("\n".join(conteudo), encoding="utf-8")
    print(f"[OK] Relatorio salvo em: {arquivo}")
    return arquivo


# =========================
# FLUXO PRINCIPAL
# =========================
def main() -> None:
    maestro = None
    execution = None
    total_items = 0
    processed_items = 0
    failed_items = 0

    try:
        maestro, execution = iniciar_maestro()

        gs = iniciar_google_sheets(maestro)
        itens = ler_melhores_precos(gs)
        total_items = len(itens)

        mensagem = montar_mensagem(itens)
        enviar_telegram(maestro, mensagem)

        relatorio = salvar_relatorio_alerta(mensagem, total_items)
        maestro.post_artifact(
            task_id=execution.task_id,
            artifact_name=relatorio.name,
            filepath=str(relatorio),
        )

        processed_items = total_items

        finalizar_task(
            maestro,
            execution,
            AutomationTaskFinishStatus.SUCCESS,
            f"Alerta enviado com sucesso. Itens enviados: {processed_items}",
            total_items=total_items,
            processed_items=processed_items,
            failed_items=0,
        )

    except Exception as e:
        print(f"[ERRO] Falha fatal no bot de alerta: {e}")

        if maestro and execution:
            failed_items = max(failed_items, 1)
            processed_items = max(total_items - failed_items, 0)

            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.FAILED,
                f"Erro fatal no alerta: {e}",
                total_items=total_items if total_items > 0 else 1,
                processed_items=processed_items,
                failed_items=failed_items if total_items > 0 else 1,
            )

        raise


if __name__ == "__main__":
    main()

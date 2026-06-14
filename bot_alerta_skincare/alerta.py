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
import queue
import threading
from html import escape
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
PROJECT_DIR = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")

ABA_MELHORES_PRECOS = "melhores_precos"
ABA_COMPRE_JUNTO = "compre_junto"
VAULT_LABEL_GOOGLE = "rebecca-google"
VAULT_LABEL_TELEGRAM = "rebecca-telegram"

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "alerta"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
TIMEOUT_SHEETS_SEGUNDOS = int(os.getenv("TIMEOUT_SHEETS_SEGUNDOS", "45"))
TIMEOUT_TELEGRAM_SEGUNDOS = int(os.getenv("TIMEOUT_TELEGRAM_SEGUNDOS", "30"))
TIMEOUT_ARTIFACT_SEGUNDOS = int(os.getenv("TIMEOUT_ARTIFACT_SEGUNDOS", "30"))

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
    """Finaliza a task no Maestro com contadores consistentes."""
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
    """Lê uma credencial do Vault e falha se ela não estiver configurada."""
    valor = maestro.get_credential(label=label, key=key)
    if valor is None:
        raise ValueError(f"Credencial não encontrada no Vault. label='{label}', key='{key}'")

    valor = str(valor).strip()
    if not valor:
        raise ValueError(f"Credencial vazia no Vault. label='{label}', key='{key}'")

    return valor


# =========================
# GOOGLE SHEETS
# =========================
def iniciar_google_sheets(maestro) -> BotGoogleSheetsPlugin:
    """Inicializa acesso à planilha que contém a aba de melhores preços."""
    google_credentials_path = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "credentials_path")
    spreadsheet_id = obter_credencial(maestro, VAULT_LABEL_GOOGLE, "spreadsheet_id")
    credentials_path = Path(google_credentials_path)

    candidatos = []
    if credentials_path.is_absolute():
        candidatos.append(credentials_path)
        candidatos.append(BASE_DIR / credentials_path.name)
        candidatos.append(PROJECT_DIR / credentials_path.name)
    else:
        candidatos.extend(
            [
                BASE_DIR / credentials_path,
                PROJECT_DIR / credentials_path,
                Path.cwd() / credentials_path,
            ]
        )

    credentials_path = next((caminho for caminho in candidatos if caminho.exists()), credentials_path)

    if not credentials_path.exists():
        raise FileNotFoundError(
            "Arquivo de credenciais do Google Sheets nao encontrado. "
            f"Valor recebido no Vault rebecca-google/credentials_path: {google_credentials_path!r}. "
            "Use um caminho absoluto valido no Runner ou inclua o client_secret.json no pacote do bot."
        )

    return BotGoogleSheetsPlugin(
        client_secret_path=str(credentials_path),
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


def ler_compre_junto(gs) -> list[dict[str, Any]]:
    """Le a aba de recomendacoes de compra combinada."""
    print("[INFO] Lendo aba compre_junto...")

    try:
        linhas = gs.as_list(sheet=ABA_COMPRE_JUNTO)
    except Exception as e:
        print(f"[AVISO] Não foi possível ler a aba compre_junto: {e}")
        return []

    if not linhas:
        print("[AVISO] Nenhuma linha retornada da aba compre_junto.")
        return []

    if len(linhas) == 1:
        print("[AVISO] A aba compre_junto possui apenas cabeçalho.")
        return []

    cabecalho = linhas[0]
    dados = linhas[1:]

    registros = []
    for linha in dados:
        linha_ajustada = list(linha) + [""] * (len(cabecalho) - len(linha))
        registros.append(dict(zip(cabecalho, linha_ajustada[: len(cabecalho)])))

    print(f"[INFO] Registros carregados da aba compre_junto: {len(registros)}")
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


def _formatar_moeda(valor: str) -> str:
    """Formata valores numericos como moeda brasileira."""
    valor = str(valor or "").strip()
    if not valor:
        return "Nao informado"

    try:
        texto = valor.replace("R$", "").strip()
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        numero = float(texto)
    except ValueError:
        return valor

    return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_probabilidade(valor: str) -> str:
    """Formata probabilidade do modelo como percentual."""
    valor = str(valor or "").strip()
    if not valor:
        return ""

    try:
        numero = float(valor.replace(",", "."))
    except ValueError:
        return valor

    if numero <= 1:
        numero *= 100

    return f"{numero:.1f}%".replace(".", ",")


def _formatar_ml(classe: str) -> str:
    """Deixa a classe do modelo mais legivel para o alerta."""
    classe = str(classe or "").strip().lower()
    if classe == "vale_comprar":
        return "Vale comprar"
    if classe == "nao_vale_comprar":
        return "Nao vale comprar"
    if classe == "api_indisponivel":
        return "API indisponivel"
    return classe.replace("_", " ").title() if classe else ""


def _texto_curto(valor: str, limite: int = 92) -> str:
    """Encurta nomes longos sem quebrar o layout da mensagem."""
    valor = " ".join(str(valor or "").split())
    if len(valor) <= limite:
        return valor
    return valor[: limite - 3].rstrip() + "..."


def _html(valor: str) -> str:
    """Escapa texto dinamico para envio com parse_mode HTML."""
    return escape(str(valor or ""), quote=False)


def montar_mensagem(itens: list[dict[str, Any]], combos: list[dict[str, Any]] | None = None) -> str:
    """Monta a mensagem que será enviada ao Telegram."""
    combos = combos or []

    if not itens and not combos:
        return "Nenhuma oferta encontrada na aba melhores_precos."

    linhas = ["GlowWise Skincare - Melhores Preços", ""]

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
            f"Loja: {loja or 'Não informado'}",
            f"Preco: {preco or 'Não informado'}",
        ]

        if recomendacao_ml:
            bloco.append(f"ML: {recomendacao_ml}")

        if probabilidade_ml:
            bloco.append(f"Probabilidade vale comprar: {probabilidade_ml}")

        if url:
            bloco.append(f"Link: {url}")

        linhas.append("\n".join(bloco))
        linhas.append("")

    if combos:
        linhas.append("Recomendações: compre junto!")
        linhas.append("")

        for i, item in enumerate(combos, start=1):
            produto_principal = _obter_campo(item, "produto_principal")
            loja_principal = _obter_campo(item, "loja_principal")
            preco_principal = _obter_campo(item, "preco_principal")
            produto_recomendado = _obter_campo(item, "produto_recomendado")
            loja_recomendada = _obter_campo(item, "loja_recomendada")
            preco_recomendado = _obter_campo(item, "preco_recomendado")
            preco_total = _obter_campo(item, "preco_total_combo")
            economia_frete = _obter_campo(item, "economia_frete")
            criterio = _obter_campo(item, "criterio_recomendacao")
            link_principal = _obter_campo(item, "link_principal")
            link_recomendado = _obter_campo(item, "link_recomendado")

            bloco = [
                f"{i}. Comprar junto",
                f"Principal: {produto_principal or 'Produto sem nome'}",
                f"Loja principal: {loja_principal or 'Não informado'}",
                f"Preco principal: {preco_principal or 'Não informado'}",
                f"Recomendado: {produto_recomendado or 'Produto sem nome'}",
                f"Loja recomendado: {loja_recomendada or 'Não informado'}",
                f"Preco recomendado: {preco_recomendado or 'Não informado'}",
                f"Total do combo: {preco_total or 'Não informado'}",
                f"Economia de frete: {economia_frete or 'Não informado'}",
            ]

            if criterio:
                bloco.append(f"Critério: {criterio}")

            if link_principal:
                bloco.append(f"Link principal: {link_principal}")

            if link_recomendado:
                bloco.append(f"Link recomendado: {link_recomendado}")

            linhas.append("\n".join(bloco))
            linhas.append("")

    mensagem = "\n".join(linhas).strip()

    return mensagem


def montar_mensagem(itens: list[dict[str, Any]], combos: list[dict[str, Any]] | None = None) -> str:
    """Monta uma mensagem visualmente mais organizada para texto puro no Telegram."""
    combos = combos or []

    if not itens and not combos:
        return "<b>GLOWWISE SKINCARE</b>\n\nNenhuma oferta encontrada na aba melhores_precos."

    separador = "-" * 34
    linhas = [
        "<b>GLOWWISE SKINCARE</b>",
        "<b>Resumo de oportunidades</b>",
        separador,
        "",
    ]

    if itens:
        linhas.extend(["MELHORES PREÇOS", separador])

        for i, item in enumerate(itens, start=1):
            produto = _texto_curto(
                _obter_campo(item, "produto", "nome", "nome_produto", "titulo") or "Produto sem nome"
            )
            loja = _obter_campo(item, "loja", "site") or "Nao informado"
            preco = _formatar_moeda(_obter_campo(item, "preco", "preco_atual", "menor_preco"))
            url = _obter_campo(item, "url", "link", "href")
            recomendacao_ml = _formatar_ml(_obter_campo(item, "recomendacao_ml", "classe_ml"))
            probabilidade_ml = _formatar_probabilidade(
                _obter_campo(item, "probabilidade_vale_comprar", "probabilidade_ml")
            )

            bloco = [
                f"{i}. {produto}",
                f"   Loja: {loja}",
                f"   Preço: {preco}",
            ]

            if recomendacao_ml:
                bloco.append(f"   Recomendação ML: {recomendacao_ml}")

            if probabilidade_ml:
                bloco.append(f"   Chance: {probabilidade_ml}")

            if url:
                bloco.append(f"   Link: {url}")

            linhas.append("\n".join(bloco))
            linhas.append("")

    if combos:
        linhas.extend(["COMPRE JUNTO", separador])

        for i, item in enumerate(combos, start=1):
            produto_principal = _texto_curto(_obter_campo(item, "produto_principal") or "Produto sem nome")
            loja_principal = _obter_campo(item, "loja_principal") or "Nao informado"
            preco_principal = _formatar_moeda(_obter_campo(item, "preco_principal"))
            produto_recomendado = _texto_curto(_obter_campo(item, "produto_recomendado") or "Produto sem nome")
            loja_recomendada = _obter_campo(item, "loja_recomendada") or "Nao informado"
            preco_recomendado = _formatar_moeda(_obter_campo(item, "preco_recomendado"))
            preco_total = _formatar_moeda(_obter_campo(item, "preco_total_combo"))
            economia_frete = _obter_campo(item, "economia_frete") or "Nao informado"
            criterio = _obter_campo(item, "criterio_recomendacao")
            link_principal = _obter_campo(item, "link_principal")
            link_recomendado = _obter_campo(item, "link_recomendado")

            bloco = [
                f"{i}. Combo recomendado",
                f"   **Principal**: {produto_principal}",
                f"   Loja: {loja_principal} | {preco_principal}",
                f"   **Complemento**: {produto_recomendado}",
                f"   Loja: {loja_recomendada} | {preco_recomendado}",
                f"   **Total**: {preco_total}",
                f"   Frete economizado: {economia_frete}",
            ]

            if criterio:
                bloco.append(f"   Critério: {criterio}")

            if link_principal:
                bloco.append(f"   Link principal: {link_principal}")

            if link_recomendado:
                bloco.append(f"   Link complemento: {link_recomendado}")

            linhas.append("\n".join(bloco))
            linhas.append("")

    return "\n".join(linhas).strip()


def montar_mensagem(itens: list[dict[str, Any]], combos: list[dict[str, Any]] | None = None) -> str:
    """Monta mensagem com tags HTML para negrito no Telegram."""
    combos = combos or []

    if not itens and not combos:
        return "<b>GLOWWISE SKINCARE</b>\n\nNenhuma oferta encontrada na aba melhores_precos."

    separador = "-" * 34
    linhas = [
        "<b>GLOWWISE SKINCARE</b>",
        "<b>Resumo de oportunidades</b>",
        separador,
        "",
    ]

    if itens:
        linhas.extend(["<b>MELHORES PRECOS</b>", separador])

        for i, item in enumerate(itens, start=1):
            produto = _texto_curto(
                _obter_campo(item, "produto", "nome", "nome_produto", "titulo") or "Produto sem nome"
            )
            loja = _obter_campo(item, "loja", "site") or "Nao informado"
            preco = _formatar_moeda(_obter_campo(item, "preco", "preco_atual", "menor_preco"))
            url = _obter_campo(item, "url", "link", "href")
            recomendacao_ml = _formatar_ml(_obter_campo(item, "recomendacao_ml", "classe_ml"))
            probabilidade_ml = _formatar_probabilidade(
                _obter_campo(item, "probabilidade_vale_comprar", "probabilidade_ml")
            )

            bloco = [
                f"<b>{i}. {_html(produto)}</b>",
                f"   <b>Loja:</b> {_html(loja)}",
                f"   <b>Preco:</b> {_html(preco)}",
            ]

            if recomendacao_ml:
                bloco.append(f"   <b>Recomendacao ML:</b> {_html(recomendacao_ml)}")

            if probabilidade_ml:
                bloco.append(f"   <b>Chance:</b> {_html(probabilidade_ml)}")

            if url:
                bloco.append(f"   <b>Link:</b> {_html(url)}")

            linhas.append("\n".join(bloco))
            linhas.append("")

    if combos:
        linhas.extend(["<b>COMPRE JUNTO</b>", separador])

        for i, item in enumerate(combos, start=1):
            produto_principal = _texto_curto(_obter_campo(item, "produto_principal") or "Produto sem nome")
            loja_principal = _obter_campo(item, "loja_principal") or "Nao informado"
            preco_principal = _formatar_moeda(_obter_campo(item, "preco_principal"))
            produto_recomendado = _texto_curto(_obter_campo(item, "produto_recomendado") or "Produto sem nome")
            loja_recomendada = _obter_campo(item, "loja_recomendada") or "Nao informado"
            preco_recomendado = _formatar_moeda(_obter_campo(item, "preco_recomendado"))
            preco_total = _formatar_moeda(_obter_campo(item, "preco_total_combo"))
            economia_frete = _obter_campo(item, "economia_frete") or "Nao informado"
            criterio = _obter_campo(item, "criterio_recomendacao")
            link_principal = _obter_campo(item, "link_principal")
            link_recomendado = _obter_campo(item, "link_recomendado")

            bloco = [
                f"<b>{i}. Combo recomendado</b>",
                f"   <b>Principal:</b> {_html(produto_principal)}",
                f"   <b>Loja:</b> {_html(loja_principal)} | {_html(preco_principal)}",
                f"   <b>Complemento:</b> {_html(produto_recomendado)}",
                f"   <b>Loja:</b> {_html(loja_recomendada)} | {_html(preco_recomendado)}",
                f"   <b>Total:</b> {_html(preco_total)}",
                f"   <b>Frete economizado:</b> {_html(economia_frete)}",
            ]

            if criterio:
                bloco.append(f"   <b>Criterio:</b> {_html(criterio)}")

            if link_principal:
                bloco.append(f"   <b>Link principal:</b> {_html(link_principal)}")

            if link_recomendado:
                bloco.append(f"   <b>Link complemento:</b> {_html(link_recomendado)}")

            linhas.append("\n".join(bloco))
            linhas.append("")

    return "\n".join(linhas).strip()


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
        return telegram.bot.send_message(chat_id=destino, text=texto, parse_mode="HTML")

    print(f"[INFO] Enviando Telegram por nome de grupo: {destino!r}")
    chat_id = telegram._find_chat_id(group=destino)
    return telegram.bot.send_message(chat_id=chat_id, text=texto, parse_mode="HTML")


def enviar_telegram(maestro, mensagem: str) -> None:
    """Envia a mensagem para o Telegram usando o plugin oficial do BotCity."""
    telegram, group = iniciar_telegram(maestro)

    print(f"[INFO] Destino Telegram configurado no Vault: {group!r}")

    partes = dividir_mensagem(mensagem)
    if not partes:
        raise RuntimeError("Não há conteúdo para enviar ao Telegram.")

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
        "RELATÓRIO DO BOT DE ALERTA",
        f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        f"Quantidade de itens enviados: {quantidade_itens}",
        "",
        "MENSAGEM ENVIADA:",
        "",
        mensagem,
    ]

    arquivo.write_text("\n".join(conteudo), encoding="utf-8")
    print(f"[OK] Relatório salvo em: {arquivo}")
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
        itens = executar_com_timeout(
            ler_melhores_precos,
            TIMEOUT_SHEETS_SEGUNDOS,
            "leitura da aba melhores_precos",
            gs,
        )
        combos = executar_com_timeout(
            ler_compre_junto,
            TIMEOUT_SHEETS_SEGUNDOS,
            "leitura da aba compre_junto",
            gs,
        )
        total_items = len(itens) + len(combos)

        mensagem = montar_mensagem(itens, combos)
        executar_com_timeout(
            enviar_telegram,
            TIMEOUT_TELEGRAM_SEGUNDOS,
            "envio ao Telegram",
            maestro,
            mensagem,
        )

        relatorio = salvar_relatorio_alerta(mensagem, total_items)
        executar_com_timeout(
            maestro.post_artifact,
            TIMEOUT_ARTIFACT_SEGUNDOS,
            "envio do artifact do alerta",
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

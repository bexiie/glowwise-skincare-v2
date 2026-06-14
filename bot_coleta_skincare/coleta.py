"""
coleta.py - Bot Coletor (GlowWise Skincare)

Responsabilidades:
- Buscar produtos de skincare na Drogasil e na Beleza na Web
- Extrair nome, preco e link de cada produto
- Salvar resultados em coleta.json e enviar ao DataPool do Maestro
- Reportar status final da task no Runner/Maestro
"""

import os
import re
import time
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from botcity.web import WebBot, Browser
from webdriver_manager.chrome import ChromeDriverManager
from botcity.maestro import (
    BotMaestroSDK,
    DataPoolEntry,
    AutomationTaskFinishStatus,
)

from utils import (
    agora_str,
    salvar_json,
    normalizar_preco,
    limpar_texto,
    nome_produto_valido,
)


# =========================
# CONFIGURAÇÕES
# =========================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATAPOOL_LABEL = "rebecca-skincare-monitoramento"
DATAPOOL_TERMOS_LABEL = "rebecca-skincare-termos"

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "coleta"
OUTPUT_JSON = ARTIFACTS_DIR / "coleta.json"

BASES = ["hidratante facial", "gel de limpeza facial"]
ATIVOS = ["vitamina c", "niacinamida"]

MAX_RESULTADOS_POR_LOJA = 10
TEMPO_ESPERA = 2
WAIT_TIMEOUT = 6
TIMEOUT_BUSCA_SEGUNDOS = int(os.getenv("TIMEOUT_BUSCA_SEGUNDOS", "45"))
TIMEOUT_DATAPOOL_SEGUNDOS = int(os.getenv("TIMEOUT_DATAPOOL_SEGUNDOS", "60"))
TIMEOUT_ARTIFACT_SEGUNDOS = int(os.getenv("TIMEOUT_ARTIFACT_SEGUNDOS", "30"))

BASE_URL_POR_LOJA = {
    "Drogasil": "https://www.drogasil.com.br",
    "Beleza na Web": "https://www.belezanaweb.com.br",
}

BotMaestroSDK.RAISE_NOT_CONNECTED = True


# =========================
# HELPERS
# =========================
def garantir_pasta(caminho):
    os.makedirs(caminho, exist_ok=True)


def esperar(segundos=TEMPO_ESPERA):
    time.sleep(segundos)


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


def gerar_termos_padrao():
    return [f"{b} {a}" for b in BASES for a in ATIVOS]


def ler_bool_env(nome_variavel: str, padrao: bool = False) -> bool:
    """
    Lê uma variável de ambiente booleana.
    Se não existir, usa o valor padrão informado.
    """
    valor = os.getenv(nome_variavel)

    if valor is None:
        return padrao

    return str(valor).strip().lower() in ("1", "true", "yes", "sim", "on")


# =========================
# MAESTRO
# =========================
def iniciar_maestro():
    """Inicia o Maestro quando disponivel; em modo local, simula a execucao."""
    modo_local = os.getenv("MODO_LOCAL", "false").lower() == "true"

    if modo_local:
        print("[INFO] Execução local ativada.")
        print("[INFO] Maestro/DataPool serão ignorados nesta execução.")
        execution = SimpleNamespace(task_id="local", parameters={})
        return None, execution

    maestro = BotMaestroSDK.from_sys_args()

    server = os.getenv("MAESTRO_SERVER")
    login = os.getenv("MAESTRO_LOGIN")
    key = os.getenv("MAESTRO_KEY")

    if server and login and key:
        maestro.login(server=server, login=login, key=key)

    execution = maestro.get_execution()
    print(f"[INFO] Task ID: {execution.task_id}")
    return maestro, execution


def finalizar_task(maestro, execution, status, mensagem, total_items=0, processed_items=0, failed_items=0):
    """Finaliza execução no Maestro"""
    if maestro is None:
        print(f"[TASK LOCAL] {status.name}: {mensagem}")
        return

    maestro.finish_task(
        task_id=execution.task_id,
        status=status,
        message=mensagem,
        total_items=int(total_items),
        processed_items=int(processed_items),
        failed_items=int(failed_items),
    )
    print(f"[TASK] {status.name}: {mensagem}")


# =========================
# WEB BOT
# =========================
def criar_bot():
    bot = WebBot()
    bot.browser = Browser.CHROME
    bot.headless = ler_bool_env("BOTCITY_HEADLESS", padrao=True)
    print(f"[INFO] Navegador headless: {bot.headless}")

    driver_path = os.getenv("CHROMEDRIVER_PATH")
    if driver_path:
        bot.driver_path = driver_path
        print(f"[INFO] Usando CHROMEDRIVER_PATH configurado.")
    else:
        bot.driver_path = ChromeDriverManager().install()
        print(f"[INFO] ChromeDriver obtido automaticamente.")

    return bot


# =========================
# EXTRAÇÃO
# =========================
def extrair_preco(texto):
    """Extrai o preco vigente, priorizando valores promocionais."""
    texto = limpar_texto(texto)
    if not texto:
        return None

    padrao_preco = r"R\$\s*\d+(?:\.\d{3})*,\d{2}"
    padroes_promocionais = [
        rf"\bpor\s*({padrao_preco})",
        rf"({padrao_preco})\s*(?:a|à)\s*vista",
        rf"({padrao_preco})\s*pagando\s*no\s*pix",
        rf"pix\s*({padrao_preco})",
    ]

    for padrao in padroes_promocionais:
        match = re.search(padrao, texto, flags=re.IGNORECASE)
        if match:
            return normalizar_preco(match.group(1))

    precos = [normalizar_preco(valor) for valor in re.findall(padrao_preco, texto)]
    precos = [preco for preco in precos if preco is not None]
    return min(precos) if precos else None


def limpar_link_produto(link, loja):
    """Remove redirecionadores e transforma links relativos em URLs completas."""
    if not link:
        return ""

    link = str(link).strip()
    url_analisada = urlparse(link)

    if "criteo.com" in url_analisada.netloc.lower():
        parametros = parse_qs(url_analisada.query)
        destino = parametros.get("dest", [link])[0]
        link = unquote(destino)

    base_url = BASE_URL_POR_LOJA.get(loja, "")
    if base_url:
        link = urljoin(base_url, link)

    return link


def extrair_cards(html, termo, loja):
    """Extrai produtos genéricos"""
    soup = BeautifulSoup(html, "lxml")
    produtos = []

    if loja == "Drogasil":
        for link_tag in soup.select("a[href*='origin=search'], a[href$='.html']"):
            nome = limpar_texto(link_tag.get_text(" "))
            link = limpar_link_produto(link_tag.get("href"), loja)

            if not nome or not link or not nome_produto_valido(nome):
                continue

            preco = None
            bloco = link_tag
            for _ in range(8):
                texto_bloco = limpar_texto(bloco.get_text(" ")) if bloco else ""
                preco = extrair_preco(texto_bloco)
                if preco:
                    break
                bloco = bloco.parent if bloco else None

            if preco:
                produtos.append({
                    "produto": nome[:120],
                    "termo_busca": termo,
                    "loja": loja,
                    "preco": preco,
                    "link": link,
                    "disponivel": True,
                    "data_coleta": agora_str(),
                })

    seletores_blocos = [
        "[data-testid*='product']",
        "[class*='product']",
        "[class*='Product']",
        "[class*='card']",
        "[class*='Card']",
        "li",
        "article",
    ]

    for bloco in soup.select(",".join(seletores_blocos)):
        texto = limpar_texto(bloco.get_text(" "))
        preco = extrair_preco(texto)
        link_tag = bloco.select_one("a[href]")
        link = limpar_link_produto(link_tag.get("href"), loja) if link_tag else ""
        nome = limpar_texto(link_tag.get_text(" ")) if link_tag else texto

        if nome and preco and link and nome_produto_valido(nome):
            produtos.append({
                "produto": nome[:120],
                "termo_busca": termo,
                "loja": loja,
                "preco": preco,
                "link": link,
                "disponivel": True,
                "data_coleta": agora_str(),
            })

    for card in soup.select("a[href]"):
        texto = limpar_texto(card.get_text())

        if not texto:
            continue

        nome = texto[:120]
        preco = extrair_preco(texto)
        link = limpar_link_produto(card.get("href"), loja)

        if nome_produto_valido(nome) and preco:
            produtos.append({
                "produto": nome,
                "termo_busca": termo,
                "loja": loja,
                "preco": preco,
                "link": link,
                "disponivel": True,
                "data_coleta": agora_str(),
            })

    unicos = []
    links_vistos = set()
    for produto in produtos:
        chave = produto["link"] or f"{produto['produto']}|{produto['preco']}"
        if chave in links_vistos:
            continue
        links_vistos.add(chave)
        unicos.append(produto)

    print(f"[INFO] {loja} - {termo}: {len(unicos)} produto(s) extraido(s).")
    return unicos[:MAX_RESULTADOS_POR_LOJA]


# =========================
# BUSCAS
# =========================
def buscar(bot, url, termo, loja):
    """Executa busca genérica"""
    try:
        if bot.driver:
            bot.driver.set_page_load_timeout(WAIT_TIMEOUT)
            bot.driver.set_script_timeout(WAIT_TIMEOUT)
    except Exception:
        pass

    try:
        bot.browse(url)
    except Exception as e:
        print(f"[AVISO] Timeout/falha ao carregar {loja} para '{termo}': {e}")
        try:
            bot.driver.execute_script("window.stop();")
        except Exception:
            pass
        return []

    if not bot.driver:
        print(f"[AVISO] Driver indisponivel apos tentar carregar {loja} para '{termo}'.")
        return []

    try:
        WebDriverWait(bot.driver, WAIT_TIMEOUT).until(
            lambda d: d.find_elements(By.TAG_NAME, "a")
        )
    except Exception:
        esperar(1)

    try:
        html = bot.driver.page_source
    except Exception as e:
        print(f"[AVISO] Nao foi possivel ler HTML de {loja} para '{termo}': {e}")
        return []

    return extrair_cards(html, termo, loja)


# =========================
# DATAPOOL
# =========================
def enviar_datapool(maestro, registros):
    """Envia dados para DataPool"""
    if maestro is None:
        print("[INFO] Execução local: envio ao DataPool ignorado.")
        return

    datapool = maestro.get_datapool(label=DATAPOOL_LABEL)

    for item in registros:
        datapool.create_entry(DataPoolEntry(values=item))

    print(f"[OK] Registros enviados ao DataPool: {len(registros)}")


# =========================
# MAIN
# =========================
def main():
    garantir_pasta(ARTIFACTS_DIR)

    maestro, execution = iniciar_maestro()
    bot = criar_bot()

    registros = []
    termos = gerar_termos_padrao()

    try:
        for termo in termos:
            print(f"[BUSCA] {termo}")

            registros += executar_com_timeout(
                buscar,
                TIMEOUT_BUSCA_SEGUNDOS,
                f"busca Drogasil para {termo}",
                bot,
                f"https://www.drogasil.com.br/search?w={termo}",
                termo,
                "Drogasil",
            )

            registros += executar_com_timeout(
                buscar,
                TIMEOUT_BUSCA_SEGUNDOS,
                f"busca Beleza na Web para {termo}",
                bot,
                f"https://www.belezanaweb.com.br/busca?q={termo}",
                termo,
                "Beleza na Web",
            )

        salvar_json(registros, str(OUTPUT_JSON))
        executar_com_timeout(
            enviar_datapool,
            TIMEOUT_DATAPOOL_SEGUNDOS,
            "envio ao DataPool",
            maestro,
            registros,
        )

        if maestro is not None:
            executar_com_timeout(
                maestro.post_artifact,
                TIMEOUT_ARTIFACT_SEGUNDOS,
                "envio do artifact coleta.json",
                task_id=execution.task_id,
                artifact_name="coleta.json",
                filepath=str(OUTPUT_JSON),
            )

        finalizar_task(
            maestro,
            execution,
            AutomationTaskFinishStatus.SUCCESS,
            f"{len(registros)} registros coletados",
            total_items=len(termos),
            processed_items=len(termos),
        )

    except Exception as e:
        if maestro is not None:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.FAILED,
                str(e),
            )
        else:
            print(f"[ERRO LOCAL] {e}")
        raise

    finally:
        bot.stop_browser()


if __name__ == "__main__":
    main()

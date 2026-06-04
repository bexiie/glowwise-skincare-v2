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
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from botcity.web import WebBot, Browser
import uuid
from urllib.parse import urljoin
from types import SimpleNamespace
from webdriver_manager.chrome import ChromeDriverManager
from botcity.maestro import (
    BotMaestroSDK,
    DataPoolEntry,
    AutomationTaskFinishStatus,
)

from utils import (
    agora_str,
    salvar_json,
    salvar_csv_acumulado,
    normalizar_preco,
    limpar_texto,
    nome_produto_valido,
)


# =========================
# CONFIGURAÇÕES
# =========================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

load_dotenv(BASE_DIR / ".env")

DATAPOOL_LABEL = "rebecca-skincare-monitoramento"
DATAPOOL_TERMOS_LABEL = "rebecca-skincare-termos"

ARTIFACTS_DIR = BASE_DIR / "artifacts" / "coleta"
OUTPUT_JSON = ARTIFACTS_DIR / "coleta.json"

RAW_DATA_DIR = PROJECT_DIR / "data" / "raw"
EXECUCOES_DIR = RAW_DATA_DIR / "execucoes"

RUN_ID = uuid.uuid4().hex[:12]

OUTPUT_CSV_HISTORICO = RAW_DATA_DIR / "produtos_coletados_historico.csv"
OUTPUT_CSV_EXECUCAO = EXECUCOES_DIR / f"produtos_coletados_{RUN_ID}.csv"

COLUNAS_DATASET = [
    "run_id",
    "produto",
    "termo_busca",
    "categoria_busca",
    "loja",
    "preco",
    "link",
    "disponivel",
    "data_coleta",
]

TERMOS_PESQUISA = [
    "gel de limpeza facial",
    "hidratante facial",
    "protetor solar facial",
    "serum facial vitamina c",
    "serum facial niacinamida",
    "serum facial acido salicilico",
]

MAX_RESULTADOS_POR_LOJA = 15
TEMPO_ESPERA = 2
WAIT_TIMEOUT = 5

BotMaestroSDK.RAISE_NOT_CONNECTED = True


# =========================
# HELPERS
# =========================
def garantir_pasta(caminho):
    os.makedirs(caminho, exist_ok=True)


def esperar(segundos=TEMPO_ESPERA):
    time.sleep(segundos)


def gerar_termos_padrao():
     return TERMOS_PESQUISA.copy()


def ler_bool_env(nome_variavel: str, padrao: bool = False) -> bool:
    """
    Lê uma variável de ambiente booleana.
    Se não existir, usa o valor padrão informado.
    """
    valor = os.getenv(nome_variavel)

    if valor is None:
        return padrao

    return str(valor).strip().lower() in ("1", "true", "yes", "sim", "on")


def limpar_nome_produto(texto):
    """
    Remove trechos promocionais e de preço do nome do produto.
    """
    texto = limpar_texto(texto)

    cortes = [
        " de R$",
        " por R$",
        " pagando no pix",
        " avaliado com nota",
        " com desconto",
        " em até",
    ]

    for corte in cortes:
        pos = texto.lower().find(corte.lower())
        if pos != -1:
            texto = texto[:pos].strip()

    ruidos_inicio = [
        "Outlet ,",
        "Preço menor no App ,",
        "Chegou na Beleza ,",
        "Cruelty Free ,",
        "Vegano ,",
        "Dermocosmético ,",
        "VIRAL NO TIKTOK ⚠️ ,",
    ]

    for prefixo in ruidos_inicio:
        if texto.startswith(prefixo):
            texto = texto[len(prefixo):].strip(" ,")

    return texto[:120]


def produto_corresponde_ao_termo(nome, termo):
    nome = limpar_texto(nome).lower()
    termo = limpar_texto(termo).lower()

    palavras_relevantes = [
        p for p in termo.split()
        if p not in {"hidratante", "facial", "gel", "de", "limpeza"}
    ]

    return all(p in nome for p in palavras_relevantes)


# =========================
# MAESTRO
# =========================
def iniciar_maestro():
    """
    Inicia o Maestro quando o bot é executado pelo Runner.
    Durante testes locais, cria uma execução simulada para permitir
    que a raspagem e a geração dos arquivos funcionem normalmente.
    """
    modo_local = os.getenv("MODO_LOCAL", "true").lower() == "true"

    if modo_local:
        print("[INFO] Execução local ativada.")
        print("[INFO] Integrações com Maestro e DataPool serão ignoradas neste teste.")

        execution = SimpleNamespace(
            task_id=f"local-{RUN_ID}",
            parameters={},
        )

        return None, execution

    maestro = BotMaestroSDK.from_sys_args()
    execution = maestro.get_execution()

    print(f"[INFO] Execução recebida pelo Maestro: {execution.task_id}")

    return maestro, execution


def finalizar_task(maestro, execution, status, mensagem, total_items=0, processed_items=0, failed_items=0):
    """Finaliza execução no Maestro"""
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
    bot.headless = ler_bool_env("BOTCITY_HEADLESS", padrao=False)
    print(f"[INFO] Navegador headless: {bot.headless}")

    driver_path = os.getenv("CHROMEDRIVER_PATH")
    if driver_path:
        bot.driver_path = driver_path
        print(f"[INFO] Usando CHROMEDRIVER_PATH configurado.")
    else:
        bot.driver_path = ChromeDriverManager().install()
        print(f"[INFO] ChromeDriver obtido automaticamente.")

    return bot


def identificar_categoria_busca(termo: str) -> str:
    """Identifica a categoria principal a partir do termo pesquisado."""
    termo = limpar_texto(termo).lower()

    if "limpeza" in termo:
        return "limpeza"

    if "hidratante" in termo:
        return "hidratacao"

    if "protetor solar" in termo:
        return "protecao_solar"

    if "serum" in termo:
        return "tratamento"

    return "outros"


def normalizar_link(link: str, loja: str) -> str:
    """Transforma links relativos em links absolutos."""
    bases = {
        "Drogasil": "https://www.drogasil.com.br",
        "Beleza na Web": "https://www.belezanaweb.com.br",
    }

    return urljoin(bases.get(loja, ""), link or "")


def montar_registro(produto, termo, loja, preco, link):
    """Cria um registro padronizado para JSON, CSV e DataPool."""
    return {
        "run_id": RUN_ID,
        "produto": limpar_texto(produto),
        "termo_busca": limpar_texto(termo).lower(),
        "categoria_busca": identificar_categoria_busca(termo),
        "loja": loja,
        "preco": float(preco),
        "link": normalizar_link(link, loja),
        "disponivel": True,
        "data_coleta": agora_str(),
    }


def remover_duplicados(registros):
    """
    Remove repetições dentro da mesma execução.
    Mantém o termo pesquisado na chave porque um produto pode aparecer
    legitimamente em pesquisas diferentes.
    """
    unicos = {}

    for item in registros:
        chave = (
            item["termo_busca"],
            item["produto"].lower(),
            item["loja"],
            item["preco"],
            item["link"],
        )

        unicos[chave] = item

    return list(unicos.values())

# =========================
# EXTRAÇÃO
# =========================
def extrair_preco(texto):
    """Extrai preço de string"""
    match = re.search(r"R\$\s*\d+[.,]\d{2}", texto or "")
    return normalizar_preco(match.group()) if match else None


def extrair_cards(html, termo, loja):
    """Extrai produtos genéricos"""
    soup = BeautifulSoup(html, "lxml")
    produtos = []

    for card in soup.select("a[href]"):
        link = card.get("href")
        if not link:
            continue

        texto = limpar_texto(card.get_text(" ", strip=True))
        if not texto:
            continue

        nome = limpar_nome_produto(texto)
        preco = extrair_preco(texto)

        if nome_produto_valido(nome) and preco:
            if not produto_corresponde_ao_termo(nome, termo):
                continue

            produtos.append(
                montar_registro(
                    produto=nome,
                    termo=termo,
                    loja=loja,
                    preco=preco,
                    link=link,
                )
            )

    return produtos[:MAX_RESULTADOS_POR_LOJA]


def extrair_cards_drogasil(driver, termo):
    produtos = []

    titulos = driver.find_elements(
        By.XPATH,
        "//h2[.//a or normalize-space(text()) != '']"
    )

    for titulo in titulos:
        try:
            nome = limpar_nome_produto(limpar_texto(titulo.text))
            if not nome_produto_valido(nome):
                continue

            if not produto_corresponde_ao_termo(nome, termo):
                continue

            bloco = titulo
            for _ in range(5):
                try:
                    bloco = bloco.find_element(By.XPATH, "..")
                except Exception:
                    break

                texto_bloco = limpar_texto(bloco.text)
                preco = extrair_preco(texto_bloco)

                if preco:
                    link = None
                    try:
                        a = bloco.find_element(By.XPATH, ".//a[@href]")
                        link = a.get_attribute("href")
                    except Exception:
                        link = None

                    if link and "/search?" not in link:
                        produtos.append(
                            montar_registro(
                                produto=nome,
                                termo=termo,
                                loja="Drogasil",
                                preco=preco,
                                link=link,
                            )
                        )
                        break

        except Exception:
            continue

    return produtos[:MAX_RESULTADOS_POR_LOJA]


# =========================
# BUSCAS
# =========================
def buscar(bot, url, termo, loja):
    """Executa busca genérica"""
    bot.browse(url)

    try:
        WebDriverWait(bot.driver, WAIT_TIMEOUT).until(
            lambda d: len(d.find_elements(By.TAG_NAME, "a")) > 20
        )
    except Exception:
        esperar(1)

    esperar(2)

    if loja == "Drogasil":
        return extrair_cards_drogasil(bot.driver, termo)

    html = bot.driver.page_source
    return extrair_cards(html, termo, loja)


# =========================
# DATAPOOL
# =========================
def enviar_datapool(maestro, registros):
    if maestro is None:
        print("[INFO] Execução local: envio ao DataPool ignorado.")
    return
    """Envia dados para DataPool"""
    datapool = maestro.get_datapool(label=DATAPOOL_LABEL)

    for item in registros:
        datapool.create_entry(DataPoolEntry(values=item))

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

            resultado_drogasil = buscar(
                bot,
                f"https://www.drogasil.com.br/search?w={termo.replace(' ', '+')}",
                termo,
                "Drogasil",
            )
            print(f"[INFO] Drogasil - {termo}: {len(resultado_drogasil)} itens")
            registros += resultado_drogasil

            resultado_bnw = buscar(
                bot,
                f"https://www.belezanaweb.com.br/busca?q={termo.replace(' ', '+')}",
                termo,
                "Beleza na Web",
            )
            print(f"[INFO] Beleza na Web - {termo}: {len(resultado_bnw)} itens")
            registros += resultado_bnw

        if not registros:
            finalizar_task(
                maestro,
                execution,
                AutomationTaskFinishStatus.FAILED,
                "Nenhum registro foi coletado.",
                total_items=len(termos),
                processed_items=0,
                failed_items=len(termos),
            )
            return

        registros = remover_duplicados(registros)

        print(f"[INFO] Registros após remoção de duplicados: {len(registros)}")
        print(f"[INFO] ID da execução: {RUN_ID}")

        salvar_json(registros, str(OUTPUT_JSON))

        salvar_csv_acumulado(
            registros,
            str(OUTPUT_CSV_EXECUCAO),
            COLUNAS_DATASET,
        )

        salvar_csv_acumulado(
            registros,
            str(OUTPUT_CSV_HISTORICO),
            COLUNAS_DATASET,
        )

        print(f"[OK] JSON salvo em: {OUTPUT_JSON}")
        print(f"[OK] CSV da execução salvo em: {OUTPUT_CSV_EXECUCAO}")
        print(f"[OK] Histórico atualizado em: {OUTPUT_CSV_HISTORICO}")

        enviar_datapool(maestro, registros)

        if maestro is not None:
            print(f"[OK] Registros enviados ao DataPool: {len(registros)}")
            maestro.post_artifact(
                task_id=execution.task_id,
                artifact_name="coleta.json",
                filepath=str(OUTPUT_JSON),
            )

        if maestro is not None:
            maestro.post_artifact(
                task_id=execution.task_id,
                artifact_name=f"produtos_coletados_{RUN_ID}.csv",
                filepath=str(OUTPUT_CSV_EXECUCAO),
            )

        if maestro is not None:
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
            raise

    finally:
        bot.stop_browser()


if __name__ == "__main__":
    main()
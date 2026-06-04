import json
import os
import re
import csv
from datetime import datetime


def agora_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def garantir_pasta(caminho):
    os.makedirs(caminho, exist_ok=True)


def salvar_json(dados, caminho):
    pasta = os.path.dirname(caminho)
    if pasta:
        garantir_pasta(pasta)

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def normalizar_preco(valor):
    if valor is None:
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None

    texto = texto.replace("R$", "").replace("\xa0", " ")
    texto = re.sub(r"[^\d,\.]", "", texto)

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None

def parece_uuid(texto):
    if not texto:
        return False
    padrao = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
    return re.match(padrao, texto.strip().lower()) is not None


def nome_produto_valido(nome):
    if not nome:
        return False

    nome = nome.strip()

    if len(nome) < 8:
        return False

    if parece_uuid(nome):
        return False

    bloqueados = [
        "access denied",
        "forbidden",
        "error",
        "ops",
        "não autorizado",
        "unauthorized",
        "request blocked"
    ]

    nome_lower = nome.lower()
    if any(p in nome_lower for p in bloqueados):
        return False

    return True

def limpar_texto(texto):
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip()

def salvar_csv_acumulado(dados, caminho, colunas):
    """
    Acrescenta registros a um arquivo CSV.
    Cria o cabeçalho somente quando o arquivo ainda não existe ou está vazio.
    """
    pasta = os.path.dirname(caminho)

    if pasta:
        garantir_pasta(pasta)

    arquivo_existe = os.path.exists(caminho)
    arquivo_vazio = not arquivo_existe or os.path.getsize(caminho) == 0

    with open(caminho, "a", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=colunas,
            extrasaction="ignore",
        )

        if arquivo_vazio:
            escritor.writeheader()

        escritor.writerows(dados)
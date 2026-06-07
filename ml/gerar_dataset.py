import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit, urlunsplit

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_JSON_PATH = PROJECT_DIR / "bot_coleta_skincare" / "artifacts" / "coleta" / "coleta.json"
RAW_DIR = PROJECT_DIR / "data" / "raw"
EXECUCOES_DIR = RAW_DIR / "execucoes"
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"

RAW_HISTORICO_PATH = RAW_DIR / "produtos_coletados_historico.csv"
PROCESSED_PATH = PROCESSED_DIR / "produtos_sanitizados.csv"

RAW_COLUMNS = [
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

TAGS_PROMOCIONAIS = {
    "aproveite",
    "cruelty free",
    "dermocosmetico",
    "dermocosmetico",
    "outlet",
    "preco menor no app",
    "preco menor no app",
    "vegano",
}


def limpar_texto(valor):
    if pd.isna(valor):
        return None

    texto = str(valor).strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s+,\s+", " , ", texto)
    return texto or None


def identificar_categoria_busca(termo):
    termo = limpar_texto(termo) or ""
    termo = termo.lower()

    if "limpeza" in termo:
        return "limpeza"
    if "hidratante" in termo:
        return "hidratacao"
    if "protetor solar" in termo:
        return "protecao_solar"
    if "serum" in termo or "vitamina c" in termo or "niacinamida" in termo:
        return "tratamento"

    return "outros"


def gerar_run_id(conteudo_json):
    return hashlib.sha1(conteudo_json.encode("utf-8")).hexdigest()[:12]


def carregar_coleta(json_path):
    conteudo = json_path.read_text(encoding="utf-8-sig")
    dados = json.loads(conteudo)

    if not isinstance(dados, list):
        raise ValueError("O arquivo coleta.json precisa conter uma lista de registros.")

    df = pd.DataFrame(dados)
    if df.empty:
        raise ValueError("O arquivo coleta.json esta vazio.")

    if "run_id" not in df.columns:
        df["run_id"] = gerar_run_id(conteudo)

    if "categoria_busca" not in df.columns:
        df["categoria_busca"] = df["termo_busca"].apply(identificar_categoria_busca)

    if "disponivel" not in df.columns:
        df["disponivel"] = True

    for coluna in RAW_COLUMNS:
        if coluna not in df.columns:
            df[coluna] = None

    df = df[RAW_COLUMNS]
    df["produto"] = df["produto"].apply(limpar_texto)
    df["termo_busca"] = df["termo_busca"].apply(limpar_texto).str.lower()
    df["categoria_busca"] = df["categoria_busca"].apply(limpar_texto).str.lower()
    df["loja"] = df["loja"].apply(limpar_texto)
    df["link"] = df["link"].apply(limpar_texto)
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df["data_coleta"] = pd.to_datetime(df["data_coleta"], errors="coerce")
    df["disponivel"] = df["disponivel"].fillna(True).astype(bool)

    return df


def limpar_link(link):
    if not isinstance(link, str):
        return link

    url_analisada = urlparse(link)
    if "criteo.com" in url_analisada.netloc.lower():
        parametros = parse_qs(url_analisada.query)
        destino = parametros.get("dest", [link])[0]
        return unquote(destino)

    return link


def canonicalizar_link(link):
    if not isinstance(link, str):
        return link

    partes = urlsplit(link.strip())
    caminho = partes.path.rstrip("/")
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), caminho, "", ""))


def normalizar_tag(texto):
    texto = str(texto).strip().lower()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def remover_trechos_preco(texto):
    cortes = [
        " de r$",
        " por r$",
        " pagando no pix",
        " avaliado com nota",
        " com desconto",
        " em ate",
        " em até",
        " cupom:",
    ]

    texto_lower = texto.lower()
    posicoes = [texto_lower.find(corte) for corte in cortes if texto_lower.find(corte) != -1]
    if posicoes:
        texto = texto[: min(posicoes)].strip()

    return texto


def limpar_produto(produto):
    texto = limpar_texto(produto)
    if texto is None:
        return None

    texto = remover_trechos_preco(texto)
    partes = [parte.strip() for parte in texto.split(",") if parte.strip()]
    if not partes:
        return texto

    partes_sem_tags = [
        parte
        for parte in partes
        if normalizar_tag(parte) not in TAGS_PROMOCIONAIS
        and not normalizar_tag(parte).startswith("vencimento ")
    ]

    if len(partes_sem_tags) >= 2:
        return partes_sem_tags[1]

    return partes_sem_tags[0] if partes_sem_tags else partes[0]


def extrair_marca(produto_original, produto_limpo):
    texto = limpar_texto(produto_original)
    if texto is None:
        return None

    partes = [parte.strip() for parte in texto.split(",") if parte.strip()]
    if len(partes) >= 2:
        return partes[0].title()

    palavras = str(produto_limpo).split()
    return palavras[0].title() if palavras else None


def sanitizar_dataset(df_raw):
    df = df_raw.copy()

    df["produto_original"] = df["produto"]
    df["link_original"] = df["link"]
    df["link"] = df["link_original"].apply(limpar_link)
    df["link_canonico"] = df["link"].apply(canonicalizar_link)
    df["produto"] = df["produto_original"].apply(limpar_produto)
    df["marca"] = df.apply(lambda row: extrair_marca(row["produto_original"], row["produto"]), axis=1)

    colunas_texto = [
        "produto_original",
        "produto",
        "marca",
        "termo_busca",
        "categoria_busca",
        "loja",
        "link_original",
        "link",
        "link_canonico",
    ]

    for coluna in colunas_texto:
        df[coluna] = df[coluna].apply(limpar_texto)

    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df["data_coleta"] = pd.to_datetime(df["data_coleta"], errors="coerce")
    df = df.dropna(subset=["produto", "preco", "link_canonico"])
    df = df.sort_values(["link_canonico", "preco"]).drop_duplicates("link_canonico", keep="first")
    df = df.reset_index(drop=True)

    return df[
        [
            "run_id",
            "produto_original",
            "produto",
            "marca",
            "termo_busca",
            "categoria_busca",
            "loja",
            "preco",
            "disponivel",
            "data_coleta",
            "link_original",
            "link",
            "link_canonico",
        ]
    ]


def salvar_dataset(df_execucao, append_historico):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXECUCOES_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    run_id = df_execucao["run_id"].iloc[0]
    execucao_path = EXECUCOES_DIR / f"produtos_coletados_{run_id}.csv"
    df_execucao.to_csv(execucao_path, index=False, encoding="utf-8-sig")

    if append_historico and RAW_HISTORICO_PATH.exists():
        df_historico = pd.read_csv(RAW_HISTORICO_PATH)
        df_raw = pd.concat([df_historico, df_execucao], ignore_index=True)
    else:
        df_raw = df_execucao

    df_raw = df_raw[RAW_COLUMNS]
    df_raw = df_raw.drop_duplicates()
    df_raw.to_csv(RAW_HISTORICO_PATH, index=False, encoding="utf-8-sig")

    df_processado = sanitizar_dataset(df_raw)
    df_processado.to_csv(PROCESSED_PATH, index=False, encoding="utf-8-sig")

    return execucao_path, RAW_HISTORICO_PATH, PROCESSED_PATH, len(df_execucao), len(df_raw), len(df_processado)


def main():
    parser = argparse.ArgumentParser(description="Gera datasets raw e processed a partir do coleta.json.")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON_PATH, help="Caminho do coleta.json.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Recria o historico bruto apenas com a coleta atual. Por padrao, acrescenta ao historico.",
    )
    args = parser.parse_args()

    df_execucao = carregar_coleta(args.json)
    resultados = salvar_dataset(df_execucao, append_historico=not args.overwrite)
    execucao_path, raw_path, processed_path, n_execucao, n_raw, n_processed = resultados

    print(f"Coleta atual: {n_execucao} registros")
    print(f"Historico bruto: {n_raw} registros")
    print(f"Dataset processado: {n_processed} registros")
    print(f"CSV da execucao: {execucao_path}")
    print(f"CSV historico: {raw_path}")
    print(f"CSV processado: {processed_path}")


if __name__ == "__main__":
    main()

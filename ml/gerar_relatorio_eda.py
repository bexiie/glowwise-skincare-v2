"""
Gera relatorio e graficos atualizados da EDA do GlowWise.

Saidas:
- reports/eda/resumo_eda.md
- reports/eda/distribuicao_categorias.png
- reports/eda/distribuicao_lojas.png
- reports/eda/distribuicao_alvo.png
- reports/eda/precos_por_categoria.png
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_PATH = PROJECT_DIR / "data" / "raw" / "produtos_coletados_historico.csv"
SANITIZED_PATH = PROJECT_DIR / "data" / "processed" / "produtos_sanitizados.csv"
ML_PATH = PROJECT_DIR / "data" / "processed" / "produtos_ml.csv"
REPORTS_DIR = PROJECT_DIR / "reports" / "eda"


def salvar_barra(serie: pd.Series, titulo: str, xlabel: str, ylabel: str, arquivo: str) -> None:
    ax = serie.plot(kind="bar", figsize=(8, 5), color="#4c78a8")
    ax.set_title(titulo)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.25)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / arquivo, dpi=160)
    plt.close()


def gerar_graficos(df: pd.DataFrame) -> None:
    salvar_barra(
        df["categoria_busca"].value_counts(),
        "Distribuicao por categoria",
        "Categoria",
        "Quantidade de produtos",
        "distribuicao_categorias.png",
    )
    salvar_barra(
        df["loja"].value_counts(),
        "Distribuicao por loja",
        "Loja",
        "Quantidade de produtos",
        "distribuicao_lojas.png",
    )
    salvar_barra(
        df["vale_comprar"].map({0: "nao_vale_comprar", 1: "vale_comprar"}).value_counts(),
        "Distribuicao da classe alvo",
        "Classe",
        "Quantidade de produtos",
        "distribuicao_alvo.png",
    )

    ax = df.boxplot(column="preco", by="categoria_busca", figsize=(9, 5), grid=False)
    ax.set_title("Precos por categoria")
    ax.set_xlabel("Categoria")
    ax.set_ylabel("Preco")
    plt.suptitle("")
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "precos_por_categoria.png", dpi=160)
    plt.close()


def gerar_resumo(df_raw: pd.DataFrame, df_sanitized: pd.DataFrame, df_ml: pd.DataFrame) -> None:
    alvo = df_ml["vale_comprar"].value_counts().rename(index={0: "nao_vale_comprar", 1: "vale_comprar"})
    alvo_percentual = (alvo / len(df_ml) * 100).round(1)
    categorias = (
        df_ml.groupby("categoria_busca")["preco"]
        .agg(registros="count", minimo="min", mediana="median", media="mean", maximo="max")
        .round(2)
        .sort_values("registros", ascending=False)
    )
    lojas = df_ml["loja"].value_counts()
    criteo_bruto = int(df_raw["link"].str.contains("criteo.com", case=False, na=False).sum()) if "link" in df_raw else 0
    criteo_limpo = int(df_sanitized["link"].str.contains("criteo.com", case=False, na=False).sum())

    linhas = [
        "# EDA GlowWise Skincare",
        "",
        "## Arquivos analisados",
        "",
        f"- Bruto consolidado: `{RAW_PATH.relative_to(PROJECT_DIR)}` ({len(df_raw)} registros)",
        f"- Sanitizado: `{SANITIZED_PATH.relative_to(PROJECT_DIR)}` ({len(df_sanitized)} registros)",
        f"- Dataset ML: `{ML_PATH.relative_to(PROJECT_DIR)}` ({len(df_ml)} registros)",
        "",
        "## Estrutura do dataset ML",
        "",
        f"- Instancias: {len(df_ml)}",
        f"- Colunas: {len(df_ml.columns)}",
        "- Features usadas no modelo: `texto_produto`, `categoria_busca`, `loja`, `preco`",
        "- Classe alvo: `vale_comprar`",
        "- Classes: `0 = nao_vale_comprar`, `1 = vale_comprar`",
        f"- Valores ausentes no dataset ML: {int(df_ml.isna().sum().sum())}",
        "",
        "## Balanceamento da classe alvo",
        "",
        pd.DataFrame({"registros": alvo, "percentual": alvo_percentual}).to_markdown(),
        "",
        "## Distribuicao por categoria",
        "",
        categorias.to_markdown(),
        "",
        "## Distribuicao por loja",
        "",
        lojas.to_frame("registros").to_markdown(),
        "",
        "## Qualidade dos dados",
        "",
        f"- Links Criteo no bruto: {criteo_bruto}",
        f"- Links Criteo apos limpeza: {criteo_limpo}",
        f"- Links canonicos duplicados no sanitizado: {int(df_sanitized['link_canonico'].duplicated().sum())}",
        f"- Registros sem preco no sanitizado: {int(df_sanitized['preco'].isna().sum())}",
        "",
        "## Leitura para os slides",
        "",
        "- A base atual tem 113 instancias, com 64 positivos e 49 negativos.",
        "- A classe positiva representa 56,6% da base, sem desbalanceamento extremo.",
        "- A categoria `tratamento` tem apenas 5 registros e deve ser expandida em novas coletas.",
        "- O dataset esta pronto para alimentar o pipeline de classificacao binaria do modelo.",
    ]
    (REPORTS_DIR / "resumo_eda.md").write_text("\n".join(linhas), encoding="utf-8")


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    df_raw = pd.read_csv(RAW_PATH)
    df_sanitized = pd.read_csv(SANITIZED_PATH)
    df_ml = pd.read_csv(ML_PATH)

    gerar_graficos(df_ml)
    gerar_resumo(df_raw, df_sanitized, df_ml)
    print(f"Relatorio de EDA salvo em: {REPORTS_DIR}")


if __name__ == "__main__":
    main()

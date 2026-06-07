"""
Gera relatorio HTML de drift com Evidently AI.

O relatorio compara uma amostra de referencia do dataset de ML com a amostra
atual. Em uma evolucao natural do projeto, a referencia pode ser congelada no
momento do treino e o current_data pode vir da coleta mais recente.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = PROJECT_DIR / "data" / "processed" / "produtos_ml.csv"
REPORTS_DIR = PROJECT_DIR / "reports" / "evidently"
DEFAULT_REPORT_PATH = REPORTS_DIR / "relatorio_drift.html"

COLUNAS_MONITORADAS = [
    "produto",
    "marca",
    "categoria_busca",
    "loja",
    "preco",
    "sinal_promocional",
    "vale_comprar",
]


def carregar_dataset(caminho: Path) -> pd.DataFrame:
    if not caminho.exists():
        raise FileNotFoundError(f"Dataset nao encontrado: {caminho}")

    df = pd.read_csv(caminho)
    faltantes = [coluna for coluna in COLUNAS_MONITORADAS if coluna not in df.columns]
    if faltantes:
        raise ValueError(f"Dataset sem colunas monitoradas: {faltantes}")

    df = df[COLUNAS_MONITORADAS].copy()
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df["sinal_promocional"] = pd.to_numeric(df["sinal_promocional"], errors="coerce").fillna(0)
    df["vale_comprar"] = pd.to_numeric(df["vale_comprar"], errors="coerce").fillna(0)

    for coluna in ["produto", "marca", "categoria_busca", "loja"]:
        df[coluna] = df[coluna].fillna("desconhecido").astype(str)

    return df.dropna(subset=["preco"]).reset_index(drop=True)


def dividir_referencia_atual(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(df) < 4:
        raise ValueError("Dataset pequeno demais para gerar comparacao de drift.")

    metade = max(len(df) // 2, 1)
    referencia = df.iloc[:metade].copy()
    atual = df.iloc[metade:].copy()

    if atual.empty:
        atual = referencia.copy()

    return referencia, atual


def gerar_relatorio(reference_data: pd.DataFrame, current_data: pd.DataFrame, output_path: Path) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report = Report(metrics=[DataDriftPreset()])
    resultado = report.run(reference_data=reference_data, current_data=current_data)
    resultado.save_html(str(output_path))

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera relatorio Evidently de drift do dataset GlowWise.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH, help="Dataset de ML monitorado.")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH, help="Caminho do HTML de saida.")
    args = parser.parse_args()

    df = carregar_dataset(args.dataset)
    reference_data, current_data = dividir_referencia_atual(df)
    output_path = gerar_relatorio(reference_data, current_data, args.output)

    print("Relatorio Evidently gerado com sucesso.")
    print(f"Data/hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Dataset: {args.dataset}")
    print(f"Referencia: {len(reference_data)} registros")
    print(f"Atual: {len(current_data)} registros")
    print(f"HTML: {output_path}")


if __name__ == "__main__":
    main()

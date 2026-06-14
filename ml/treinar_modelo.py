"""
Treina o modelo GlowWise de recomendacao vale_comprar / nao_vale_comprar.

O objetivo desta primeira versao e transformar o historico coletado pelo bot em
um classificador supervisionado simples, rastreado no MLflow e promovido para
uso pela FastAPI local.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow.tracking import MlflowClient
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_DIR / "data" / "processed" / "produtos_sanitizados.csv"
ML_DATASET_PATH = PROJECT_DIR / "data" / "processed" / "produtos_ml.csv"
MLRUNS_DIR = PROJECT_DIR / "mlruns"
MLFLOW_DB_PATH = PROJECT_DIR / "mlflow.db"
MLFLOW_TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH.as_posix()}"
MODEL_NAME = "glowwise-vale-comprar"
EXPERIMENT_NAME = "glowwise-recomendacao-skincare"

FEATURES = ["texto_produto", "categoria_busca", "loja", "preco"]
TARGET = "vale_comprar"

SINAIS_PROMOCIONAIS = (
    "outlet",
    "cupom",
    "desconto",
    "pix",
    "promo",
    "oferta",
)


def preparar_dataset(caminho: Path) -> pd.DataFrame:
    df = pd.read_csv(caminho)

    colunas_obrigatorias = [
        "produto_original",
        "produto",
        "marca",
        "termo_busca",
        "categoria_busca",
        "loja",
        "preco",
    ]
    faltantes = [coluna for coluna in colunas_obrigatorias if coluna not in df.columns]
    if faltantes:
        raise ValueError(f"Dataset sem colunas obrigatórias: {faltantes}")

    df = df.copy()
    df["preco"] = pd.to_numeric(df["preco"], errors="coerce")
    df = df.dropna(subset=["produto", "categoria_busca", "loja", "preco"])

    df["texto_produto"] = (
        df["produto"].fillna("")
        + " "
        + df["marca"].fillna("")
        + " "
        + df["termo_busca"].fillna("")
        + " "
        + df["produto_original"].fillna("")
    )

    df["sinal_promocional"] = df["produto_original"].fillna("").str.lower().apply(
        lambda texto: int(any(sinal in texto for sinal in SINAIS_PROMOCIONAIS))
    )

    q50_categoria = df.groupby("categoria_busca")["preco"].transform("median")
    q75_categoria = df.groupby("categoria_busca")["preco"].transform(lambda serie: serie.quantile(0.75))

    barato_na_categoria = df["preco"] <= q50_categoria
    promocional_com_teto = (df["sinal_promocional"] == 1) & (df["preco"] <= q75_categoria)
    df[TARGET] = (barato_na_categoria | promocional_com_teto).astype(int)

    return df


def criar_pipeline(modelo) -> Pipeline:
    preprocessador = ColumnTransformer(
        transformers=[
            ("texto", TfidfVectorizer(max_features=400, ngram_range=(1, 2)), "texto_produto"),
            (
                "categoricas",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="constant", fill_value="desconhecido")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                ["categoria_busca", "loja"],
            ),
            (
                "numericas",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                ["preco"],
            ),
        ]
    )

    return Pipeline(
        steps=[
            ("preprocessador", preprocessador),
            ("modelo", modelo),
        ]
    )


def modelos_candidatos() -> dict[str, object]:
    return {
        "logistic_regression": LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
        "random_forest": RandomForestClassifier(n_estimators=120, class_weight="balanced", random_state=42),
        "gradient_boosting": GradientBoostingClassifier(random_state=42),
    }


def avaliar(y_true, y_pred) -> dict[str, float]:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
    }


def promover_modelo(run_id: str, model_name: str) -> None:
    client = MlflowClient()
    versoes = client.search_model_versions(f"name = '{model_name}'")
    versao_vencedora = None

    for versao in versoes:
        if versao.run_id == run_id:
            versao_vencedora = versao.version
            break

    if versao_vencedora is None:
        raise RuntimeError(f"Não encontrei versão registrada para run_id={run_id}.")

    client.set_registered_model_alias(model_name, "production", versao_vencedora)
    print(f"Modelo promovido: {model_name} versao {versao_vencedora} alias @production")


def treinar(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset)
    df = preparar_dataset(dataset_path)

    ML_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ML_DATASET_PATH, index=False, encoding="utf-8-sig")

    X = df[FEATURES]
    y = df[TARGET]

    if y.nunique() < 2:
        raise ValueError("A rotulagem gerou apenas uma classe. Ajuste a heurística antes do treino.")

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=args.test_size,
        random_state=42,
        stratify=y,
    )

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    melhor = None
    resultados = []

    for nome, modelo in modelos_candidatos().items():
        pipeline = criar_pipeline(modelo)

        with mlflow.start_run(run_name=nome) as run:
            pipeline.fit(X_train, y_train)
            predicoes = pipeline.predict(X_test)
            metricas = avaliar(y_test, predicoes)

            mlflow.log_params(
                {
                    "algoritmo": nome,
                    "dataset": str(dataset_path.relative_to(PROJECT_DIR)),
                    "total_registros": int(len(df)),
                    "features": ",".join(FEATURES),
                    "rotulo": TARGET,
                }
            )
            mlflow.log_metrics(metricas)
            mlflow.log_artifact(str(ML_DATASET_PATH), artifact_path="dataset")
            mlflow.sklearn.log_model(
                sk_model=pipeline,
                name="modelo",
                registered_model_name=MODEL_NAME,
                input_example=X_train.head(3),
            )

            resultado = {
                "run_id": run.info.run_id,
                "modelo": nome,
                **metricas,
            }
            resultados.append(resultado)
            print(
                f"{nome}: "
                f"f1={metricas['f1']:.3f} "
                f"precision={metricas['precision']:.3f} "
                f"recall={metricas['recall']:.3f} "
                f"accuracy={metricas['accuracy']:.3f}"
            )

            if melhor is None or metricas["f1"] > melhor["f1"]:
                melhor = resultado

    if melhor is None:
        raise RuntimeError("Nenhum modelo foi treinado.")

    promover_modelo(melhor["run_id"], MODEL_NAME)
    print(f"Vencedor: {melhor['modelo']} com f1={melhor['f1']:.3f}")
    print(f"Dataset ML salvo em: {ML_DATASET_PATH}")
    print(f"MLflow Tracking URI: {MLFLOW_TRACKING_URI}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Treina e registra o modelo de recomendação GlowWise.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH, help="CSV processado usado no treino.")
    parser.add_argument("--test-size", type=float, default=0.25, help="Proporção de teste.")
    return parser.parse_args()


if __name__ == "__main__":
    treinar(parse_args())

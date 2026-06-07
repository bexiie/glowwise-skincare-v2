"""
FastAPI local do modelo GlowWise.

Execute a partir da raiz do projeto:
    uvicorn api.main:app --reload
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import mlflow
import mlflow.pyfunc
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel, Field


PROJECT_DIR = Path(__file__).resolve().parents[1]
MLRUNS_DIR = PROJECT_DIR / "mlruns"
MLFLOW_DB_PATH = PROJECT_DIR / "mlflow.db"
MLFLOW_TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH.as_posix()}"
MODEL_NAME = "glowwise-vale-comprar"
MODEL_URI = f"models:/{MODEL_NAME}@production"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

app = FastAPI(title="GlowWise ML API", version="1.0.0")
_modelo = None


class ProdutoEntrada(BaseModel):
    produto: str = Field(..., examples=["Gel de Limpeza Facial Vitamina C 120g"])
    marca: str = Field(default="", examples=["Garnier"])
    termo_busca: str = Field(default="", examples=["gel de limpeza facial vitamina c"])
    categoria_busca: str = Field(default="", examples=["limpeza"])
    loja: str = Field(default="", examples=["Beleza na Web"])
    preco: float = Field(..., gt=0, examples=[48.9])
    produto_original: str = Field(default="", examples=["Garnier, Gel de Limpeza, Outlet, por R$48,90"])


class PredicaoSaida(BaseModel):
    classe: Literal["vale_comprar", "nao_vale_comprar"]
    vale_comprar: bool
    probabilidade_vale_comprar: float | None
    modelo: str
    justificativa: str


def carregar_modelo():
    global _modelo
    if _modelo is None:
        _modelo = mlflow.pyfunc.load_model(MODEL_URI)
    return _modelo


def montar_dataframe(produto: ProdutoEntrada) -> pd.DataFrame:
    texto_produto = " ".join(
        [
            produto.produto,
            produto.marca,
            produto.termo_busca,
            produto.produto_original,
        ]
    )

    return pd.DataFrame(
        [
            {
                "texto_produto": texto_produto,
                "categoria_busca": produto.categoria_busca,
                "loja": produto.loja,
                "preco": produto.preco,
            }
        ]
    )


def obter_probabilidade(modelo, dados: pd.DataFrame) -> float | None:
    predict_fn = getattr(modelo, "predict", None)
    sklearn_model = getattr(modelo, "_model_impl", None)
    raw_model = getattr(sklearn_model, "sklearn_model", None)

    if raw_model is not None and hasattr(raw_model, "predict_proba"):
        probabilidades = raw_model.predict_proba(dados)
        classes = list(raw_model.classes_)
        if 1 in classes:
            return float(probabilidades[0][classes.index(1)])

    if predict_fn is None:
        return None

    return None


def montar_justificativa(classe: str, probabilidade: float | None, preco: float) -> str:
    if probabilidade is None:
        return f"Modelo classificou como {classe} considerando texto, categoria, loja e preco."

    return (
        f"Modelo estimou probabilidade de {probabilidade:.2%} para vale_comprar "
        f"considerando oferta de R$ {preco:.2f}."
    )


@app.on_event("startup")
def mostrar_rotas_disponiveis():
    print("")
    print("GlowWise ML API pronta.")
    print("Rotas uteis:")
    print("  GET  http://127.0.0.1:8000/saude")
    print("  POST http://127.0.0.1:8000/predict")
    print("  DOCS http://127.0.0.1:8000/docs")
    print("")


@app.get("/saude")
def saude():
    return {
        "status": "ok",
        "modelo": MODEL_NAME,
        "model_uri": MODEL_URI,
        "tracking_uri": MLFLOW_TRACKING_URI,
    }


@app.post("/predict", response_model=PredicaoSaida)
def predict(produto: ProdutoEntrada):
    modelo = carregar_modelo()
    dados = montar_dataframe(produto)
    predicao = modelo.predict(dados)
    classe_numerica = int(predicao[0])
    classe = "vale_comprar" if classe_numerica == 1 else "nao_vale_comprar"
    probabilidade = obter_probabilidade(modelo, dados)

    return PredicaoSaida(
        classe=classe,
        vale_comprar=classe_numerica == 1,
        probabilidade_vale_comprar=probabilidade,
        modelo=MODEL_NAME,
        justificativa=montar_justificativa(classe, probabilidade, produto.preco),
    )

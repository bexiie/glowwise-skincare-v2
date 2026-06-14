"""
Gera graficos e tabelas de avaliacao dos modelos GlowWise.

Saidas:
- reports/modelo/metricas_modelos.csv
- reports/modelo/matriz_confusao_<modelo>.png
- reports/modelo/roc_modelos.png
- reports/modelo/pr_modelos.png
- reports/modelo/curva_treino_validacao_<modelo>.png
- reports/modelo/comparacao_metricas.png
- reports/modelo/resumo_modelo.md
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    average_precision_score,
    confusion_matrix,
    log_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split

from treinar_modelo import FEATURES, TARGET, criar_pipeline, modelos_candidatos


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATASET_ML_PATH = PROJECT_DIR / "data" / "processed" / "produtos_ml.csv"
REPORTS_DIR = PROJECT_DIR / "reports" / "modelo"


def obter_scores_positivos(modelo, x_test):
    """Retorna score/probabilidade para a classe positiva."""
    if hasattr(modelo, "predict_proba"):
        return modelo.predict_proba(x_test)[:, 1]
    if hasattr(modelo, "decision_function"):
        return modelo.decision_function(x_test)
    return modelo.predict(x_test)


def avaliar_modelos():
    df = pd.read_csv(DATASET_ML_PATH)
    x = df[FEATURES]
    y = df[TARGET]

    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=42,
        stratify=y,
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    resultados = []
    predicoes_por_modelo = {}
    scores_por_modelo = {}

    for nome, estimador in modelos_candidatos().items():
        modelo = criar_pipeline(estimador)
        modelo.fit(x_train, y_train)

        predicoes = modelo.predict(x_test)
        scores = obter_scores_positivos(modelo, x_test)
        auc = roc_auc_score(y_test, scores)
        auc_pr = average_precision_score(y_test, scores)

        matriz = confusion_matrix(y_test, predicoes, labels=[0, 1])
        tn, fp, fn, tp = matriz.ravel()

        accuracy = (tp + tn) / matriz.sum()
        precision = tp / (tp + fp) if (tp + fp) else 0
        recall = tp / (tp + fn) if (tp + fn) else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

        resultados.append(
            {
                "modelo": nome,
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "auc": auc,
                "auc_pr": auc_pr,
                "tn": int(tn),
                "fp": int(fp),
                "fn": int(fn),
                "tp": int(tp),
            }
        )
        predicoes_por_modelo[nome] = predicoes
        scores_por_modelo[nome] = scores

    df_metricas = pd.DataFrame(resultados).sort_values(["f1", "auc"], ascending=False)
    df_metricas.to_csv(REPORTS_DIR / "metricas_modelos.csv", index=False, encoding="utf-8-sig")

    melhor_modelo = df_metricas.iloc[0]["modelo"]

    gerar_grafico_metricas(df_metricas)
    gerar_roc_modelos(y_test, scores_por_modelo)
    gerar_pr_modelos(y_test, scores_por_modelo)
    gerar_curva_loss_treino_validacao(x_train, x_test, y_train, y_test)
    gerar_matrizes_confusao(y_test, predicoes_por_modelo)
    gerar_resumo(df, y_train, y_test, df_metricas, melhor_modelo)

    print(f"Relatório de avaliação salvo em: {REPORTS_DIR}")
    print(df_metricas.to_string(index=False))


def gerar_grafico_metricas(df_metricas: pd.DataFrame) -> None:
    colunas = ["accuracy", "precision", "recall", "f1", "auc", "auc_pr"]
    ax = df_metricas.set_index("modelo")[colunas].plot(kind="bar", figsize=(11, 6))
    ax.set_title("Comparação das métricas por modelo")
    ax.set_xlabel("Modelo")
    ax.set_ylabel("Valor")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    ax.grid(axis="y", alpha=0.25)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "comparacao_metricas.png", dpi=160)
    plt.close()


def gerar_roc_modelos(y_test, scores_por_modelo: dict[str, object]) -> None:
    plt.figure(figsize=(8, 6))

    for nome, scores in scores_por_modelo.items():
        fpr, tpr, _ = roc_curve(y_test, scores)
        auc = roc_auc_score(y_test, scores)
        plt.plot(fpr, tpr, linewidth=2, label=f"{nome} (AUC={auc:.3f})")

    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Aleatorio")
    plt.title("Curva ROC dos modelos")
    plt.xlabel("Taxa de falso positivo")
    plt.ylabel("Taxa de verdadeiro positivo")
    plt.xlim(0, 1)
    plt.ylim(0, 1.05)
    plt.grid(alpha=0.25)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "roc_modelos.png", dpi=160)
    plt.close()


def gerar_pr_modelos(y_test, scores_por_modelo: dict[str, object]) -> None:
    plt.figure(figsize=(8, 6))

    for nome, scores in scores_por_modelo.items():
        precision, recall, _ = precision_recall_curve(y_test, scores)
        auc_pr = average_precision_score(y_test, scores)
        plt.plot(recall, precision, linewidth=2, label=f"{nome} (AUC-PR={auc_pr:.3f})")

    taxa_base = y_test.mean()
    plt.plot([0, 1], [taxa_base, taxa_base], linestyle="--", color="gray", label="Taxa base")
    plt.title("Curva Precision-Recall dos modelos")
    plt.xlabel("Recall")
    plt.ylabel("Precision")
    plt.xlim(0, 1)
    plt.ylim(0, 1.05)
    plt.grid(alpha=0.25)
    plt.legend(loc="lower left")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "pr_modelos.png", dpi=160)
    plt.close()


def gerar_curva_loss_treino_validacao(x_train, x_test, y_train, y_test) -> None:
    modelo_nome = "gradient_boosting"
    modelo = criar_pipeline(modelos_candidatos()[modelo_nome])
    modelo.fit(x_train, y_train)

    preprocessador = modelo.named_steps["preprocessador"]
    classificador = modelo.named_steps["modelo"]
    x_train_processado = preprocessador.transform(x_train)
    x_test_processado = preprocessador.transform(x_test)

    loss_treino = [
        log_loss(y_train, probabilidades, labels=[0, 1])
        for probabilidades in classificador.staged_predict_proba(x_train_processado)
    ]
    loss_validacao = [
        log_loss(y_test, probabilidades, labels=[0, 1])
        for probabilidades in classificador.staged_predict_proba(x_test_processado)
    ]
    epocas = range(1, len(loss_treino) + 1)

    plt.figure(figsize=(8, 6))
    plt.plot(epocas, loss_treino, linewidth=2, label="Treino")
    plt.plot(epocas, loss_validacao, linewidth=2, label="Validacao")
    plt.title("Curva de treino e validacao - Gradient Boosting")
    plt.xlabel("Epocas / iteracoes")
    plt.ylabel("Log loss")
    plt.grid(alpha=0.25)
    plt.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "curva_loss_treino_validacao_gradient_boosting.png", dpi=160)
    plt.savefig(REPORTS_DIR / "curva_treino_validacao_random_forest.png", dpi=160)
    plt.close()


def gerar_matrizes_confusao(y_test, predicoes_por_modelo: dict[str, object]) -> None:
    for nome, predicoes in predicoes_por_modelo.items():
        matriz = confusion_matrix(y_test, predicoes, labels=[0, 1])
        display = ConfusionMatrixDisplay(
            confusion_matrix=matriz,
            display_labels=["nao_vale_comprar", "vale_comprar"],
        )
        fig, ax = plt.subplots(figsize=(6, 5))
        display.plot(ax=ax, cmap="Blues", colorbar=False, values_format="d")
        ax.set_title(f"Matriz de confusao - {nome}")
        plt.tight_layout()
        plt.savefig(REPORTS_DIR / f"matriz_confusao_{nome}.png", dpi=160)
        plt.close(fig)


def gerar_resumo(df, y_train, y_test, df_metricas: pd.DataFrame, melhor_modelo: str) -> None:
    resumo = [
        "# Avaliação do Modelo GlowWise",
        "",
        f"Total de registros no dataset ML: {len(df)}",
        f"Registros de treino: {len(y_train)}",
        f"Registros de teste: {len(y_test)}",
        "",
        "## Distribuição do alvo",
        "",
        df[TARGET].value_counts().rename(index={0: "nao_vale_comprar", 1: "vale_comprar"}).to_string(),
        "",
        "## Métricas",
        "",
        df_metricas.to_markdown(index=False, floatfmt=".3f"),
        "",
        f"Modelo vencedor pelo critério F1-score: **{melhor_modelo}**",
        "",
        "Observação: as métricas validam o desempenho inicial da pipeline. Como a base ainda é pequena, os resultados devem ser reavaliados conforme novas coletas forem adicionadas.",
    ]
    (REPORTS_DIR / "resumo_modelo.md").write_text("\n".join(resumo), encoding="utf-8")


if __name__ == "__main__":
    avaliar_modelos()

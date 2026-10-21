
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------
# Project paths
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SOURCE_PATH = (
    PROJECT_ROOT
    / "data"
    / "source"
    / "Evaluaciones_Agropecuarias_Municipales_–_EVA._2019_-_2025._Base_Agrícola_20260905.csv"
)

RAW_PATH = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "eva_agricola_raw.csv"
)


# ---------------------------------------------------------
# Extract EVA dataset
# ---------------------------------------------------------

def ejecutar_extraccion() -> pd.DataFrame:
    """
    Read the original EVA dataset and save an untransformed
    copy in the RAW layer.

    Returns:
        pd.DataFrame: Data extracted from the source file.
    """

    print("Iniciando extracción del dataset EVA...")

    if not SOURCE_PATH.is_file():
        raise FileNotFoundError(
            f"No se encontró el archivo fuente de EVA: {SOURCE_PATH}"
        )

    df = pd.read_csv(
        SOURCE_PATH,
        sep=",",
        encoding="utf-8",
    )

    if df.empty:
        raise ValueError(
            "El archivo fuente de EVA está vacío. "
            "Se cancela la extracción."
        )

    # Create the RAW directory if it does not exist.
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Persist the extracted data without applying transformations.
    df.to_csv(
        RAW_PATH,
        index=False,
        encoding="utf-8",
    )


    print(f"Registros extraídos: {len(df):,}")
    print(f"Columnas extraídas: {len(df.columns)}")
    print(f"Archivo fuente: {SOURCE_PATH}")
    print(f"Archivo RAW generado: {RAW_PATH}")

    return df

if __name__ == "__main__":
    ejecutar_extraccion()
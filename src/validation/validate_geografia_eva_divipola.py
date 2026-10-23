
from pathlib import Path
import logging

import pandas as pd


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ARCHIVO_EVA = (
    PROJECT_ROOT / "data" / "processed" / "eva_agricola_processed.csv"
)

ARCHIVO_DIVIPOLA = (
    PROJECT_ROOT / "data" / "source" / "divipola.csv"
)

CARPETA_REPORTE = (
    PROJECT_ROOT / "reports" / "validation" / "geografia_eva_divipola"
)

COLUMNAS_DIVIPOLA = {
    "Código Departamento": "codigo_departamento",
    "Nombre Departamento": "departamento",
    "Código Municipio": "codigo_municipio",
    "Nombre Municipio": "municipio",
    "longitud": "longitud",
    "Latitud": "latitud",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# NORMALIZACIÓN
# ============================================================

def normalizar_codigo(serie, longitud):
    """
    Normaliza códigos como texto y preserva los ceros iniciales.
    No convierte códigos a enteros.
    """
    resultado = serie.astype("string").str.strip()

    # Corregir códigos que accidentalmente lleguen como 5001.0.
    resultado = resultado.str.replace(
        r"\.0$", "", regex=True
    )

    # Completar códigos numéricos con ceros a la izquierda.
    numericos = resultado.str.fullmatch(r"\d+").fillna(False)

    resultado.loc[numericos] = (
        resultado.loc[numericos].str.zfill(longitud)
    )

    return resultado


def convertir_decimal(serie):
    """
    Convierte coordenadas con coma decimal a valores numéricos.
    Ejemplo: '-75,581775' -> -75.581775.
    """
    texto = (
        serie.astype("string")
        .str.strip()
        .str.replace(",", ".", regex=False)
    )

    texto = texto.replace("", pd.NA)

    return pd.to_numeric(texto, errors="coerce")


def guardar_reporte(nombre, dataframe):
    ruta = CARPETA_REPORTE / nombre
    dataframe.to_csv(ruta, index=False, encoding="utf-8-sig")
    logger.info("Reporte generado: %s", ruta)


# ============================================================
# LECTURA
# ============================================================

def leer_archivos():
    if not ARCHIVO_EVA.exists():
        raise FileNotFoundError(
            f"No existe el archivo EVA: {ARCHIVO_EVA}"
        )

    if not ARCHIVO_DIVIPOLA.exists():
        raise FileNotFoundError(
            f"No existe el archivo DIVIPOLA: {ARCHIVO_DIVIPOLA}"
        )

    eva = pd.read_csv(
        ARCHIVO_EVA,
        dtype="string",
        keep_default_na=False,
    )

    divipola = pd.read_csv(
        ARCHIVO_DIVIPOLA,
        dtype="string",
        keep_default_na=False,
    )

    requeridas_eva = {"codigo_municipio"}
    faltantes_eva = requeridas_eva - set(eva.columns)

    if faltantes_eva:
        raise ValueError(
            f"Faltan columnas en EVA: {sorted(faltantes_eva)}"
        )

    faltantes_divipola = (
        set(COLUMNAS_DIVIPOLA) - set(divipola.columns)
    )

    if faltantes_divipola:
        raise ValueError(
            "Faltan columnas en DIVIPOLA: "
            f"{sorted(faltantes_divipola)}"
        )

    divipola = divipola.rename(columns=COLUMNAS_DIVIPOLA)

    eva["codigo_municipio"] = normalizar_codigo(
        eva["codigo_municipio"], 5
    )

    divipola["codigo_municipio"] = normalizar_codigo(
        divipola["codigo_municipio"], 5
    )

    divipola["codigo_departamento"] = normalizar_codigo(
        divipola["codigo_departamento"], 2
    )

    for columna in ["latitud", "longitud"]:
        divipola[columna] = convertir_decimal(divipola[columna])

    logger.info("Registros EVA: %s", f"{len(eva):,}")
    logger.info("Registros DIVIPOLA: %s", f"{len(divipola):,}")

    return eva, divipola


# ============================================================
# VALIDACIÓN
# ============================================================

def validar():
    CARPETA_REPORTE.mkdir(parents=True, exist_ok=True)

    eva, divipola = leer_archivos()

    # Códigos válidos: cinco dígitos numéricos.
    codigos_eva_validos = eva["codigo_municipio"].str.fullmatch(
        r"\d{5}"
    ).fillna(False)

    codigos_divipola_validos = (
        divipola["codigo_municipio"]
        .str.fullmatch(r"\d{5}")
        .fillna(False)
    )

    eva_invalidos = eva.loc[
        ~codigos_eva_validos, ["codigo_municipio"]
    ].drop_duplicates()

    divipola_invalidos = divipola.loc[
        ~codigos_divipola_validos
    ].copy()

    eva_codigos = set(
        eva.loc[codigos_eva_validos, "codigo_municipio"].unique()
    )

    divipola_codigos = set(
        divipola.loc[
            codigos_divipola_validos, "codigo_municipio"
        ].unique()
    )

    # Comparación de códigos.
    codigos_eva_sin_divipola = sorted(
        eva_codigos - divipola_codigos
    )

    codigos_divipola_sin_eva = sorted(
        divipola_codigos - eva_codigos
    )

    coincidencias = sorted(eva_codigos & divipola_codigos)

    # Duplicados de código municipal en DIVIPOLA.
    duplicados = divipola.loc[
        divipola["codigo_municipio"].isin(
            divipola.loc[
                divipola["codigo_municipio"].duplicated(
                    keep=False
                ),
                "codigo_municipio",
            ]
        )
    ].sort_values("codigo_municipio")

    # Atributos de DIVIPOLA para municipios que necesita EVA.
    catalogo_eva = divipola.loc[
        divipola["codigo_municipio"].isin(eva_codigos)
    ].copy()

    columnas_obligatorias = [
        "codigo_departamento",
        "departamento",
        "codigo_municipio",
        "municipio",
        "latitud",
        "longitud",
    ]

    # Normalizar atributos de texto sin alterar los tipos numéricos.
    columnas_texto = [
        "codigo_departamento",
        "departamento",
        "codigo_municipio",
        "municipio",
    ]

    for columna in columnas_texto:
        catalogo_eva[columna] = (
            catalogo_eva[columna]
            .astype("string")
            .str.strip()
            .replace("", pd.NA)
        )

    # Asegurar que las coordenadas sigan siendo numéricas.
    for columna in ["latitud", "longitud"]:
        catalogo_eva[columna] = pd.to_numeric(
            catalogo_eva[columna],
            errors="coerce",
        )

    faltantes_atributos = catalogo_eva.loc[
        catalogo_eva[columnas_obligatorias].isna().any(axis=1)
    ].copy()

    # Coordenadas no convertibles o fuera de rango.
    coordenadas_invalidas = catalogo_eva.loc[
        catalogo_eva["latitud"].isna()
        | catalogo_eva["longitud"].isna()
        | ~catalogo_eva["latitud"].between(-90, 90)
        | ~catalogo_eva["longitud"].between(-180, 180)
    ].copy()

    # Informes detallados.
    guardar_reporte(
        "eva_sin_coincidencia_divipola.csv",
        pd.DataFrame({
            "codigo_municipio": codigos_eva_sin_divipola
        }),
    )

    guardar_reporte(
        "divipola_sin_coincidencia_eva.csv",
        divipola.loc[
            divipola["codigo_municipio"].isin(
                codigos_divipola_sin_eva
            )
        ].copy(),
    )

    guardar_reporte(
        "duplicados_divipola.csv",
        duplicados,
    )

    guardar_reporte(
        "codigos_invalidos_eva.csv",
        eva_invalidos,
    )

    guardar_reporte(
        "codigos_invalidos_divipola.csv",
        divipola_invalidos,
    )

    guardar_reporte(
        "atributos_faltantes_municipios_eva.csv",
        faltantes_atributos,
    )

    guardar_reporte(
        "coordenadas_invalidas_municipios_eva.csv",
        coordenadas_invalidas,
    )

    # Resumen general.
    resumen = [
        ("EVA", "registros_totales", len(eva)),
        ("EVA", "codigos_municipales_distintos_validos", len(eva_codigos)),
        ("EVA", "registros_con_codigo_invalido", len(eva_invalidos)),
        ("DIVIPOLA", "registros_totales", len(divipola)),
        ("DIVIPOLA", "codigos_municipales_distintos_validos", len(divipola_codigos)),
        ("DIVIPOLA", "registros_con_codigo_invalido", len(divipola_invalidos)),
        ("CRUCE", "codigos_coincidentes", len(coincidencias)),
        ("CRUCE", "codigos_eva_ausentes_en_divipola", len(codigos_eva_sin_divipola)),
        ("CRUCE", "codigos_divipola_ausentes_en_eva", len(codigos_divipola_sin_eva)),
        ("DIVIPOLA", "codigos_duplicados", duplicados["codigo_municipio"].nunique()),
        ("DIVIPOLA", "municipios_eva_con_atributos_faltantes", len(faltantes_atributos)),
        ("DIVIPOLA", "municipios_eva_con_coordenadas_invalidas", len(coordenadas_invalidas)),
    ]

    resumen_df = pd.DataFrame(
        resumen, columns=["categoria", "validacion", "cantidad"]
    )

    guardar_reporte(
        "resumen_validacion_geografia.csv",
        resumen_df,
    )

    logger.info("=" * 60)
    logger.info("RESUMEN DE VALIDACIÓN GEOGRÁFICA")
    logger.info("=" * 60)

    for categoria, validacion, cantidad in resumen:
        logger.info(
            "%s | %s: %s",
            categoria,
            validacion,
            f"{cantidad:,}",
        )

    logger.info("Validación terminada.")
    logger.info("Reportes en: %s", CARPETA_REPORTE)


if __name__ == "__main__":
    validar()

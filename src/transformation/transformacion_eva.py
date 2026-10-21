
import logging
from pathlib import Path

import pandas as pd


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "source"
    / "Evaluaciones_Agropecuarias_Municipales_–_EVA._2019_-_2025._Base_Agrícola_20260905.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "eva_agricola_processed.csv"
)

REGISTROS_ESPERADOS = 166732

COLUMNAS_ORIGEN = {
    "Código Dane departamento": "codigo_departamento",
    "Departamento": "departamento",
    "Código Dane municipio": "codigo_municipio",
    "Municipio": "municipio",
    "Grupo cultivo": "grupo_cultivo",
    "Subgrupo": "subgrupo",
    "Cultivo": "cultivo",
    "Desagregación cultivo": "desagregacion_cultivo",
    "Año": "anio",
    "Periodo": "periodo",
    "Área sembrada": "area_sembrada",
    "Área cosechada": "area_cosechada",
    "Producción": "produccion",
    "Rendimiento": "rendimiento",
    "Ciclo del cultivo": "ciclo_cultivo",
    "Estado físico del cultivo": "estado_fisico_cultivo",
    "Código del cultivo": "codigo_cultivo",
    "Nombre científico del cultivo": "nombre_cientifico",
}

COLUMNAS_TEXTO = [
    "departamento",
    "municipio",
    "grupo_cultivo",
    "subgrupo",
    "cultivo",
    "desagregacion_cultivo",
    "periodo",
    "ciclo_cultivo",
    "estado_fisico_cultivo",
    "nombre_cientifico",
]

COLUMNAS_METRICAS = [
    "area_sembrada",
    "area_cosechada",
    "produccion",
    "rendimiento",
]

COLUMNAS_CLAVE = [
    "codigo_municipio",
    "codigo_cultivo",
    "anio",
    "periodo",
]

COLUMNAS_FINALES = list(COLUMNAS_ORIGEN.values())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("transform_eva")


# ============================================================
# 2. FUNCIONES DE TRANSFORMACIÓN
# ============================================================

def limpiar_texto(serie):
    """
    Elimina espacios externos sin convertir nulos en cadenas
    como 'nan' o '<NA>'.
    """
    return serie.astype("string").str.strip()


def convertir_numero_eva(serie):
    """
    Convierte números con formato decimal colombiano.

    Ejemplos:
        '128,00'   -> 128.00
        '9.217,00' -> 9217.00
        '0,15'     -> 0.15
        '125.50'   -> 125.50

    Los valores vacíos se conservan como nulos.
    Los valores no interpretables se convierten en NA y se
    contabilizan posteriormente para informar la incidencia.
    """
    texto = limpiar_texto(serie)

    texto = texto.replace("", pd.NA)

    tiene_coma = texto.str.contains(",", regex=False, na=False)

    resultado = texto.copy()

    # Convención colombiana: punto de miles y coma decimal.
    resultado.loc[tiene_coma] = (
        resultado.loc[tiene_coma]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )

    return pd.to_numeric(resultado, errors="coerce")


def normalizar_codigo(serie, longitud=None):
    """
    Conserva los códigos como texto para preservar ceros iniciales.

    Si el origen contiene códigos enteros representados como '5.0',
    elimina únicamente el sufijo decimal .0.

    No convierte códigos a números ni altera códigos alfanuméricos.
    """
    codigo = limpiar_texto(serie)

    codigo = codigo.str.replace(
        r"^(\d+)\.0+$",
        r"\1",
        regex=True,
    )

    es_numerico = codigo.str.fullmatch(r"\d+", na=False)

    if longitud is not None:
        codigo.loc[es_numerico] = (
            codigo.loc[es_numerico].str.zfill(longitud)
        )

    return codigo


# ============================================================
# 3. TRANSFORMACIÓN
# ============================================================

def ejecutar_transformacion():

    print("=" * 70)
    print("TRANSFORMACIÓN DEL DATASET EVA")
    print("=" * 70)

    print(f"\nOrigen:  {INPUT_FILE}")
    print(f"Destino: {OUTPUT_FILE}")

    # --------------------------------------------------------
    # 1. Verificar archivo y leer datos
    # --------------------------------------------------------

    logger.info("Leyendo archivo original.")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"No existe el archivo de entrada: {INPUT_FILE}"
        )

    df = pd.read_csv(
        INPUT_FILE,
        sep=",",
        encoding="utf-8-sig",
        dtype="string",
        low_memory=False,
    )

    registros_entrada = len(df)

    logger.info("Registros de entrada: %s", f"{registros_entrada:,}")
    logger.info("Columnas de entrada: %s", len(df.columns))

    # --------------------------------------------------------
    # 2. Validar estructura de origen
    # --------------------------------------------------------

    columnas_faltantes = [
        columna
        for columna in COLUMNAS_ORIGEN
        if columna not in df.columns
    ]

    if columnas_faltantes:
        raise ValueError(
            "El archivo de origen no cumple el esquema esperado. "
            f"Columnas faltantes: {columnas_faltantes}"
        )

    df = df.rename(columns=COLUMNAS_ORIGEN)

    # --------------------------------------------------------
    # 3. Normalizar identificadores
    # --------------------------------------------------------

    logger.info("Normalizando identificadores.")

    df["codigo_departamento"] = normalizar_codigo(
        df["codigo_departamento"],
        longitud=2,
    )

    df["codigo_municipio"] = normalizar_codigo(
        df["codigo_municipio"],
        longitud=5,
    )

    df["codigo_cultivo"] = normalizar_codigo(
        df["codigo_cultivo"]
    )

    # --------------------------------------------------------
    # 4. Estandarizar textos
    # --------------------------------------------------------

    logger.info("Estandarizando campos de texto.")

    for columna in COLUMNAS_TEXTO:
        df[columna] = limpiar_texto(df[columna])

    # --------------------------------------------------------
    # 5. Convertir métricas y año
    # --------------------------------------------------------

    logger.info("Convirtiendo métricas numéricas.")

    # Distinguir valores originalmente vacíos de valores
    # no vacíos que fallan durante la conversión.
    errores_conversion = {}

    for columna in COLUMNAS_METRICAS:
        original = limpiar_texto(df[columna])

        vacios_originales = original.isna() | original.eq("")

        convertido = convertir_numero_eva(original)

        conversion_fallida = (
            ~vacios_originales
            & convertido.isna()
        )

        errores_conversion[columna] = int(
            conversion_fallida.sum()
        )

        df[columna] = convertido

    anio_original = limpiar_texto(df["anio"])

    anio_numerico = pd.to_numeric(
        anio_original,
        errors="coerce",
    )

    anio_vacio = anio_original.isna() | anio_original.eq("")

    anio_invalido = (
        ~anio_vacio
        & (
            anio_numerico.isna()
            | (anio_numerico % 1 != 0)
        )
    )

    logger.info(
        "Años no convertibles a enteros: %s",
        f"{int(anio_invalido.sum()):,}",
    )

    df["anio"] = anio_numerico.where(
        anio_numerico % 1 == 0
    ).astype("Int64")

    # --------------------------------------------------------
    # 6. Ordenar columnas y comprobar volumen
    # --------------------------------------------------------

    df = df[COLUMNAS_FINALES]

    if len(df) != registros_entrada:
        raise RuntimeError(
            "La transformación modificó inesperadamente "
            "el número de registros."
        )

    logger.info(
        "Registros de entrada y salida coinciden: %s",
        f"{len(df):,}",
    )

    if registros_entrada != REGISTROS_ESPERADOS:
        logger.warning(
            "Volumen diferente de la referencia: esperados %s, "
            "encontrados %s.",
            f"{REGISTROS_ESPERADOS:,}",
            f"{registros_entrada:,}",
        )

    # --------------------------------------------------------
    # 7. Reportar incidencias de conversión
    # --------------------------------------------------------

    print("\n--- Conversión de métricas ---")

    for columna, cantidad in errores_conversion.items():
        print(f"{columna:<20}: {cantidad:,} conversiones fallidas")

        if cantidad > 0:
            logger.warning(
                "%s contiene %s valores no convertibles.",
                columna,
                f"{cantidad:,}",
            )

    # --------------------------------------------------------
    # 8. Validar códigos
    # --------------------------------------------------------

    print("\n--- Validación estructural de códigos ---")

    departamentos_invalidos = int(
        (
            ~df["codigo_departamento"].str.fullmatch(
                r"\d{2}", na=False
            )
        ).sum()
    )

    municipios_invalidos = int(
        (
            ~df["codigo_municipio"].str.fullmatch(
                r"\d{5}", na=False
            )
        ).sum()
    )

    print(f"Departamentos inválidos: {departamentos_invalidos:,}")
    print(f"Municipios inválidos:    {municipios_invalidos:,}")

    if departamentos_invalidos or municipios_invalidos:
        logger.warning(
            "Se encontraron códigos geográficos con formato inválido."
        )

    # --------------------------------------------------------
    # 9. Consistencia temporal
    # --------------------------------------------------------

    print("\n--- Consistencia año / periodo ---")

    anio_periodo = pd.to_numeric(
        df["periodo"].str.extract(r"^(\d{4})", expand=False),
        errors="coerce",
    )

    inconsistencia_temporal = (
        df["anio"].isna()
        | anio_periodo.isna()
        | (df["anio"] != anio_periodo)
    )

    cantidad_temporal = int(inconsistencia_temporal.sum())

    print(f"Inconsistencias temporales: {cantidad_temporal:,}")

    if cantidad_temporal:
        logger.warning(
            "Se encontraron %s inconsistencias temporales.",
            f"{cantidad_temporal:,}",
        )

    # --------------------------------------------------------
    # 10. Granularidad
    # --------------------------------------------------------

    print("\n--- Granularidad ---")

    claves = df[COLUMNAS_CLAVE]

    clave_incompleta = (
        claves.isna().any(axis=1)
        | claves.apply(
            lambda col: col.astype("string").str.strip().eq("")
        ).any(axis=1)
    )

    duplicados_excedentes = int(
        claves.duplicated(keep="first").sum()
    )

    filas_en_duplicados = int(
        claves.duplicated(keep=False).sum()
    )

    claves_unicas = int(
        claves.drop_duplicates().shape[0]
    )

    print(f"Registros:                    {len(df):,}")
    print(f"Claves únicas:                {claves_unicas:,}")
    print(f"Claves duplicadas excedentes: {duplicados_excedentes:,}")
    print(f"Filas en grupos duplicados:   {filas_en_duplicados:,}")
    print(f"Claves incompletas:           {int(clave_incompleta.sum()):,}")

    if duplicados_excedentes or clave_incompleta.any():
        logger.warning(
            "Revisar duplicados o claves incompletas. "
            "No se eliminaron registros."
        )

    # --------------------------------------------------------
    # 11. Valores negativos
    # --------------------------------------------------------

    print("\n--- Valores negativos ---")

    for columna in COLUMNAS_METRICAS:
        negativos = int((df[columna] < 0).sum())

        print(f"{columna:<20}: {negativos:,}")

        if negativos:
            logger.warning(
                "%s contiene %s valores negativos.",
                columna,
                f"{negativos:,}",
            )

    # --------------------------------------------------------
    # 12. Condiciones semánticas informativas
    # --------------------------------------------------------

    print("\n--- Condiciones semánticas informativas ---")

    cosechada_mayor = int(
        (
            df["area_cosechada"]
            > df["area_sembrada"]
        ).sum()
    )

    sembrada_cero_cosechada_mayor = int(
        (
            (df["area_sembrada"] == 0)
            & (df["area_cosechada"] > 0)
        ).sum()
    )

    print(
        "Área cosechada > área sembrada: "
        f"{cosechada_mayor:,}"
    )

    print(
        "Área sembrada = 0 y cosechada > 0: "
        f"{sembrada_cero_cosechada_mayor:,}"
    )

    # Se conservan deliberadamente ambos tipos de registros.
    # No se aplican imputaciones ni eliminaciones automáticas.

    # --------------------------------------------------------
    # 13. Exportar datos transformados
    # --------------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    logger.info("Archivo generado: %s", OUTPUT_FILE)
    logger.info("Registros exportados: %s", f"{len(df):,}")

    print("\n" + "=" * 70)
    print("TRANSFORMACIÓN FINALIZADA")
    print("=" * 70)
    print(f"Archivo generado: {OUTPUT_FILE}")
    print(f"Registros: {len(df):,}")
    print(f"Columnas: {len(df.columns)}")

    return df


if __name__ == "__main__":
    ejecutar_transformacion()

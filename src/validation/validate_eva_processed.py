
from pathlib import Path
import logging
import sys

import numpy as np
import pandas as pd
import great_expectations as gx


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ARCHIVO_PROCESADO = (
    PROJECT_ROOT / "data" / "processed" / "eva_agricola_processed.csv"
)

CARPETA_SOURCE = PROJECT_ROOT / "data" / "source"
CARPETA_VALIDACION = PROJECT_ROOT / "data" / "validation"

ARCHIVO_REPORTE = (
    CARPETA_VALIDACION / "validacion_eva_processed.csv"
)

REGISTROS_ESPERADOS = 166732

ANIO_MINIMO = 2019
ANIO_MAXIMO = 2025

COLUMNAS_ESPERADAS = [
    "codigo_departamento",
    "departamento",
    "codigo_municipio",
    "municipio",
    "grupo_cultivo",
    "subgrupo",
    "cultivo",
    "desagregacion_cultivo",
    "anio",
    "periodo",
    "area_sembrada",
    "area_cosechada",
    "produccion",
    "rendimiento",
    "ciclo_cultivo",
    "estado_fisico_cultivo",
    "codigo_cultivo",
    "nombre_cientifico",
]

COLUMNAS_TEXTO = [
    "codigo_departamento",
    "departamento",
    "codigo_municipio",
    "municipio",
    "grupo_cultivo",
    "subgrupo",
    "cultivo",
    "desagregacion_cultivo",
    "periodo",
    "ciclo_cultivo",
    "estado_fisico_cultivo",
    "codigo_cultivo",
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

# Campos obligatorios de acuerdo con la política acordada.
# Las métricas y las dimensiones descriptivas restantes pueden
# tener nulos si el contrato de origen lo permite.
COLUMNAS_OBLIGATORIAS = [
    "codigo_departamento",
    "codigo_municipio",
    "departamento",
    "municipio",
    "cultivo",
    "codigo_cultivo",
    "anio",
    "periodo",
]

PERIODOS_VALIDOS = {
    str(anio)
    for anio in range(ANIO_MINIMO, ANIO_MAXIMO + 1)
}

PERIODOS_VALIDOS.update(
    f"{anio}{semestre}"
    for anio in range(ANIO_MINIMO, ANIO_MAXIMO + 1)
    for semestre in ("A", "B")
)


# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# 3. REPORTE DE VALIDACIONES
# ============================================================

resultados = []


def registrar(
    categoria,
    validacion,
    estado,
    riesgo,
    detalle,
    accion,
):
    """Registra el resultado de una validación."""

    resultados.append(
        {
            "categoria": categoria,
            "validacion": validacion,
            "estado": estado,
            "riesgo": riesgo,
            "detalle": str(detalle),
            "accion_recomendada": accion,
        }
    )

    nivel_log = {
        "ERROR": logging.ERROR,
        "ADVERTENCIA": logging.WARNING,
        "OK": logging.INFO,
        "INFORMATIVO": logging.INFO,
    }.get(estado, logging.INFO)

    logger.log(
        nivel_log,
        "[%s] %s | %s",
        estado,
        validacion,
        detalle,
    )


def finalizar(estado_final):
    """Guarda el reporte y devuelve el código de salida."""

    CARPETA_VALIDACION.mkdir(
        parents=True,
        exist_ok=True,
    )

    reporte = pd.DataFrame(resultados)

    reporte.to_csv(
        ARCHIVO_REPORTE,
        index=False,
        encoding="utf-8-sig",
    )

    logger.info("Reporte generado: %s", ARCHIVO_REPORTE)
    logger.info("Estado final: %s", estado_final)

    if estado_final == "BLOQUEADO":
        return 1

    if estado_final == "REQUIERE_REVISION":
        return 0

    return 0


# ============================================================
# 4. GREAT EXPECTATIONS 1.24.0
# ============================================================

def ejecutar_validaciones_gx(df):
    """
    Ejecuta expectativas declarativas mediante GX.

    La API utilizada corresponde al enfoque de DataFrame,
    Data Asset, Batch Definition y Expectation Suite.
    """

    try:
        contexto = gx.get_context(mode="ephemeral")

        datasource = contexto.data_sources.add_pandas(
            name="eva_processed_datasource"
        )

        asset = datasource.add_dataframe_asset(
            name="eva_processed_asset"
        )

        batch_definition = (
            asset.add_batch_definition_whole_dataframe(
                "eva_processed_batch"
            )
        )

        batch = batch_definition.get_batch(
            batch_parameters={"dataframe": df}
        )

        suite = gx.ExpectationSuite(
            name="eva_processed_suite"
        )

        # Campos contractualmente obligatorios.
        for columna in COLUMNAS_OBLIGATORIAS:
            if columna in df.columns:
                suite.add_expectation(
                    gx.expectations.ExpectColumnValuesToNotBeNull(
                        column=columna
                    )
                )

        # La suite debe ejecutarse aunque existan otras
        # validaciones manuales que también fallen.
        resultado = batch.validate(suite)

        if resultado.success:
            registrar(
                "GREAT_EXPECTATIONS",
                "EVA-GX-01",
                "OK",
                "BAJO",
                "Todas las expectativas GX fueron satisfechas.",
                "Continuar con las demás validaciones.",
            )
        else:
            registrar(
                "GREAT_EXPECTATIONS",
                "EVA-GX-01",
                "ERROR",
                "CRITICO",
                (
                    "Una o más expectativas GX fallaron. "
                    f"Resultado: {resultado}"
                ),
                "Revisar las expectativas fallidas y los registros afectados.",
            )

    except Exception as exc:
        registrar(
            "GREAT_EXPECTATIONS",
            "EVA-GX-01",
            "ERROR",
            "CRITICO",
            f"No fue posible ejecutar GX: {exc}",
            "Revisar la versión de GX, su configuración y las expectativas.",
        )


# ============================================================
# 5. VALIDACIÓN DE ARCHIVO Y ESQUEMA
# ============================================================

def validar_archivo_y_esquema():
    if not ARCHIVO_PROCESADO.exists():
        registrar(
            "ARCHIVO",
            "EVA-P01",
            "ERROR",
            "CRITICO",
            f"No existe el archivo: {ARCHIVO_PROCESADO}",
            "Ejecutar la transformación de EVA y comprobar la ruta de salida.",
        )
        return None

    try:
        # Los códigos se leen como texto para conservar ceros iniciales.
        df = pd.read_csv(
            ARCHIVO_PROCESADO,
            dtype={
                columna: "string"
                for columna in COLUMNAS_TEXTO
            },
            encoding="utf-8-sig",
            low_memory=False,
        )

    except Exception as exc:
        registrar(
            "ARCHIVO",
            "EVA-P02",
            "ERROR",
            "CRITICO",
            f"No fue posible leer el CSV: {exc}",
            "Revisar la codificación, estructura y contenido del archivo.",
        )
        return None

    if df.empty:
        registrar(
            "VOLUMEN",
            "EVA-P03",
            "ERROR",
            "CRITICO",
            "El archivo procesado no contiene registros.",
            "Revisar la extracción y la transformación.",
        )
        return None

    registrar(
        "ARCHIVO",
        "EVA-P04",
        "OK",
        "BAJO",
        f"Archivo leído correctamente: {len(df)} registros.",
        "Continuar con las validaciones.",
    )

    if list(df.columns) != COLUMNAS_ESPERADAS:
        faltantes = [
            c for c in COLUMNAS_ESPERADAS
            if c not in df.columns
        ]

        adicionales = [
            c for c in df.columns
            if c not in COLUMNAS_ESPERADAS
        ]

        registrar(
            "ESQUEMA",
            "EVA-P05",
            "ERROR",
            "CRITICO",
            (
                f"Esquema inválido. Faltantes: {faltantes}; "
                f"adicionales: {adicionales}; "
                f"orden correcto: {list(df.columns) == COLUMNAS_ESPERADAS}."
            ),
            "Ajustar la transformación al esquema contractual de 18 columnas.",
        )
    else:
        registrar(
            "ESQUEMA",
            "EVA-P05",
            "OK",
            "BAJO",
            "Las 18 columnas existen y respetan el orden contractual.",
            "Continuar con las validaciones.",
        )

    faltantes = [
        c for c in COLUMNAS_ESPERADAS
        if c not in df.columns
    ]

    if faltantes:
        # No se continúa con reglas que requieren columnas inexistentes.
        return df

    return df


# ============================================================
# 6. VOLUMEN Y CONSERVACIÓN DE REGISTROS
# ============================================================

def validar_volumen(df):
    if len(df) != REGISTROS_ESPERADOS:
        registrar(
            "VOLUMEN",
            "EVA-P06",
            "ADVERTENCIA",
            "ALTO",
            (
                f"Se esperaban {REGISTROS_ESPERADOS} registros, "
                f"pero se encontraron {len(df)}."
            ),
            "Comparar el volumen con el origen y revisar posibles cambios de cobertura.",
        )
    else:
        registrar(
            "VOLUMEN",
            "EVA-P06",
            "OK",
            "BAJO",
            f"Volumen coincidente: {REGISTROS_ESPERADOS} registros.",
            "Continuar con las validaciones.",
        )

    # La transformación actual lee data/source. Se busca un candidato
    # de origen para comprobar la conservación del número de filas.
    candidatos = sorted(
        p for p in CARPETA_SOURCE.glob("*.csv")
        if "eva" in p.name.casefold()
        or "evaluaciones_agropecuarias" in p.name.casefold()
    )

    if len(candidatos) != 1:
        registrar(
            "INTEGRIDAD_ETL",
            "EVA-P07",
            "ERROR",
            "CRITICO",
            (
                "No se pudo identificar de forma inequívoca el archivo "
                f"de origen. Candidatos encontrados: {len(candidatos)}."
            ),
            "Ajustar la referencia al archivo exacto utilizado por la transformación.",
        )
        return

    archivo_origen = candidatos[0]

    try:
        df_origen = pd.read_csv(
            archivo_origen,
            encoding="utf-8-sig",
            dtype="string",
            low_memory=False,
        )

        cantidad_origen = len(df_origen)

        if cantidad_origen != len(df):
            registrar(
                "INTEGRIDAD_ETL",
                "EVA-P08",
                "ERROR",
                "CRITICO",
                (
                    f"El origen tiene {cantidad_origen} filas y el procesado "
                    f"tiene {len(df)}."
                ),
                "Investigar la pérdida o incorporación de filas durante la transformación.",
            )
        else:
            registrar(
                "INTEGRIDAD_ETL",
                "EVA-P08",
                "OK",
                "BAJO",
                (
                    f"Se conserva el número de filas: "
                    f"{cantidad_origen}."
                ),
                "Continuar con las validaciones.",
            )

    except Exception as exc:
        registrar(
            "INTEGRIDAD_ETL",
            "EVA-P08",
            "ERROR",
            "CRITICO",
            f"No se pudo comprobar la conservación de filas: {exc}",
            "Verificar el archivo de origen y sus permisos de lectura.",
        )


# ============================================================
# 7. NULOS Y TIPOS
# ============================================================

def validar_nulos_y_tipos(df):
    # Nulos por columna: se informan para monitoreo.
    for columna in COLUMNAS_ESPERADAS:
        cantidad_nulos = int(df[columna].isna().sum())

        cantidad_vacios = 0
        if pd.api.types.is_string_dtype(df[columna].dtype):
            cantidad_vacios = int(
                df[columna].str.strip().eq("").fillna(False).sum()
            )

        total = cantidad_nulos + cantidad_vacios

        if total:
            estado = (
                "ERROR"
                if columna in COLUMNAS_OBLIGATORIAS
                else "ADVERTENCIA"
            )

            registrar(
                "NULABILIDAD",
                f"EVA-P09-{columna}",
                estado,
                "ALTO" if estado == "ERROR" else "MEDIO",
                f"{columna}: {total} valores nulos o vacíos.",
                (
                    "Completar o investigar los campos obligatorios."
                    if estado == "ERROR"
                    else "Revisar si los valores ausentes están permitidos por el contrato."
                ),
            )

    # Año: debe ser un entero dentro del alcance.
    anio_numerico = pd.to_numeric(
        df["anio"],
        errors="coerce",
    )

    anio_invalido = (
        anio_numerico.isna()
        | ~np.isfinite(anio_numerico.fillna(0))
        | (anio_numerico % 1 != 0)
        | (anio_numerico < ANIO_MINIMO)
        | (anio_numerico > ANIO_MAXIMO)
    )

    cantidad_anio_invalido = int(anio_invalido.sum())

    if cantidad_anio_invalido:
        registrar(
            "TIPOS_Y_DOMINIOS",
            "EVA-P10",
            "ERROR",
            "CRITICO",
            (
                f"{cantidad_anio_invalido} filas tienen año inválido, "
                "no entero o fuera de 2019–2025."
            ),
            "Revisar la conversión y el valor original de anio.",
        )
    else:
        registrar(
            "TIPOS_Y_DOMINIOS",
            "EVA-P10",
            "OK",
            "BAJO",
            "Todos los años son enteros entre 2019 y 2025.",
            "Continuar con las validaciones.",
        )

    # Las métricas deben ser numéricas si contienen un valor.
    for columna in COLUMNAS_METRICAS:
        original = df[columna]
        numerico = pd.to_numeric(original, errors="coerce")

        no_vacio = (
            original.notna()
            & original.astype("string").str.strip().ne("")
        )

        invalido = no_vacio & (
            numerico.isna()
            | ~np.isfinite(numerico.fillna(0))
        )

        cantidad_invalidos = int(invalido.sum())

        if cantidad_invalidos:
            registrar(
                "TIPOS_Y_DOMINIOS",
                f"EVA-P11-{columna}",
                "ERROR",
                "CRITICO",
                (
                    f"{cantidad_invalidos} valores no vacíos de "
                    f"{columna} no son numéricos válidos."
                ),
                "Revisar el origen y la lógica de conversión; no sustituir por cero.",
            )
        else:
            registrar(
                "TIPOS_Y_DOMINIOS",
                f"EVA-P11-{columna}",
                "OK",
                "BAJO",
                f"{columna}: valores informados numéricos válidos.",
                "Continuar con las validaciones.",
            )


# ============================================================
# 8. CÓDIGOS TERRITORIALES
# ============================================================

def validar_codigos(df):
    depto = df["codigo_departamento"].astype("string").str.strip()
    municipio = df["codigo_municipio"].astype("string").str.strip()
    cultivo = df["codigo_cultivo"].astype("string").str.strip()

    depto_valido = depto.str.fullmatch(r"\d{2}", na=False)
    municipio_valido = municipio.str.fullmatch(r"\d{5}", na=False)
    cultivo_valido = cultivo.notna() & cultivo.ne("")

    prefijo_valido = municipio.str[:2].eq(depto).fillna(False)

    controles = [
        (
            "EVA-P12",
            "Código de departamento",
            depto_valido,
            "El código debe tener exactamente dos dígitos.",
        ),
        (
            "EVA-P13",
            "Código de municipio",
            municipio_valido,
            "El código debe tener exactamente cinco dígitos.",
        ),
        (
            "EVA-P14",
            "Código de cultivo",
            cultivo_valido,
            "El código de cultivo no debe estar vacío.",
        ),
        (
            "EVA-P15",
            "Coherencia territorial",
            prefijo_valido,
            "Los dos primeros dígitos del municipio deben coincidir con el departamento.",
        ),
    ]

    for codigo, nombre, mascara, detalle in controles:
        cantidad_invalidos = int((~mascara).sum())

        if cantidad_invalidos:
            registrar(
                "CODIGOS",
                codigo,
                "ERROR",
                "CRITICO",
                f"{nombre}: {cantidad_invalidos} registros inválidos. {detalle}",
                "Revisar los códigos originales y la normalización aplicada.",
            )
        else:
            registrar(
                "CODIGOS",
                codigo,
                "OK",
                "BAJO",
                f"{nombre}: todos los registros cumplen la regla.",
                "Continuar con las validaciones.",
            )


# ============================================================
# 9. VALIDACIÓN TEMPORAL
# ============================================================

def validar_periodos(df):
    periodo = df["periodo"].astype("string").str.strip()

    periodo_valido = periodo.isin(PERIODOS_VALIDOS)

    if (~periodo_valido).any():
        ejemplos = (
            periodo.loc[~periodo_valido]
            .drop_duplicates()
            .head(10)
            .tolist()
        )

        registrar(
            "TEMPORALIDAD",
            "EVA-P16",
            "ERROR",
            "CRITICO",
            (
                f"{int((~periodo_valido).sum())} registros tienen "
                f"periodos fuera del dominio. Ejemplos: {ejemplos}"
            ),
            "Aceptar únicamente periodos anuales o semestrales de 2019–2025.",
        )
    else:
        registrar(
            "TEMPORALIDAD",
            "EVA-P16",
            "OK",
            "BAJO",
            "Todos los periodos pertenecen al dominio de 21 valores.",
            "Continuar con las validaciones.",
        )

    anio = pd.to_numeric(df["anio"], errors="coerce")
    anio_periodo = pd.to_numeric(
        periodo.str.slice(0, 4),
        errors="coerce",
    )

    inconsistente = (
        anio.isna()
        | anio_periodo.isna()
        | anio.ne(anio_periodo)
    )

    cantidad_inconsistente = int(inconsistente.sum())

    if cantidad_inconsistente:
        registrar(
            "TEMPORALIDAD",
            "EVA-P17",
            "ERROR",
            "CRITICO",
            (
                f"{cantidad_inconsistente} registros tienen "
                "anio inconsistente con periodo."
            ),
            "Revisar los campos anio y periodo en el origen.",
        )
    else:
        registrar(
            "TEMPORALIDAD",
            "EVA-P17",
            "OK",
            "BAJO",
            "anio coincide con el año indicado en periodo.",
            "Continuar con las validaciones.",
        )


# ============================================================
# 10. CLAVE NATURAL
# ============================================================

def validar_clave_natural(df):
    incompleta = df[COLUMNAS_CLAVE].isna().any(axis=1)

    for columna in COLUMNAS_CLAVE:
        if pd.api.types.is_string_dtype(df[columna].dtype):
            incompleta |= (
                df[columna]
                .astype("string")
                .str.strip()
                .eq("")
                .fillna(False)
            )

    cantidad_incompletas = int(incompleta.sum())

    if cantidad_incompletas:
        registrar(
            "UNICIDAD",
            "EVA-P18",
            "ERROR",
            "CRITICO",
            f"{cantidad_incompletas} registros tienen clave natural incompleta.",
            "Revisar las claves; no eliminar ni completar registros automáticamente.",
        )

    duplicados = df.duplicated(
        subset=COLUMNAS_CLAVE,
        keep=False,
    )

    cantidad_filas_duplicadas = int(duplicados.sum())

    if cantidad_filas_duplicadas:
        ejemplos = (
            df.loc[duplicados, COLUMNAS_CLAVE]
            .head(10)
            .to_dict(orient="records")
        )

        registrar(
            "UNICIDAD",
            "EVA-P19",
            "ERROR",
            "CRITICO",
            (
                f"{cantidad_filas_duplicadas} filas pertenecen a "
                f"claves duplicadas. Ejemplos: {ejemplos}"
            ),
            "Investigar las claves duplicadas sin eliminar filas automáticamente.",
        )
    else:
        registrar(
            "UNICIDAD",
            "EVA-P19",
            "OK",
            "BAJO",
            "No se encontraron claves naturales duplicadas.",
            "Continuar con las validaciones.",
        )

    if not cantidad_incompletas:
        registrar(
            "UNICIDAD",
            "EVA-P20",
            "OK",
            "BAJO",
            "Todas las claves naturales están completas.",
            "Continuar con las validaciones.",
        )


# ============================================================
# 11. REGLAS DE NEGOCIO
# ============================================================

def validar_metricas_y_semantica(df):
    metricas = {
        columna: pd.to_numeric(df[columna], errors="coerce")
        for columna in COLUMNAS_METRICAS
    }

    for columna, serie in metricas.items():
        negativos = serie.lt(0).fillna(False)
        cantidad = int(negativos.sum())

        if cantidad:
            registrar(
                "REGLAS_NEGOCIO",
                f"EVA-P21-{columna}",
                "ADVERTENCIA",
                "ALTO",
                f"{cantidad} registros tienen valores negativos en {columna}.",
                "Investigar los valores con la fuente; no corregir automáticamente.",
            )
        else:
            registrar(
                "REGLAS_NEGOCIO",
                f"EVA-P21-{columna}",
                "OK",
                "BAJO",
                f"No se encontraron valores negativos en {columna}.",
                "Continuar con las validaciones.",
            )

    sembrada = metricas["area_sembrada"]
    cosechada = metricas["area_cosechada"]

    mascara = (
        sembrada.notna()
        & cosechada.notna()
        & cosechada.gt(sembrada)
    )

    cantidad = int(mascara.sum())

    if cantidad:
        registrar(
            "REGLAS_NEGOCIO",
            "EVA-P22",
            "ADVERTENCIA",
            "MEDIO",
            f"{cantidad} registros tienen área cosechada superior al área sembrada.",
            "Revisar la coherencia agronómica de los registros afectados.",
        )
    else:
        registrar(
            "REGLAS_NEGOCIO",
            "EVA-P22",
            "OK",
            "BAJO",
            "No se encontraron áreas cosechadas superiores a las sembradas.",
            "Continuar con las validaciones.",
        )

    mascara = (
        sembrada.eq(0)
        & cosechada.gt(0)
    ).fillna(False)

    cantidad = int(mascara.sum())

    if cantidad:
        registrar(
            "REGLAS_NEGOCIO",
            "EVA-P23",
            "ADVERTENCIA",
            "MEDIO",
            f"{cantidad} registros tienen área sembrada igual a cero y área cosechada positiva.",
            "Revisar los registros afectados; no cambiar sus valores automáticamente.",
        )


# ============================================================
# 12. ESTADO FINAL
# ============================================================

def determinar_estado_final():
    estados = [r["estado"] for r in resultados]

    if "ERROR" in estados:
        return "BLOQUEADO"

    if "ADVERTENCIA" in estados:
        return "REQUIERE_REVISION"

    return "APROBADO"


# ============================================================
# 13. EJECUCIÓN PRINCIPAL
# ============================================================

def main():
    logger.info("Iniciando validación post-transformación de EVA.")

    df = validar_archivo_y_esquema()

    if df is None:
        return finalizar("BLOQUEADO")

    if not all(c in df.columns for c in COLUMNAS_ESPERADAS):
        registrar(
            "ESQUEMA",
            "EVA-P24",
            "ERROR",
            "CRITICO",
            "No se ejecutaron las validaciones dependientes de columnas faltantes.",
            "Corregir el esquema antes de continuar.",
        )
        return finalizar("BLOQUEADO")

    validar_volumen(df)
    ejecutar_validaciones_gx(df)
    validar_nulos_y_tipos(df)
    validar_codigos(df)
    validar_periodos(df)
    validar_clave_natural(df)
    validar_metricas_y_semantica(df)

    estado_final = determinar_estado_final()

    logger.info("Resumen de estados:")
    logger.info(
        "ERROR=%s | ADVERTENCIA=%s | OK=%s | INFORMATIVO=%s",
        sum(r["estado"] == "ERROR" for r in resultados),
        sum(r["estado"] == "ADVERTENCIA" for r in resultados),
        sum(r["estado"] == "OK" for r in resultados),
        sum(r["estado"] == "INFORMATIVO" for r in resultados),
    )

    return finalizar(estado_final)


if __name__ == "__main__":
    sys.exit(main())

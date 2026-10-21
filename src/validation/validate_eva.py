
from pathlib import Path
import logging

import pandas as pd
import great_expectations as gx


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ARCHIVO_STAGING = (
    PROJECT_ROOT / "data" / "staging" / "eva_agricola_staging.csv"
)

CARPETA_VALIDACION = PROJECT_ROOT / "data" / "validation"

ARCHIVO_REPORTE = CARPETA_VALIDACION / "validacion_staging.csv"

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

REGISTROS_ESPERADOS = 166732

# Las advertencias no bloquean por sí solas el procesamiento.
# Los errores sí bloquean la aprobación del staging.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("validate_eva")


# ============================================================
# 2. REGISTRO DE RESULTADOS Y GESTIÓN DE RIESGOS
# ============================================================

resultados = []


def registrar_resultado(
    categoria,
    validacion,
    estado,
    detalle,
    riesgo,
    accion,
):
    resultados.append({
        "categoria": categoria,
        "validacion": validacion,
        "estado": estado,
        "riesgo": riesgo,
        "detalle": detalle,
        "accion_recomendada": accion,
    })

    nivel_log = (
        logging.ERROR if estado == "ERROR"
        else logging.WARNING if estado == "ADVERTENCIA"
        else logging.INFO
    )

    logger.log(
        nivel_log,
        "%s | %s | %s",
        estado,
        validacion,
        detalle,
    )


def resultado_error_critico(categoria, validacion, detalle):
    registrar_resultado(
        categoria,
        validacion,
        "ERROR",
        detalle,
        "CRITICO",
        "Bloquear la aprobación del staging. Corregir el origen "
        "o el proceso responsable y ejecutar nuevamente la validación.",
    )


def resultado_advertencia(categoria, validacion, detalle, riesgo="MEDIO"):
    registrar_resultado(
        categoria,
        validacion,
        "ADVERTENCIA",
        detalle,
        riesgo,
        "Revisar la incidencia y documentar su tratamiento. "
        "No modificar ni eliminar registros automáticamente.",
    )


def resultado_ok(categoria, validacion, detalle):
    registrar_resultado(
        categoria,
        validacion,
        "OK",
        detalle,
        "BAJO",
        "Sin acción correctiva.",
    )


# ============================================================
# 3. VALIDACIONES CON GREAT EXPECTATIONS
# ============================================================

def ejecutar_validaciones_gx(df):
    """
    Ejecuta expectativas GX sobre el DataFrame completo.

    Los errores de expectativas se registran como incidencias.
    Un fallo de ejecución de GX no se interpreta como un resultado
    satisfactorio: se registra como ERROR.
    """

    logger.info("Ejecutando expectativas de Great Expectations.")

    try:
        context = gx.get_context(mode="ephemeral")

        data_source = context.data_sources.add_pandas(
            name="eva_data_source"
        )

        data_asset = data_source.add_dataframe_asset(
            name="eva_staging"
        )

        batch_definition = (
            data_asset.add_batch_definition_whole_dataframe(
                "eva_staging_batch"
            )
        )

        batch = batch_definition.get_batch(
            batch_parameters={"dataframe": df}
        )

        suite = gx.ExpectationSuite(
            name="eva_staging_quality_suite"
        )

        # Las columnas obligatorias deben estar presentes.
        # Se valida su completitud con GX.
        for columna in COLUMNAS_ESPERADAS:
            if columna in df.columns:
                suite.add_expectation(
                    gx.expectations.ExpectColumnValuesToNotBeNull(
                        column=columna
                    )
                )

        context.suites.add(suite)

        validator = context.get_validator(
            batch=batch,
            expectation_suite=suite,
        )

        resultado_gx = validator.validate()

        fallidas = [
            item
            for item in resultado_gx.results
            if not item.success
        ]

        if not fallidas:
            resultado_ok(
                "Great Expectations",
                "Expectation Suite",
                f"GX ejecutó correctamente las expectativas "
                f"definidas para {len(suite.expectations)} columnas.",
            )

        else:
            for item in fallidas:
                config = item.expectation_config
                columna = config.kwargs.get("column", "desconocida")

                resultado_error_critico(
                    "Great Expectations",
                    f"Valores nulos - {columna}",
                    "La expectativa de GX detectó valores nulos "
                    f"en la columna '{columna}'.",
                )

    except Exception as exc:
        resultado_error_critico(
            "Great Expectations",
            "Ejecución del motor GX",
            f"No fue posible ejecutar GX correctamente: {exc}",
        )


# ============================================================
# 4. PROGRAMA PRINCIPAL
# ============================================================

def ejecutar_validacion_staging():

    global resultados
    resultados = []

    print("=" * 70)
    print("VALIDACIÓN DEL DATASET STAGING EVA")
    print("=" * 70)

    # --------------------------------------------------------
    # 1. EXISTENCIA DEL ARCHIVO
    # --------------------------------------------------------

    print("\n[1/10] Verificando archivo.")

    if not ARCHIVO_STAGING.exists():
        resultado_error_critico(
            "Archivo",
            "Existencia del archivo",
            f"No existe el archivo: {ARCHIVO_STAGING}",
        )
        guardar_reporte()
        return

    resultado_ok(
        "Archivo",
        "Existencia del archivo",
        f"Archivo encontrado: {ARCHIVO_STAGING}",
    )

    # --------------------------------------------------------
    # 2. LECTURA DEL DATASET
    # --------------------------------------------------------

    print("\n[2/10] Leyendo dataset staging.")

    try:
        df = pd.read_csv(
            ARCHIVO_STAGING,
            dtype=str,
            low_memory=False,
        )

    except Exception as exc:
        resultado_error_critico(
            "Lectura",
            "Lectura del CSV",
            f"No fue posible leer el archivo: {exc}",
        )
        guardar_reporte()
        return

    registros = len(df)

    logger.info("Registros leídos: %s", f"{registros:,}")
    logger.info("Columnas leídas: %s", len(df.columns))

    if registros == 0:
        resultado_error_critico(
            "Volumen",
            "Dataset vacío",
            "El archivo no contiene registros.",
        )
        guardar_reporte()
        return

    # --------------------------------------------------------
    # 3. VOLUMEN
    # --------------------------------------------------------

    print("\n[3/10] Validando volumen.")

    if registros == REGISTROS_ESPERADOS:
        resultado_ok(
            "Volumen",
            "Cantidad de registros",
            f"Se encontraron {registros:,} registros, "
            "coincidiendo con el volumen de referencia.",
        )

    else:
        resultado_advertencia(
            "Volumen",
            "Cantidad de registros",
            f"Esperados: {REGISTROS_ESPERADOS:,}. "
            f"Encontrados: {registros:,}.",
            "ALTO",
        )

    # --------------------------------------------------------
    # 4. ESTRUCTURA
    # --------------------------------------------------------

    print("\n[4/10] Validando estructura.")

    columnas_actuales = list(df.columns)

    faltantes = [
        c for c in COLUMNAS_ESPERADAS
        if c not in columnas_actuales
    ]

    adicionales = [
        c for c in columnas_actuales
        if c not in COLUMNAS_ESPERADAS
    ]

    if not faltantes and not adicionales:
        if columnas_actuales == COLUMNAS_ESPERADAS:
            resultado_ok(
                "Estructura",
                "Columnas esperadas",
                "Las 18 columnas coinciden en nombre y orden.",
            )
        else:
            resultado_advertencia(
                "Estructura",
                "Orden de columnas",
                "Las columnas existen, pero su orden difiere "
                "del contrato esperado.",
            )

    else:
        resultado_error_critico(
            "Estructura",
            "Columnas esperadas",
            f"Faltantes: {faltantes}. Adicionales: {adicionales}.",
        )

    # No ejecutar controles dependientes de columnas inexistentes.
    columnas_disponibles = set(df.columns)

    # --------------------------------------------------------
    # 5. TIPOS DE DATOS
    # --------------------------------------------------------

    print("\n[5/10] Validando tipos de datos.")

    errores_tipos = []

    for columna in COLUMNAS_TEXTO:
        if columna not in columnas_disponibles:
            continue

        # El CSV se carga como texto intencionalmente para
        # preservar códigos con ceros iniciales.
        valores = df[columna]

        if valores.dropna().map(
            lambda x: not isinstance(x, str)
        ).any():
            errores_tipos.append(
                f"{columna}: contiene valores no textuales."
            )

    anio_numerico = None

    if "anio" in columnas_disponibles:
        anio_numerico = pd.to_numeric(
            df["anio"],
            errors="coerce",
        )

        anio_invalido = (
            anio_numerico.isna()
            | (anio_numerico % 1 != 0)
        )

        if anio_invalido.any():
            errores_tipos.append(
                f"anio: {int(anio_invalido.sum())} valores "
                "no convertibles a enteros."
            )

    metricas = {}

    for columna in COLUMNAS_METRICAS:
        if columna not in columnas_disponibles:
            continue

        original = df[columna]
        valores = pd.to_numeric(
            original,
            errors="coerce",
        )

        invalidos = original.notna() & valores.isna()

        metricas[columna] = valores

        if invalidos.any():
            errores_tipos.append(
                f"{columna}: {int(invalidos.sum())} "
                "valores no numéricos."
            )

        no_finitos = valores.notna() & ~valores.map(
            lambda x: __import__("math").isfinite(x)
        )

        if no_finitos.any():
            errores_tipos.append(
                f"{columna}: {int(no_finitos.sum())} "
                "valores no finitos."
            )

    if errores_tipos:
        resultado_error_critico(
            "Tipos",
            "Tipos de datos",
            " | ".join(errores_tipos),
        )
    else:
        resultado_ok(
            "Tipos",
            "Tipos de datos",
            "Códigos y dimensiones conservados como texto; "
            "año entero y métricas numéricas.",
        )

    # --------------------------------------------------------
    # 6. NULOS Y VACÍOS
    # --------------------------------------------------------

    print("\n[6/10] Validando nulos y vacíos.")

    nulos_total = int(df.isna().sum().sum())

    vacios_por_columna = (
        df.apply(lambda col: col.str.strip().eq(""))
        .sum()
    )

    vacios_total = int(vacios_por_columna.sum())

    if nulos_total == 0 and vacios_total == 0:
        resultado_ok(
            "Completitud",
            "Nulos y vacíos",
            "No se encontraron nulos ni cadenas vacías.",
        )

    else:
        # La completitud ya se clasifica con detalle por campo.
        # Los nulos en columnas requeridas también se evalúan con GX.
        detalle = (
            f"Nulos: {nulos_total:,}. "
            f"Cadenas vacías: {vacios_total:,}. "
            f"Detalle de vacíos: "
            f"{vacios_por_columna[vacios_por_columna > 0].to_dict()}."
        )

        resultado_advertencia(
            "Completitud",
            "Nulos y vacíos",
            detalle,
            "ALTO",
        )

    # --------------------------------------------------------
    # 7. CÓDIGOS GEOGRÁFICOS Y DE CULTIVO
    # --------------------------------------------------------

    print("\n[7/10] Validando códigos.")

    errores_codigos = []

    if "codigo_departamento" in columnas_disponibles:
        codigo_dep = df["codigo_departamento"].str.strip()

        invalidos = ~codigo_dep.str.fullmatch(
            r"\d{2}", na=False
        )

        if invalidos.any():
            errores_codigos.append(
                f"Códigos de departamento inválidos: "
                f"{int(invalidos.sum())}."
            )

    if "codigo_municipio" in columnas_disponibles:
        codigo_mun = df["codigo_municipio"].str.strip()

        invalidos = ~codigo_mun.str.fullmatch(
            r"\d{5}", na=False
        )

        if invalidos.any():
            errores_codigos.append(
                f"Códigos de municipio inválidos: "
                f"{int(invalidos.sum())}."
            )

        elif "codigo_departamento" in columnas_disponibles:
            codigo_dep = df["codigo_departamento"].str.strip()

            prefijos_inconsistentes = (
                codigo_mun.str[:2] != codigo_dep
            )

            if prefijos_inconsistentes.any():
                errores_codigos.append(
                    "El prefijo departamental no coincide con "
                    f"el código municipal en "
                    f"{int(prefijos_inconsistentes.sum())} registros."
                )

    if "codigo_cultivo" in columnas_disponibles:
        codigo_cultivo = df["codigo_cultivo"].str.strip()

        if codigo_cultivo.eq("").any():
            errores_codigos.append(
                "Existen códigos de cultivo vacíos."
            )

    if errores_codigos:
        resultado_error_critico(
            "Identificadores",
            "Códigos geográficos y de cultivo",
            " ".join(errores_codigos),
        )
    else:
        resultado_ok(
            "Identificadores",
            "Códigos geográficos y de cultivo",
            "Los códigos presentes cumplen las reglas "
            "estructurales aplicadas.",
        )

    # --------------------------------------------------------
    # 8. CONSISTENCIA TEMPORAL
    # --------------------------------------------------------

    print("\n[8/10] Validando año y periodo.")

    if {
        "anio", "periodo"
    }.issubset(columnas_disponibles):

        anio = pd.to_numeric(
            df["anio"],
            errors="coerce",
        )

        periodo = df["periodo"].str.strip()

        anio_periodo = periodo.str.extract(
            r"^(\d{4})",
            expand=False,
        )

        periodo_anio_numerico = pd.to_numeric(
            anio_periodo,
            errors="coerce",
        )

        inconsistencia = (
            anio.isna()
            | periodo_anio_numerico.isna()
            | (anio != periodo_anio_numerico)
            | (anio % 1 != 0)
        )

        cantidad = int(inconsistencia.sum())

        if cantidad == 0:
            resultado_ok(
                "Tiempo",
                "Año vs periodo",
                "Año y periodo son consistentes en todos los registros.",
            )
        else:
            resultado_error_critico(
                "Tiempo",
                "Año vs periodo",
                f"{cantidad:,} registros inconsistentes.",
            )

    # --------------------------------------------------------
    # 9. GRANULARIDAD Y UNICIDAD
    # --------------------------------------------------------

    print("\n[9/10] Validando granularidad.")

    if set(COLUMNAS_CLAVE).issubset(columnas_disponibles):

        claves = df[COLUMNAS_CLAVE].apply(
            lambda col: col.str.strip()
        )

        clave_incompleta = claves.eq("").any(axis=1) | claves.isna().any(axis=1)

        duplicados = int(
            claves.duplicated(keep=False).sum()
        )

        claves_duplicadas_excedentes = int(
            claves.duplicated(keep="first").sum()
        )

        if clave_incompleta.any():
            resultado_error_critico(
                "Granularidad",
                "Completitud de la clave",
                f"{int(clave_incompleta.sum())} registros tienen "
                "al menos un atributo de la clave vacío o nulo.",
            )

        if claves_duplicadas_excedentes == 0:
            resultado_ok(
                "Granularidad",
                "Municipio + cultivo + año + periodo",
                f"{registros:,} registros y ninguna clave duplicada.",
            )
        else:
            resultado_error_critico(
                "Granularidad",
                "Municipio + cultivo + año + periodo",
                f"{claves_duplicadas_excedentes:,} claves "
                f"duplicadas excedentes; "
                f"{duplicados:,} filas participan en duplicados.",
            )

    # --------------------------------------------------------
    # 10. MÉTRICAS Y CONDICIONES SEMÁNTICAS
    # --------------------------------------------------------

    print("\n[10/10] Validando métricas y semántica.")

    for columna, valores in metricas.items():

        negativos = int((valores < 0).sum())
        minimo = valores.min()
        maximo = valores.max()

        if negativos == 0:
            resultado_ok(
                "Métricas",
                f"Valores negativos - {columna}",
                f"Sin valores negativos. Rango: {minimo} a {maximo}.",
            )
        else:
            resultado_advertencia(
                "Métricas",
                f"Valores negativos - {columna}",
                f"{negativos:,} valores negativos.",
                "ALTO",
            )

    if {
        "area_sembrada", "area_cosechada"
    }.issubset(metricas):

        area_sembrada = metricas["area_sembrada"]
        area_cosechada = metricas["area_cosechada"]

        cosechada_mayor = int(
            (area_cosechada > area_sembrada).sum()
        )

        sembrada_cero_cosechada_mayor = int(
            (
                (area_sembrada == 0)
                & (area_cosechada > 0)
            ).sum()
        )

        registrar_resultado(
            "Semántica",
            "Área cosechada > área sembrada",
            "INFORMATIVO",
            f"{cosechada_mayor:,} registros. "
            "Se conservan para análisis; no se tratan automáticamente "
            "como errores.",
            "POR REVISAR",
            "Investigar el contexto agrícola antes de establecer "
            "una regla de rechazo.",
        )

        registrar_resultado(
            "Semántica",
            "Área sembrada = 0 y cosechada > 0",
            "INFORMATIVO",
            f"{sembrada_cero_cosechada_mayor:,} registros. "
            "Se conservan sin corrección automática.",
            "POR REVISAR",
            "Evaluar la plausibilidad con la definición del dato "
            "y el contexto de origen.",
        )

    # Ejecutar GX sobre el DataFrame cargado.
    # La validación pandas complementa, no reemplaza, las expectativas.
    print("\nEjecutando Great Expectations.")
    ejecutar_validaciones_gx(df)

    # ========================================================
    # 11. ESTADO FINAL Y REPORTE
    # ========================================================

    estados = {r["estado"] for r in resultados}

    if "ERROR" in estados:
        estado_final = "BLOQUEADO"
    elif "ADVERTENCIA" in estados:
        estado_final = "REQUIERE_REVISION"
    else:
        estado_final = "APROBADO"

    registrar_resultado(
        "Resultado final",
        "Decisión de calidad del staging",
        estado_final,
        "Resultado calculado a partir de los controles ejecutados.",
        "GLOBAL",
        (
            "No promover a la siguiente etapa si está BLOQUEADO. "
            "Si REQUIERE_REVISION, evaluar las advertencias según "
            "la política del proceso antes de continuar. "
            "APROBADO indica que no se registraron errores "
            "ni advertencias bloqueantes."
        ),
    )

    print("\n" + "=" * 70)
    print("RESUMEN DE VALIDACIÓN")
    print("=" * 70)

    reporte = pd.DataFrame(resultados)

    for estado, cantidad in reporte["estado"].value_counts().items():
        print(f"{estado:<20}: {cantidad}")

    print(f"\nEstado final: {estado_final}")

    guardar_reporte(reporte)

    print("\nVALIDACIÓN FINALIZADA")


# ============================================================
# 12. EXPORTACIÓN DEL REPORTE
# ============================================================

def guardar_reporte(reporte=None):

    CARPETA_VALIDACION.mkdir(
        parents=True,
        exist_ok=True,
    )

    if reporte is None:
        reporte = pd.DataFrame(resultados)

    reporte.to_csv(
        ARCHIVO_REPORTE,
        index=False,
        encoding="utf-8-sig",
    )

    logger.info("Reporte generado: %s", ARCHIVO_REPORTE)


if __name__ == "__main__":
    ejecutar_validacion_staging()

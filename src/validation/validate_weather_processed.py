
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import great_expectations as gx


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = PROJECT_ROOT / "data" / "raw" / "open_meteo_daily_batches"
PROCESSED_FILE = PROJECT_ROOT / "data" / "processed" / "weather_processed.csv"
DIVIPOLA_FILE = PROJECT_ROOT / "data" / "source" / "divipola.csv"
REPORT_DIR = PROJECT_ROOT / "outputs" / "validation"

START_DATE = pd.Timestamp("2019-01-01")
END_DATE = pd.Timestamp("2025-12-31")
EXPECTED_DAYS = (END_DATE - START_DATE).days + 1

EXPECTED_MUNICIPALITIES = 1103
EXPECTED_BATCHES = set(range(1, 46))
CHUNK_SIZE = 100_000

EXPECTED_SOURCE_COLUMNS = [
    "location_id",
    "date",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
    "municipality_code",
    "municipality_name",
    "latitude",
    "longitude",
]

EXPECTED_PROCESSED_COLUMNS = EXPECTED_SOURCE_COLUMNS + [
    "source_batch_id"
]

WEATHER_COLUMNS = [
    "temperature_2m_min",
    "temperature_2m_mean",
    "temperature_2m_max",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
]

NUMERIC_COLUMNS = WEATHER_COLUMNS + [
    "location_id",
    "latitude",
    "longitude",
]

CRITICAL = "CRITICA"
WARNING = "ADVERTENCIA"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("validate_weather_processed")


# ============================================================
# 2. UTILIDADES
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_column(
    columns: list[str],
    candidates: set[str],
) -> str | None:
    normalized = {
        str(column).strip().lower(): column
        for column in columns
    }

    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]

    return None


def identify_batch(path: Path) -> int | None:
    match = re.search(
        r"batch[_-]?(\d+)",
        path.stem,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def normalize_code(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(5)
    )


def normalize_batch_id(series: pd.Series) -> pd.Series:
    result = (
        series.astype("string")
        .str.strip()
        .str.lower()
        .str.replace(
            r"^batch[-_]?(\d+)$",
            r"batch_\1",
            regex=True,
        )
    )

    result = result.str.replace(
        r"^batch_(\d)$", r"batch_00\1", regex=True
    )
    result = result.str.replace(
        r"^batch_(\d{2})$", r"batch_0\1", regex=True
    )

    return result


def add_check(
    report: dict,
    check_id: str,
    category: str,
    passed: bool,
    severity: str,
    detail: str,
    action: str,
    metrics: dict | None = None,
) -> None:
    report["validations"].append({
        "id": check_id,
        "categoria": category,
        "estado": (
            "OK" if passed
            else "ADVERTENCIA" if severity == WARNING
            else "ERROR"
        ),
        "riesgo": severity,
        "detalle": detail,
        "accion_recomendada": action,
        "metricas": metrics or {},
    })


def count_csv_rows(path: Path) -> int:
    """Cuenta registros por bloques sin cargar todo el archivo."""
    total = 0

    for chunk in pd.read_csv(
        path,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        total += len(chunk)

    return total


def discover_batches() -> tuple[dict[int, Path], list[int]]:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(
            f"No existe el directorio de batches: {INPUT_DIR}"
        )

    paths: dict[int, Path] = {}
    duplicates = []

    for path in INPUT_DIR.glob("weather_batch_*.csv"):
        number = identify_batch(path)

        if number is None:
            continue

        if number in paths:
            duplicates.append(number)
        else:
            paths[number] = path

    return paths, sorted(set(duplicates))


def load_divipola_codes() -> set[str]:
    if not DIVIPOLA_FILE.exists():
        raise FileNotFoundError(
            f"No existe el catálogo DIVIPOLA: {DIVIPOLA_FILE}"
        )

    catalog = pd.read_csv(
        DIVIPOLA_FILE,
        dtype=str,
        low_memory=False,
    )
    catalog.columns = catalog.columns.str.strip()

    code_col = find_column(
        list(catalog.columns),
        {
            "codigo_municipio",
            "código_municipio",
            "municipality_code",
            "municipality_id",
            "cod_municipio",
            "código municipio",
            "codigo municipio",
        },
    )

    type_col = find_column(
        list(catalog.columns),
        {
            "tipo",
            "tipo_divipola",
            "tipo_entidad",
            "type",
            "category",
        },
    )

    if code_col is None:
        raise ValueError(
            "No se identificó la columna del código municipal "
            f"en DIVIPOLA. Columnas: {list(catalog.columns)}"
        )

    codes = normalize_code(catalog[code_col])

    if type_col is not None:
        entity_type = (
            catalog[type_col]
            .astype("string")
            .str.strip()
            .str.lower()
        )

        municipality_mask = entity_type.isin({
            "municipio",
            "municipios",
            "municipality",
        })

        codes = codes[municipality_mask]

    return set(codes.dropna().tolist())


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)

    if isinstance(value, (np.bool_,)):
        return bool(value)

    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if value is None or value is pd.NA:
        return None

    if isinstance(value, float) and np.isnan(value):
        return None

    return value


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    path = REPORT_DIR / (
        f"weather_processed_validation_{timestamp}.json"
    )

    report["report_path"] = str(path)
    report["report_generated_at_utc"] = utc_now()

    path.write_text(
        json.dumps(
            json_safe(report),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


# ============================================================
# 3. GREAT EXPECTATIONS
# ============================================================

def validate_with_gx(df: pd.DataFrame) -> dict:
    """
    Ejecuta expectativas básicas con Great Expectations 1.24.0.
    Las reglas cruzadas se implementan mediante pandas.
    """
    result = {
        "executed": False,
        "success": False,
        "detail": None,
        "expectations": [],
    }

    try:
        context = gx.get_context(mode="ephemeral")

        datasource = context.data_sources.add_pandas(
            name="weather_processed_datasource"
        )

        asset = datasource.add_dataframe_asset(
            name="weather_processed_asset"
        )

        batch_definition = (
            asset.add_batch_definition_whole_dataframe(
                name="weather_processed_batch"
            )
        )

        batch = batch_definition.get_batch(
            batch_parameters={"dataframe": df}
        )

        suite = gx.ExpectationSuite(
            name="weather_processed_basic_suite"
        )

        suite.add_expectation(
            gx.expectations.ExpectColumnValuesToNotBeNull(
                column="date"
            )
        )

        suite.add_expectation(
            gx.expectations.ExpectColumnValuesToNotBeNull(
                column="municipality_code"
            )
        )

        suite.add_expectation(
            gx.expectations.ExpectColumnValuesToNotBeNull(
                column="source_batch_id"
            )
        )

        validation = batch.validate(suite)

        result["executed"] = True
        result["success"] = bool(validation.success)
        result["expectations"] = [
            {
                "type": item.expectation_config.type,
                "success": bool(item.success),
                "result": json_safe(dict(item.result or {})),
            }
            for item in validation.results
        ]

        result["detail"] = (
            "Expectativas de Great Expectations ejecutadas."
        )

    except Exception as exc:
        result["detail"] = (
            f"Falló la ejecución de Great Expectations: {exc}"
        )
        logger.exception(
            "No fue posible ejecutar Great Expectations"
        )

    return result


# ============================================================
# 4. VALIDACIÓN PRINCIPAL
# ============================================================

def ejecutar_validacion() -> tuple[dict, int]:

    report = {
        "dataset": "weather",
        "stage": "post_transformation",
        "validator": "validate_weather_processed.py",
        "started_at_utc": utc_now(),
        "processed_file": str(PROCESSED_FILE),
        "input_directory": str(INPUT_DIR),
        "divipola_file": str(DIVIPOLA_FILE),
        "periodo_esperado": {
            "inicio": str(START_DATE.date()),
            "fin": str(END_DATE.date()),
            "dias_por_municipio": EXPECTED_DAYS,
        },
        "municipios_esperados": EXPECTED_MUNICIPALITIES,
        "validations": [],
        "metrics": {},
        "great_expectations": {},
        "status": "BLOQUEADO",
    }

    # --------------------------------------------------------
    # WX-POST-01: existencia y lectura
    # --------------------------------------------------------

    if not PROCESSED_FILE.exists():
        add_check(
            report,
            "WX-POST-01",
            "ARCHIVO",
            False,
            CRITICAL,
            f"No existe el archivo {PROCESSED_FILE}.",
            "Ejecutar la transformación y verificar su ruta de salida.",
        )
        report["finished_at_utc"] = utc_now()
        return report, 1

    try:
        df = pd.read_csv(
            PROCESSED_FILE,
            low_memory=False,
            dtype={
                "municipality_code": "string",
                "source_batch_id": "string",
                "batch_id": "string",
                "source_batch": "string",
            },
        )
        df.columns = df.columns.str.strip()

    except Exception as exc:
        add_check(
            report,
            "WX-POST-01",
            "ARCHIVO",
            False,
            CRITICAL,
            f"No fue posible leer el CSV: {exc}",
            "Revisar la integridad del archivo y volver a ejecutar "
            "la transformación.",
        )
        report["finished_at_utc"] = utc_now()
        return report, 1

    add_check(
        report,
        "WX-POST-01",
        "ARCHIVO",
        True,
        CRITICAL,
        f"Archivo leído correctamente: {len(df):,} registros.",
        "Continuar con las validaciones.",
        {
            "rows": len(df),
            "columns": len(df.columns),
        },
    )

    # --------------------------------------------------------
    # WX-POST-02: esquema
    # --------------------------------------------------------

    missing_columns = sorted(
        set(EXPECTED_PROCESSED_COLUMNS) - set(df.columns)
    )
    unexpected_columns = sorted(
        set(df.columns) - set(EXPECTED_PROCESSED_COLUMNS)
    )
    duplicate_columns = df.columns[
        df.columns.duplicated()
    ].tolist()

    schema_ok = (
        not missing_columns
        and not unexpected_columns
        and not duplicate_columns
    )

    add_check(
        report,
        "WX-POST-02",
        "ESQUEMA",
        schema_ok,
        CRITICAL,
        (
            "El esquema coincide con las 13 columnas esperadas."
            if schema_ok
            else "El esquema procesado no coincide con el contrato."
        ),
        "Alinear la transformación con el esquema acordado.",
        {
            "expected_columns": EXPECTED_PROCESSED_COLUMNS,
            "actual_columns": list(df.columns),
            "missing_columns": missing_columns,
            "unexpected_columns": unexpected_columns,
            "duplicate_columns": duplicate_columns,
        },
    )

    # --------------------------------------------------------
    # WX-POST-03: inventario y conservación de filas
    # --------------------------------------------------------

    try:
        source_paths, duplicate_batches = discover_batches()

        source_counts = {
            number: count_csv_rows(path)
            for number, path in source_paths.items()
        }

        source_rows = sum(source_counts.values())
        row_count_ok = len(df) == source_rows

        missing_batches = sorted(
            EXPECTED_BATCHES - set(source_paths)
        )
        unexpected_batches = sorted(
            set(source_paths) - EXPECTED_BATCHES
        )

        add_check(
            report,
            "WX-POST-03",
            "VOLUMEN",
            row_count_ok,
            CRITICAL,
            (
                f"Filas fuente={source_rows:,}; "
                f"filas procesadas={len(df):,}."
            ),
            "Investigar pérdidas o duplicaciones de filas durante "
            "la transformación.",
            {
                "source_rows": source_rows,
                "processed_rows": len(df),
                "source_batch_count": len(source_paths),
            },
        )

        batch_inventory_ok = (
            not missing_batches
            and not unexpected_batches
            and not duplicate_batches
        )

        add_check(
            report,
            "WX-POST-03B",
            "BATCHES",
            batch_inventory_ok,
            CRITICAL,
            (
                "Se encontraron los 45 batches esperados."
                if batch_inventory_ok
                else "El inventario de batches está incompleto "
                     "o contiene identificadores duplicados."
            ),
            "Verificar los archivos de extracción y su nomenclatura.",
            {
                "missing_batches": missing_batches,
                "unexpected_batches": unexpected_batches,
                "duplicate_batches": duplicate_batches,
            },
        )

    except Exception as exc:
        source_paths = {}
        source_counts = {}
        source_rows = None

        add_check(
            report,
            "WX-POST-03",
            "VOLUMEN",
            False,
            CRITICAL,
            f"No fue posible contrastar los batches: {exc}",
            "Verificar el directorio y la lectura de los archivos fuente.",
        )

    # Resolver columnas reconocidas.
    date_col = find_column(
        list(df.columns),
        {"date", "time", "fecha"},
    )

    code_col = find_column(
        list(df.columns),
        {
            "municipality_code",
            "codigo_municipio",
            "municipality_id",
            "cod_municipio",
        },
    )

    batch_col = find_column(
        list(df.columns),
        {
            "source_batch_id",
            "batch_id",
            "source_batch",
        },
    )

    # --------------------------------------------------------
    # WX-POST-04: campos obligatorios y valores numéricos
    # --------------------------------------------------------

    required_columns = [
        "location_id",
        "municipality_name",
        "latitude",
        "longitude",
    ]

    if date_col:
        required_columns.append(date_col)

    if code_col:
        required_columns.append(code_col)

    if batch_col:
        required_columns.append(batch_col)

    nulls_required = {
        column: int(df[column].isna().sum())
        for column in required_columns
        if column in df.columns
    }

    required_ok = (
        len(required_columns) == 7
        and all(value == 0 for value in nulls_required.values())
    )

    add_check(
        report,
        "WX-POST-04",
        "OBLIGATORIOS",
        required_ok,
        CRITICAL,
        (
            "Los campos obligatorios no contienen nulos."
            if required_ok
            else f"Nulos detectados: {nulls_required}."
        ),
        "Revisar los datos fuente y las conversiones realizadas "
        "durante la transformación.",
        {"nulls_by_column": nulls_required},
    )

    invalid_numeric = {}
    non_finite_numeric = {}

    for column in NUMERIC_COLUMNS:
        if column not in df.columns:
            continue

        original = df[column]
        converted = pd.to_numeric(original, errors="coerce")

        invalid_numeric[column] = int(
            (original.notna() & converted.isna()).sum()
        )

        non_finite_numeric[column] = int(
            (
                converted.notna()
                & ~np.isfinite(converted.astype(float))
            ).sum()
        )

    numeric_ok = (
        all(v == 0 for v in invalid_numeric.values())
        and all(v == 0 for v in non_finite_numeric.values())
        and set(NUMERIC_COLUMNS).issubset(df.columns)
    )

    add_check(
        report,
        "WX-POST-04B",
        "TIPOS",
        numeric_ok,
        CRITICAL,
        (
            "Las columnas numéricas contienen valores válidos."
            if numeric_ok
            else "Hay conversiones numéricas inválidas, infinitos "
                 "o columnas numéricas faltantes."
        ),
        "Revisar las conversiones numéricas y los archivos originales.",
        {
            "invalid_numeric_values": invalid_numeric,
            "non_finite_values": non_finite_numeric,
        },
    )

    # --------------------------------------------------------
    # WX-POST-05: fechas y periodo
    # --------------------------------------------------------

    if date_col:
        dates = pd.to_datetime(df[date_col], errors="coerce")

        invalid_dates = int(dates.isna().sum())

        out_of_range = int(
            (
                dates.notna()
                & (
                    (dates < START_DATE)
                    | (dates > END_DATE)
                )
            ).sum()
        )

        dates_ok = invalid_dates == 0 and out_of_range == 0

        add_check(
            report,
            "WX-POST-05",
            "FECHAS",
            dates_ok,
            CRITICAL,
            (
                "Todas las fechas son válidas y pertenecen al "
                "periodo 2019-01-01 a 2025-12-31."
                if dates_ok
                else f"Fechas inválidas={invalid_dates}; "
                     f"fuera de rango={out_of_range}."
            ),
            "Revisar la normalización de fechas y corregir la causa "
            "en la fuente o transformación.",
            {
                "invalid_dates": invalid_dates,
                "out_of_range_dates": out_of_range,
                "minimum_date": dates.min(),
                "maximum_date": dates.max(),
            },
        )

    else:
        dates = pd.Series(pd.NaT, index=df.index)

        add_check(
            report,
            "WX-POST-05",
            "FECHAS",
            False,
            CRITICAL,
            "No se encontró una columna de fecha.",
            "Conservar una columna de fecha reconocida, por ejemplo date.",
        )

    # --------------------------------------------------------
    # WX-POST-06: códigos municipales y unicidad
    # --------------------------------------------------------

    if code_col:
        codes = normalize_code(df[code_col])

        invalid_code_mask = (
            codes.isna()
            | ~codes.str.fullmatch(r"\d{5}", na=False)
        )

        invalid_codes = int(invalid_code_mask.sum())
        unique_municipalities = int(
            codes[~invalid_code_mask].nunique()
        )

    else:
        codes = pd.Series(pd.NA, index=df.index, dtype="string")
        invalid_codes = len(df)
        unique_municipalities = 0

    if code_col and date_col:
        keys = pd.DataFrame({
            "municipality_code": codes,
            "date": dates.dt.normalize(),
        })

        duplicate_key_rows = int(
            keys.duplicated(
                subset=["municipality_code", "date"],
                keep=False,
            ).sum()
        )

        uniqueness_ok = (
            duplicate_key_rows == 0
            and invalid_codes == 0
            and dates.notna().all()
        )

    else:
        duplicate_key_rows = 0
        uniqueness_ok = False

    add_check(
        report,
        "WX-POST-06",
        "UNICIDAD",
        uniqueness_ok,
        CRITICAL,
        (
            "La clave municipio-fecha es única."
            if uniqueness_ok
            else f"Filas en claves duplicadas={duplicate_key_rows}; "
                 f"códigos municipales inválidos={invalid_codes}."
        ),
        "Investigar las claves duplicadas y normalizar los códigos "
        "municipales.",
        {
            "duplicate_key_rows": duplicate_key_rows,
            "invalid_municipality_codes": invalid_codes,
            "unique_municipalities": unique_municipalities,
        },
    )

    # --------------------------------------------------------
    # WX-POST-07: trazabilidad de batches
    # --------------------------------------------------------

    if batch_col:
        batch_ids = normalize_batch_id(df[batch_col])

        pattern_ok = batch_ids.str.fullmatch(
            r"batch_\d{3}",
            na=False,
        )

        batch_numbers = pd.to_numeric(
            batch_ids.str.extract(
                r"batch_(\d{3})",
                expand=False,
            ),
            errors="coerce",
        )

        invalid_batch_ids = int((~pattern_ok).sum())

        out_of_range_batches = int(
            (
                pattern_ok
                & ~batch_numbers.isin(EXPECTED_BATCHES)
            ).sum()
        )

        batches_in_output = sorted(
            int(value)
            for value in batch_numbers.dropna().unique()
        )

        batch_ids_ok = (
            invalid_batch_ids == 0
            and out_of_range_batches == 0
        )

    else:
        batch_ids = pd.Series(pd.NA, index=df.index, dtype="string")
        invalid_batch_ids = len(df)
        out_of_range_batches = 0
        batches_in_output = []
        batch_ids_ok = False

    add_check(
        report,
        "WX-POST-07",
        "TRAZABILIDAD",
        batch_ids_ok,
        CRITICAL,
        (
            "Los identificadores de origen usan batch_001 a batch_045."
            if batch_ids_ok
            else f"Identificadores inválidos={invalid_batch_ids}; "
                 f"batches fuera de rango={out_of_range_batches}."
        ),
        "Asegurar que source_batch_id se asigne a partir del archivo "
        "real que originó cada registro.",
        {
            "invalid_batch_ids": invalid_batch_ids,
            "out_of_range_batches": out_of_range_batches,
            "batches_in_output": batches_in_output,
        },
    )

    # --------------------------------------------------------
    # WX-POST-08: coherencia DIVIPOLA
    # --------------------------------------------------------

    try:
        catalog_codes = load_divipola_codes()

        output_codes = set(codes.dropna().tolist())
        codes_outside_catalog = sorted(output_codes - catalog_codes)

        divipola_ok = (
            len(catalog_codes) == EXPECTED_MUNICIPALITIES
            and unique_municipalities == EXPECTED_MUNICIPALITIES
            and not codes_outside_catalog
        )

        add_check(
            report,
            "WX-POST-08",
            "DIVIPOLA",
            divipola_ok,
            CRITICAL,
            (
                "Los códigos municipales coinciden con el catálogo DIVIPOLA."
                if divipola_ok
                else f"Códigos en catálogo={len(catalog_codes)}; "
                     f"municipios en salida={unique_municipalities}; "
                     f"códigos fuera del catálogo={len(codes_outside_catalog)}."
            ),
            "Verificar el catálogo DIVIPOLA y la correspondencia de "
            "los códigos de municipio.",
            {
                "catalog_municipalities": len(catalog_codes),
                "output_municipalities": unique_municipalities,
                "codes_outside_catalog_count": len(codes_outside_catalog),
                "codes_outside_catalog_sample": codes_outside_catalog[:20],
            },
        )

    except Exception as exc:
        add_check(
            report,
            "WX-POST-08",
            "DIVIPOLA",
            False,
            CRITICAL,
            f"No fue posible validar DIVIPOLA: {exc}",
            "Verificar la ruta y el esquema del catálogo.",
        )

    # --------------------------------------------------------
    # WX-POST-09: dominios meteorológicos
    # --------------------------------------------------------

    weather = {
        column: pd.to_numeric(df[column], errors="coerce")
        for column in WEATHER_COLUMNS
        if column in df.columns
    }

    domain_results = []

    def register_domain(
        rule_id: str,
        column: str,
        invalid_mask: pd.Series,
        severity: str,
        rule: str,
    ) -> None:
        invalid_count = int(invalid_mask.fillna(False).sum())

        domain_results.append({
            "id": rule_id,
            "column": column,
            "invalid_rows": invalid_count,
            "severity": severity,
            "rule": rule,
        })

    temperature_columns = {
        "temperature_2m_min",
        "temperature_2m_mean",
        "temperature_2m_max",
    }

    if temperature_columns.issubset(weather):
        tmin = weather["temperature_2m_min"]
        tmean = weather["temperature_2m_mean"]
        tmax = weather["temperature_2m_max"]

        complete = tmin.notna() & tmean.notna() & tmax.notna()

        register_domain(
            "WX-POST-09A",
            "temperature_2m_min/mean/max",
            complete & ~((tmin <= tmean) & (tmean <= tmax)),
            CRITICAL,
            "Cuando las tres temperaturas existen: min <= mean <= max.",
        )

    if "precipitation_sum" in weather:
        register_domain(
            "WX-POST-09B",
            "precipitation_sum",
            weather["precipitation_sum"] < 0,
            CRITICAL,
            "La precipitación no puede ser negativa.",
        )

    if "relative_humidity_2m_mean" in weather:
        humidity = weather["relative_humidity_2m_mean"]

        register_domain(
            "WX-POST-09C",
            "relative_humidity_2m_mean",
            (humidity < 0) | (humidity > 100),
            CRITICAL,
            "La humedad relativa debe estar entre 0 y 100.",
        )

    if "et0_fao_evapotranspiration" in weather:
        register_domain(
            "WX-POST-09D",
            "et0_fao_evapotranspiration",
            weather["et0_fao_evapotranspiration"] < 0,
            WARNING,
            "ET0 negativa requiere revisión.",
        )

    critical_domain_errors = [
        item for item in domain_results
        if item["invalid_rows"] > 0
        and item["severity"] == CRITICAL
    ]

    warning_domain_errors = [
        item for item in domain_results
        if item["invalid_rows"] > 0
        and item["severity"] == WARNING
    ]

    domain_ok = (
        not critical_domain_errors
        and not warning_domain_errors
        and len(weather) == len(WEATHER_COLUMNS)
    )

    add_check(
        report,
        "WX-POST-09",
        "DOMINIOS_METEOROLOGICOS",
        domain_ok,
        CRITICAL if critical_domain_errors else WARNING,
        (
            "Las reglas de dominio meteorológico se cumplen."
            if domain_ok
            else f"Incumplimientos críticos={len(critical_domain_errors)}; "
                 f"advertencias={len(warning_domain_errors)}."
        ),
        "Investigar los valores anómalos. No corregirlos automáticamente.",
        {"rules": domain_results},
    )

    # --------------------------------------------------------
    # WX-POST-10: valores nulos meteorológicos
    # --------------------------------------------------------

    nulls_weather = {
        column: int(df[column].isna().sum())
        for column in WEATHER_COLUMNS
        if column in df.columns
    }

    if set(WEATHER_COLUMNS).issubset(df.columns):
        all_weather_missing = int(
            df[WEATHER_COLUMNS].isna().all(axis=1).sum()
        )
    else:
        all_weather_missing = len(df)

    weather_nulls_ok = (
        len(nulls_weather) == len(WEATHER_COLUMNS)
        and all(value == 0 for value in nulls_weather.values())
        and all_weather_missing == 0
    )

    add_check(
        report,
        "WX-POST-10",
        "NULOS_METEOROLOGICOS",
        weather_nulls_ok,
        WARNING,
        (
            "No se encontraron nulos meteorológicos."
            if weather_nulls_ok
            else f"Nulos por variable={nulls_weather}; "
                 f"filas sin ninguna variable meteorológica="
                 f"{all_weather_missing}."
        ),
        "Evaluar los nulos según los requisitos de consumo. "
        "No imputar datos en el validador.",
        {
            "nulls_by_column": nulls_weather,
            "rows_without_weather_values": all_weather_missing,
        },
    )

    # --------------------------------------------------------
    # WX-POST-11: cobertura diaria por municipio
    # --------------------------------------------------------

    coverage_ok = False
    coverage_metrics = {}

    if code_col and date_col and dates.notna().all():
        coverage = pd.DataFrame({
            "municipality_code": codes,
            "date": dates.dt.normalize(),
        }).dropna()

        stats = coverage.groupby("municipality_code")["date"].agg(
            rows="size",
            distinct_dates="nunique",
            minimum="min",
            maximum="max",
        )

        wrong_rows = stats[stats["rows"] != EXPECTED_DAYS]
        wrong_distinct_dates = stats[
            stats["distinct_dates"] != EXPECTED_DAYS
        ]
        wrong_start = stats[stats["minimum"] != START_DATE]
        wrong_end = stats[stats["maximum"] != END_DATE]

        coverage_ok = (
            len(stats) == EXPECTED_MUNICIPALITIES
            and wrong_rows.empty
            and wrong_distinct_dates.empty
            and wrong_start.empty
            and wrong_end.empty
        )

        problematic_codes = sorted(
            set(wrong_rows.index)
            | set(wrong_distinct_dates.index)
            | set(wrong_start.index)
            | set(wrong_end.index)
        )

        coverage_metrics = {
            "municipalities_found": len(stats),
            "expected_municipalities": EXPECTED_MUNICIPALITIES,
            "expected_days_per_municipality": EXPECTED_DAYS,
            "municipalities_wrong_row_count": len(wrong_rows),
            "municipalities_wrong_distinct_dates": len(wrong_distinct_dates),
            "municipalities_wrong_start_date": len(wrong_start),
            "municipalities_wrong_end_date": len(wrong_end),
            "problematic_municipality_sample": problematic_codes[:20],
        }

    add_check(
        report,
        "WX-POST-11",
        "COBERTURA_TEMPORAL",
        coverage_ok,
        CRITICAL,
        (
            f"Los {EXPECTED_MUNICIPALITIES} municipios tienen "
            f"{EXPECTED_DAYS} días en el periodo esperado."
            if coverage_ok
            else "La cobertura temporal municipal no cumple el contrato."
        ),
        "Revisar batches, fechas y claves duplicadas.",
        coverage_metrics,
    )

    # --------------------------------------------------------
    # WX-POST-12: comparación con las fuentes originales
    # --------------------------------------------------------

    comparison = {
        "batches_compared": 0,
        "batches_with_errors": [],
    }

    compare_columns = EXPECTED_SOURCE_COLUMNS.copy()

    can_compare = (
        batch_col is not None
        and set(compare_columns).issubset(df.columns)
        and bool(source_paths)
    )

    if can_compare:
        output_batch_ids = normalize_batch_id(df[batch_col])

        for number, source_path in sorted(source_paths.items()):
            batch_id = f"batch_{number:03d}"

            output_batch = df.loc[
                output_batch_ids == batch_id,
                compare_columns,
            ].copy()

            try:
                source = pd.read_csv(
                    source_path,
                    low_memory=False,
                    dtype={"municipality_code": "string"},
                )
                source.columns = source.columns.str.strip()

                # El código de extracción define el esquema fuente.
                missing_source = sorted(
                    set(compare_columns) - set(source.columns)
                )

                if missing_source:
                    comparison["batches_with_errors"].append({
                        "batch": batch_id,
                        "error": f"Faltan columnas fuente: {missing_source}",
                    })
                    continue

                source = source[compare_columns].copy()

                for frame in (source, output_batch):
                    frame["date"] = pd.to_datetime(
                        frame["date"],
                        errors="coerce",
                    ).dt.strftime("%Y-%m-%d")

                    frame["municipality_code"] = normalize_code(
                        frame["municipality_code"]
                    )

                    for column in NUMERIC_COLUMNS:
                        if column in frame.columns:
                            frame[column] = pd.to_numeric(
                                frame[column],
                                errors="coerce",
                            ).round(8)

                    frame["municipality_name"] = (
                        frame["municipality_name"]
                        .astype("string")
                        .str.strip()
                    )

                source = (
                    source.sort_values(
                        compare_columns,
                        na_position="last",
                    )
                    .reset_index(drop=True)
                )

                output_batch = (
                    output_batch.sort_values(
                        compare_columns,
                        na_position="last",
                    )
                    .reset_index(drop=True)
                )

                same = len(source) == len(output_batch)

                if same:
                    try:
                        pd.testing.assert_frame_equal(
                            source,
                            output_batch,
                            check_dtype=False,
                            check_exact=False,
                            atol=1e-8,
                            rtol=1e-8,
                        )
                    except AssertionError:
                        same = False

                comparison["batches_compared"] += 1

                if not same:
                    comparison["batches_with_errors"].append({
                        "batch": batch_id,
                        "source_rows": len(source),
                        "processed_rows": len(output_batch),
                        "error": "El contenido fuente y procesado difiere.",
                    })

            except Exception as exc:
                comparison["batches_with_errors"].append({
                    "batch": batch_id,
                    "error": str(exc),
                })

    else:
        comparison["batches_with_errors"].append({
            "error": (
                "No se puede comparar: faltan columnas contractuales, "
                "source_batch_id o batches fuente."
            ),
        })

    comparison_ok = (
        can_compare
        and comparison["batches_compared"] == len(source_paths)
        and not comparison["batches_with_errors"]
    )

    add_check(
        report,
        "WX-POST-12",
        "CONSISTENCIA_FUENTE_RESULTADO",
        comparison_ok,
        CRITICAL,
        (
            f"Se compararon {comparison['batches_compared']} batches "
            "sin diferencias."
            if comparison_ok
            else "La comparación entre fuente y resultado procesado falló."
        ),
        "Revisar la conservación de valores y la asignación de source_batch_id.",
        comparison,
    )

    # --------------------------------------------------------
    # WX-POST-13: Great Expectations
    # --------------------------------------------------------

    gx_result = validate_with_gx(df)
    report["great_expectations"] = gx_result

    gx_ok = gx_result["executed"] and gx_result["success"]

    add_check(
        report,
        "WX-POST-13",
        "GREAT_EXPECTATIONS",
        gx_ok,
        CRITICAL,
        (
            "Las expectativas básicas de Great Expectations se cumplieron."
            if gx_ok
            else str(gx_result["detail"])
        ),
        "Verificar la ejecución de GX y corregir las expectativas incumplidas.",
    )

    # --------------------------------------------------------
    # 5. ESTADO FINAL Y CÓDIGO DE SALIDA
    # --------------------------------------------------------

    report["metrics"] = {
        "processed_rows": len(df),
        "processed_columns": len(df.columns),
        "unique_municipalities": unique_municipalities,
        "source_batches_found": len(source_paths),
        "source_rows": source_rows,
        "minimum_date": dates.min(),
        "maximum_date": dates.max(),
    }

    critical_failures = [
        item for item in report["validations"]
        if item["estado"] == "ERROR"
        and item["riesgo"] == CRITICAL
    ]

    warning_failures = [
        item for item in report["validations"]
        if item["estado"] == "ADVERTENCIA"
    ]

    if critical_failures:
        report["status"] = "BLOQUEADO"
        exit_code = 1

    elif warning_failures:
        report["status"] = "REQUIERE_REVISION"
        exit_code = 0

    else:
        report["status"] = "APROBADO"
        exit_code = 0

    report["finished_at_utc"] = utc_now()

    return report, exit_code


# ============================================================
# 6. EJECUCIÓN
# ============================================================

def main() -> int:
    try:
        report, exit_code = ejecutar_validacion()

    except Exception as exc:
        logger.exception("Error no controlado en la validación")

        report = {
            "dataset": "weather",
            "stage": "post_transformation",
            "validator": "validate_weather_processed.py",
            "started_at_utc": utc_now(),
            "finished_at_utc": utc_now(),
            "status": "BLOQUEADO",
            "fatal_error": str(exc),
            "validations": [{
                "id": "WX-POST-FATAL",
                "categoria": "EJECUCION",
                "estado": "ERROR",
                "riesgo": CRITICAL,
                "detalle": str(exc),
                "accion_recomendada": (
                    "Revisar el error de ejecución antes de reintentar."
                ),
            }],
        }

        exit_code = 1

    try:
        report_path = write_report(report)
        logger.info("Reporte generado: %s", report_path)

    except Exception:
        logger.exception("No fue posible guardar el reporte JSON")
        exit_code = 1

    logger.info(
        "Estado final de validación: %s",
        report.get("status", "BLOQUEADO"),
    )
    logger.info("Código de salida: %s", exit_code)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())


from pathlib import Path
import logging
import re

import pandas as pd


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = PROJECT_ROOT / "data" / "raw" / "open_meteo_daily_batches"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

OUTPUT_FILE = OUTPUT_DIR / "weather_processed.csv"

TOTAL_BATCHES = 45
EXPECTED_BATCHES = set(range(1, TOTAL_BATCHES + 1))

START_DATE = pd.Timestamp("2019-01-01")
END_DATE = pd.Timestamp("2025-12-31")
EXPECTED_DAYS = (END_DATE - START_DATE).days + 1

# Expected variables from the extraction contract.
NUMERIC_COLUMNS = {
    "temperature_2m_min",
    "temperature_2m_mean",
    "temperature_2m_max",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
}

DATE_CANDIDATES = {"date", "time", "fecha"}
MUNICIPALITY_CODE_CANDIDATES = {
    "codigo_municipio",
    "municipality_code",
    "municipality_id",
    "cod_municipio",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def identificar_batch(path: Path) -> int | None:
    """Extract batch number from a filename such as batch_001.csv."""
    match = re.search(r"batch[_-]?(\d+)", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def buscar_columna(
    columns: list[str],
    candidates: set[str],
) -> str | None:
    """Find a column using case-insensitive matching."""
    by_lower = {column.lower().strip(): column for column in columns}

    for candidate in candidates:
        if candidate in by_lower:
            return by_lower[candidate]

    return None


def normalizar_fecha(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a recognized date column without dropping invalid rows."""
    date_column = buscar_columna(
        list(df.columns),
        DATE_CANDIDATES,
    )

    if date_column is None:
        logger.warning(
            "No recognized date column found. "
            "Date normalization was skipped."
        )
        return df

    parsed = pd.to_datetime(df[date_column], errors="coerce")

    invalid = int(
        df[date_column].notna().sum() - parsed.notna().sum()
    )

    if invalid:
        logger.warning(
            "Column '%s': %s non-empty values could not be parsed as dates.",
            date_column,
            invalid,
        )

    df[date_column] = parsed.dt.strftime("%Y-%m-%d")
    return df


def transformar_batch(path: Path) -> pd.DataFrame:
    """Read and standardize one batch while preserving its records."""
    batch_number = identificar_batch(path)

    if batch_number is None:
        raise ValueError(
            f"Could not identify batch number from filename: {path.name}"
        )

    logger.info("Reading %s", path.name)

    df = pd.read_csv(path, low_memory=False)
    original_rows = len(df)

    # Normalize whitespace in column names only.
    df.columns = [str(column).strip() for column in df.columns]

    if df.columns.duplicated().any():
        duplicated = df.columns[df.columns.duplicated()].tolist()
        raise ValueError(
            f"Duplicate column names in {path.name}: {duplicated}"
        )

    # Preserve an existing provenance field, if available.
    provenance_candidates = {
        "source_batch_id",
        "batch_id",
        "source_batch",
    }
    provenance_column = buscar_columna(
        list(df.columns),
        provenance_candidates,
    )

    if provenance_column is None:
        df["source_batch_id"] = f"batch_{batch_number:03d}"
    elif provenance_column != "source_batch_id":
        df = df.rename(
            columns={provenance_column: "source_batch_id"}
        )

    # Normalize dates when the source has a recognized date column.
    df = normalizar_fecha(df)

    # Normalize known weather variables to numeric types.
    for column in df.columns:
        if column.lower().strip() in NUMERIC_COLUMNS:
            original = df[column]
            converted = pd.to_numeric(original, errors="coerce")

            failed = int(
                original.notna().sum() - converted.notna().sum()
            )

            if failed:
                logger.warning(
                    "%s | %s: %s non-empty values failed numeric conversion.",
                    path.name,
                    column,
                    failed,
                )

            df[column] = converted

    # Do not silently remove, impute, or correct records.
    if len(df) != original_rows:
        raise RuntimeError(
            f"Unexpected row count change in {path.name}: "
            f"{original_rows} -> {len(df)}"
        )

    logger.info(
        "%s processed: %s rows, %s columns",
        path.name,
        len(df),
        len(df.columns),
    )

    return df


# ============================================================
# TRANSFORMATION AND CONSOLIDATION
# ============================================================

def ejecutar_transformacion() -> None:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(
            f"Input directory does not exist: {INPUT_DIR}"
        )

    files = sorted(INPUT_DIR.glob("weather_batch_*.csv"))

    if not files:
        raise FileNotFoundError(
            f"No batch CSV files found in {INPUT_DIR}"
        )

    batch_numbers = [identificar_batch(path) for path in files]

    if len(batch_numbers) != len(set(batch_numbers)):
        raise ValueError(
            "More than one CSV file was found for the same batch. "
            "Review the input directory before consolidation."
        )

    found_batches = set(batch_numbers)
    missing_batches = sorted(EXPECTED_BATCHES - found_batches)
    unexpected_batches = sorted(found_batches - EXPECTED_BATCHES)

    logger.info("Discovered %s batch files.", len(files))
    logger.info("Available batch numbers: %s", sorted(found_batches))

    if missing_batches or unexpected_batches:
        details = []

        if missing_batches:
            details.append(
                f"Missing batches: "
                f"{[f'{n:03d}' for n in missing_batches]}"
            )

        if unexpected_batches:
            details.append(
                f"Unexpected batches: {sorted(unexpected_batches)}"
            )

        raise ValueError(
            "Batch completeness check failed. "
            + "; ".join(details)
            + ". Transformation aborted."
        )

    if found_batches != EXPECTED_BATCHES:
        raise RuntimeError(
            "The discovered batches do not match the expected set. "
            "Transformation aborted."
        )

    logger.info(
        "Batch completeness verified: all %s expected batches are present.",
        TOTAL_BATCHES,
    )

    transformed_batches = [
        transformar_batch(path) for path in files
    ]

    # Do not silently accept schema differences between batches.
    reference_columns = set(transformed_batches[0].columns)

    for path, df in zip(files[1:], transformed_batches[1:]):
        current_columns = set(df.columns)

        if current_columns != reference_columns:
            missing_columns = sorted(reference_columns - current_columns)
            extra_columns = sorted(current_columns - reference_columns)

            raise ValueError(
                f"Schema mismatch in {path.name}. "
                f"Missing columns: {missing_columns}; "
                f"extra columns: {extra_columns}. "
                "Review the batches before consolidation."
            )

    # Align column order to the first batch.
    column_order = list(transformed_batches[0].columns)

    consolidated = pd.concat(
        [df[column_order] for df in transformed_batches],
        ignore_index=True,
    )

    # Basic coverage diagnostics; these do not modify the data.
    code_column = buscar_columna(
        list(consolidated.columns),
        MUNICIPALITY_CODE_CANDIDATES,
    )

    if code_column:
        municipality_count = consolidated[code_column].nunique(
            dropna=True
        )
        logger.info(
            "Distinct municipality codes in available batches: %s / %s",
            municipality_count,
            EXPECTED_BATCHES,
        )
    else:
        logger.warning(
            "No recognized municipality-code column found; "
            "municipality coverage could not be calculated."
        )

    date_column = buscar_columna(
        list(consolidated.columns),
        DATE_CANDIDATES,
    )

    if date_column:
        dates = pd.to_datetime(
            consolidated[date_column],
            errors="coerce",
        )
        logger.info(
            "Expected daily coverage: %s days per municipality, "
            "from %s through %s.",
            EXPECTED_DAYS,
            START_DATE.date(),
            END_DATE.date(),
        )
        logger.info(
            "Consolidated date range: %s to %s",
            dates.min(),
            dates.max(),
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    consolidated.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8",
    )

    logger.info("=" * 60)
    logger.info("TRANSFORMATION COMPLETED")
    logger.info(
    "Validated batch set: %s / %s",
    len(found_batches),
    TOTAL_BATCHES,
    )
    logger.info("Consolidated rows: %s", len(consolidated))
    logger.info("Output: %s", OUTPUT_FILE)
    logger.info(
        "Coverage status: %s",
        "COMPLETE" if not missing_batches else "PARTIAL",
    )


if __name__ == "__main__":
    ejecutar_transformacion()

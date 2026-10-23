
from pathlib import Path
import json
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd


# ---------------------------------------------------------
# Project paths
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "open_meteo_daily_batches"
)

REPORT_DIR = PROJECT_ROOT / "outputs" / "validation"

DIVIPOLA_FILE = PROJECT_ROOT / "data" / "source" / "divipola.csv"


# ---------------------------------------------------------
# Expected extraction configuration
# ---------------------------------------------------------

START_DATE = "2019-01-01"
END_DATE = "2025-12-31"

TOTAL_TERRITORIES = 1103
TOTAL_CATALOG_CODES = 1122
TOTAL_BATCHES = 45
BATCH_SIZE = 25
FINAL_BATCH_SIZE = TOTAL_TERRITORIES - (
    BATCH_SIZE * (TOTAL_BATCHES - 1)
)

EXPECTED_DATES = pd.date_range(
    start=START_DATE,
    end=END_DATE,
    freq="D",
)

DAYS_PER_TERRITORY = len(EXPECTED_DATES)

EXPECTED_TOTAL_ROWS = (
    TOTAL_TERRITORIES * DAYS_PER_TERRITORY
)

# Expected units from the Open-Meteo extraction contract.
# These units are documented here but cannot be verified from
# numeric CSV values alone.
EXPECTED_UNITS = {
    "temperature_2m_mean": "°C",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "precipitation_sum": "mm",
    "relative_humidity_2m_mean": "%",
    "et0_fao_evapotranspiration": "mm",
}


# ---------------------------------------------------------
# Expected input schema
# ---------------------------------------------------------

EXPECTED_COLUMNS = [
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

WEATHER_COLUMNS = list(EXPECTED_UNITS)

NUMERIC_COLUMNS = [
    "location_id",
    *WEATHER_COLUMNS,
    "latitude",
    "longitude",
]

KEY_COLUMNS = [
    "municipality_code",
    "date",
]


# ---------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Validation report
# ---------------------------------------------------------

def create_report() -> dict:
    """Create the summary for the current validation run."""
    return {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_directory": str(INPUT_DIR),
        "expected_period": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "days_per_municipality": DAYS_PER_TERRITORY,
        },
        "expected_extraction": {
            "municipalities": TOTAL_TERRITORIES,
            "batches": TOTAL_BATCHES,
            "records": EXPECTED_TOTAL_ROWS,
            "weather_units": EXPECTED_UNITS,
        },
        "batches_discovered": 0,
        "batches_validated": 0,
        "records_validated": 0,
        "municipalities_validated": 0,
        "missing_batches": [],
        "warnings": [],
        "batch_results": [],
        "status": "RUNNING",
        "error": None,
    }


def save_report(report: dict) -> Path:
    """Save the report as a timestamped JSON file."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    report_path = REPORT_DIR / (
        f"weather_validation_{timestamp}.json"
    )

    with report_path.open("w", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2,
        )

    logger.info("Validation report saved: %s", report_path)

    return report_path


def add_warning(report: dict, message: str) -> None:
    """Log a warning and include it in the report."""
    logger.warning(message)
    report["warnings"].append(message)


# ---------------------------------------------------------
# Batch discovery
# ---------------------------------------------------------

def discover_batches() -> list[Path]:
    """Discover available weather batch CSV files."""
    if not INPUT_DIR.is_dir():
        raise FileNotFoundError(
            f"Weather batch directory not found: {INPUT_DIR}"
        )

    batch_files = sorted(
        INPUT_DIR.glob("weather_batch_*.csv")
    )

    if not batch_files:
        raise FileNotFoundError(
            f"No weather batch CSV files found in {INPUT_DIR}"
        )

    return batch_files


# ---------------------------------------------------------
# Schema, types, coordinates, and keys
# ---------------------------------------------------------

def validate_schema_and_keys(path: Path) -> pd.DataFrame:
    """
    Validate schema, data types, finite numbers, municipality
    codes, coordinate ranges, and municipality-date uniqueness.
    """
    df = pd.read_csv(
        path,
        dtype={"municipality_code": "string"},
    )

    # DQ-API-00: Schema validation
    if df.columns.duplicated().any():
        raise ValueError(
            f"{path.name}: duplicate column names detected."
        )

    missing_columns = [
        column
        for column in EXPECTED_COLUMNS
        if column not in df.columns
    ]

    unexpected_columns = [
        column
        for column in df.columns
        if column not in EXPECTED_COLUMNS
    ]

    if missing_columns or unexpected_columns:
        raise ValueError(
            f"{path.name}: schema mismatch. "
            f"Missing columns: {missing_columns}. "
            f"Unexpected columns: {unexpected_columns}."
        )

    if df.empty:
        raise ValueError(
            f"{path.name}: batch contains no records."
        )

    # DQ-API-00: Date validation
    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce",
    )

    if df["date"].isna().any():
        raise ValueError(
            f"{path.name}: "
            f"{int(df['date'].isna().sum())} invalid or missing dates."
        )

    # DQ-API-00: Numeric type and finite-value validation
    for column in NUMERIC_COLUMNS:
        original = df[column]

        converted = pd.to_numeric(
            original,
            errors="coerce",
        )

        invalid = original.notna() & converted.isna()

        if invalid.any():
            raise ValueError(
                f"{path.name}: {int(invalid.sum())} invalid numeric "
                f"values in '{column}'."
            )

        non_finite = converted.notna() & ~np.isfinite(converted)

        if non_finite.any():
            raise ValueError(
                f"{path.name}: {int(non_finite.sum())} non-finite "
                f"numeric values in '{column}'."
            )

        df[column] = converted

    # DQ-API-00: location_id must be present and integer-like.
    if df["location_id"].isna().any():
        raise ValueError(
            f"{path.name}: location_id contains missing values."
        )

    non_integer_ids = df["location_id"] % 1 != 0

    if non_integer_ids.any():
        raise ValueError(
            f"{path.name}: location_id contains non-integer values."
        )

    # DQ-API-00: Municipality code format
    codes = df["municipality_code"].str.strip()

    invalid_codes = (
        codes.isna()
        | ~codes.str.fullmatch(r"\d{5}", na=False)
    )

    if invalid_codes.any():
        raise ValueError(
            f"{path.name}: "
            f"{int(invalid_codes.sum())} invalid municipality codes."
        )

    df["municipality_code"] = codes

    # DQ-API-00: Municipality name must be present.
    names = df["municipality_name"].astype("string").str.strip()

    invalid_names = names.isna() | names.eq("")

    if invalid_names.any():
        raise ValueError(
            f"{path.name}: {int(invalid_names.sum())} missing or "
            "empty municipality names."
        )

    df["municipality_name"] = names

    # DQ-GEO-02: Basic physical coordinate ranges.
    # This does not verify geographic accuracy.
    invalid_latitude = (
        df["latitude"].notna()
        & ~df["latitude"].between(-90, 90)
    )

    invalid_longitude = (
        df["longitude"].notna()
        & ~df["longitude"].between(-180, 180)
    )

    if invalid_latitude.any():
        raise ValueError(
            f"{path.name}: {int(invalid_latitude.sum())} latitude "
            "values outside -90 to 90."
        )

    if invalid_longitude.any():
        raise ValueError(
            f"{path.name}: {int(invalid_longitude.sum())} longitude "
            "values outside -180 to 180."
        )

    missing_coordinates = df[
        ["latitude", "longitude"]
    ].isna().any(axis=1)

    if missing_coordinates.any():
        raise ValueError(
            f"{path.name}: {int(missing_coordinates.sum())} records "
            "have missing latitude or longitude."
        )

    # DQ-API-01: Municipality-date uniqueness
    duplicated_keys = df.duplicated(
        subset=KEY_COLUMNS,
        keep=False,
    )

    if duplicated_keys.any():
        raise ValueError(
            f"{path.name}: {int(duplicated_keys.sum())} records "
            "participate in duplicate municipality-date keys."
        )

    logger.info(
        "%s: schema, types, finite numbers, municipality codes, "
        "coordinates, and keys passed.",
        path.name,
    )

    return df


# ---------------------------------------------------------
# Temporal coverage
# ---------------------------------------------------------

def validate_temporal_coverage(
    df: pd.DataFrame,
    path: Path,
) -> None:
    """Verify exact daily date coverage for every municipality."""
    expected_set = set(
        pd.DatetimeIndex(EXPECTED_DATES)
    )

    errors = []

    for municipality_code, group in df.groupby(
        "municipality_code",
        sort=False,
    ):
        actual_set = set(
            pd.DatetimeIndex(group["date"].unique())
        )

        missing_dates = expected_set - actual_set
        unexpected_dates = actual_set - expected_set

        if missing_dates or unexpected_dates:
            errors.append(
                f"{municipality_code}: "
                f"{len(missing_dates)} missing dates, "
                f"{len(unexpected_dates)} unexpected dates"
            )

    if errors:
        raise ValueError(
            f"{path.name}: temporal coverage failed for "
            f"{len(errors)} municipalities. "
            f"{'; '.join(errors[:5])}"
        )

    logger.info(
        "%s: temporal coverage passed for %s municipalities.",
        path.name,
        df["municipality_code"].nunique(),
    )


# ---------------------------------------------------------
# Weather domain validation
# ---------------------------------------------------------

def validate_weather_domains(
    df: pd.DataFrame,
    path: Path,
    report: dict,
) -> None:
    """
    Validate meteorological domains.

    Missing values are reported without imputation.
    Negative ET0 is reported pending confirmation of the
    source-variable contract.
    """

    # DQ-API-04: Temperature ordering is blocking.
    complete_temperatures = df[
        [
            "temperature_2m_min",
            "temperature_2m_mean",
            "temperature_2m_max",
        ]
    ].notna().all(axis=1)

    invalid_temperature_order = complete_temperatures & (
        (
            df["temperature_2m_min"]
            > df["temperature_2m_mean"]
        )
        | (
            df["temperature_2m_mean"]
            > df["temperature_2m_max"]
        )
    )

    if invalid_temperature_order.any():
        raise ValueError(
            f"{path.name}: "
            f"{int(invalid_temperature_order.sum())} records violate "
            "temperature ordering (min <= mean <= max)."
        )

    # DQ-API-05: Negative precipitation is blocking.
    negative_precipitation = (
        df["precipitation_sum"].notna()
        & (df["precipitation_sum"] < 0)
    )

    if negative_precipitation.any():
        raise ValueError(
            f"{path.name}: "
            f"{int(negative_precipitation.sum())} negative "
            "precipitation values."
        )

    # ET0: Report negative values pending contract confirmation.
    negative_et0 = (
        df["et0_fao_evapotranspiration"].notna()
        & (df["et0_fao_evapotranspiration"] < 0)
    )

    if negative_et0.any():
        add_warning(
            report,
            f"{path.name}: {int(negative_et0.sum())} negative values "
            "in et0_fao_evapotranspiration. Review against the "
            "confirmed Open-Meteo variable contract.",
        )

    # DQ-API-06: Relative humidity must be in [0, 100].
    humidity = df["relative_humidity_2m_mean"]

    invalid_humidity = (
        humidity.notna()
        & ~humidity.between(0, 100)
    )

    if invalid_humidity.any():
        raise ValueError(
            f"{path.name}: "
            f"{int(invalid_humidity.sum())} records have relative "
            "humidity outside 0-100%."
        )

    # DQ-API-07: Report missing weather values.
    # No imputation or automatic row deletion is performed.
    for column in WEATHER_COLUMNS:
        missing_count = int(df[column].isna().sum())

        if missing_count:
            add_warning(
                report,
                f"{path.name}: {missing_count} missing values in "
                f"{column} ({100 * missing_count / len(df):.4f}%).",
            )

    all_weather_missing = df[
        WEATHER_COLUMNS
    ].isna().all(axis=1)

    if all_weather_missing.any():
        add_warning(
            report,
            f"{path.name}: {int(all_weather_missing.sum())} records "
            "have all weather variables missing.",
        )

    logger.info(
        "%s: weather-domain checks completed.",
        path.name,
    )


# ---------------------------------------------------------
# DIVIPOLA reference validation
# ---------------------------------------------------------

def load_divipola_codes() -> set[str]:
    """
    Load and validate DIVIPOLA.

    Accept all territorial codes present in DIVIPOLA,
    regardless of territorial classification.
    """
    if not DIVIPOLA_FILE.is_file():
        raise FileNotFoundError(
            f"DIVIPOLA file not found: {DIVIPOLA_FILE}"
        )

    df = pd.read_csv(
        DIVIPOLA_FILE,
        dtype={"Código Municipio": "string"},
    )

    code_col = "Código Municipio"

    if code_col not in df.columns:
        raise ValueError(
            f"DIVIPOLA is missing required column: {code_col}"
        )

    df[code_col] = df[code_col].str.strip()

    if df[code_col].isna().any() or df[code_col].eq("").any():
        raise ValueError(
            "DIVIPOLA contains null or empty territorial codes."
        )

    invalid_codes = ~df[code_col].str.fullmatch(
        r"\d{5}",
        na=False,
    )

    if invalid_codes.any():
        examples = df.loc[
            invalid_codes,
            code_col,
        ].head(10).tolist()

        raise ValueError(
            f"DIVIPOLA contains invalid territorial codes: {examples}"
        )

    duplicated_codes = df.loc[
        df[code_col].duplicated(keep=False),
        code_col,
    ].unique()

    if len(duplicated_codes):
        raise ValueError(
            "DIVIPOLA contains duplicate territorial codes: "
            f"{duplicated_codes[:10].tolist()}"
        )

    if len(df) != TOTAL_CATALOG_CODES:
        raise ValueError(
            f"Unexpected DIVIPOLA row count: {len(df)}; "
            f"expected {TOTAL_CATALOG_CODES}."
        )

    catalog_codes = set(df[code_col].tolist())

    logger.info(
        "DIVIPOLA catalog validated: %s territorial codes.",
        len(catalog_codes),
    )

    logger.info(
        "All territorial classifications are included in the extraction scope."
    )

    return catalog_codes


def validate_municipality_reference(
    df: pd.DataFrame,
    path: Path,
    catalog_codes: set[str],
) -> set[str]:
    """Ensure codes belong to the 1,103 municipalities in scope."""
    batch_codes = set(
        df["municipality_code"].unique()
    )

    unknown_codes = batch_codes - catalog_codes

    if unknown_codes:
        raise ValueError(
            f"{path.name}: {len(unknown_codes)} municipality codes "
            "are outside the 1,103-municipality extraction scope: "
            f"{sorted(unknown_codes)[:10]}"
        )

    logger.info(
        "%s: all %s municipality codes belong to the extraction scope.",
        path.name,
        len(batch_codes),
    )

    return batch_codes


# ---------------------------------------------------------
# Cross-batch consistency
# ---------------------------------------------------------

def validate_batch_consistency(
    batch_codes: dict[int, set[str]],
    batch_rows: dict[int, int],
) -> None:
    """Validate batch territory counts, row counts, and overlaps."""
    seen_codes: set[str] = set()

    for batch_number in sorted(batch_codes):
        codes = batch_codes[batch_number]

        expected_territories = (
            FINAL_BATCH_SIZE
            if batch_number == TOTAL_BATCHES
            else BATCH_SIZE
        )

        if len(codes) != expected_territories:
            raise ValueError(
                f"Batch {batch_number:03d}: expected "
                f"{expected_territories} municipalities, "
                f"found {len(codes)}."
            )

        overlap = seen_codes & codes

        if overlap:
            raise ValueError(
                f"Batch {batch_number:03d}: {len(overlap)} municipalities "
                "also occur in earlier batches: "
                f"{sorted(overlap)[:10]}"
            )

        expected_rows = (
            expected_territories * DAYS_PER_TERRITORY
        )

        if batch_rows[batch_number] != expected_rows:
            raise ValueError(
                f"Batch {batch_number:03d}: expected "
                f"{expected_rows} records, "
                f"found {batch_rows[batch_number]}."
            )

        seen_codes.update(codes)

    logger.info(
        "Cross-batch consistency passed: %s unique municipalities "
        "across %s available batches.",
        len(seen_codes),
        len(batch_codes),
    )


# ---------------------------------------------------------
# Extraction completeness
# ---------------------------------------------------------

def validate_extraction_completeness(
    batch_numbers: set[int],
    batch_codes: dict[int, set[str]],
    batch_rows: dict[int, int],
    report: dict,
) -> str:
    """
    Return COMPLETE or PARTIAL.

    A partial extraction is not approved as complete.
    """
    expected_numbers = set(
        range(1, TOTAL_BATCHES + 1)
    )

    missing_batches = sorted(
        expected_numbers - batch_numbers
    )

    total_rows = sum(batch_rows.values())

    all_codes = (
        set().union(*batch_codes.values())
        if batch_codes
        else set()
    )

    report["missing_batches"] = missing_batches
    report["records_validated"] = total_rows
    report["municipalities_validated"] = len(all_codes)

    if missing_batches:
        add_warning(
            report,
            f"PARTIAL EXTRACTION: {len(batch_numbers)} of "
            f"{TOTAL_BATCHES} batches available. "
            f"Missing batches: {missing_batches}. "
            "Global completeness is not approved.",
        )

        return "PARTIAL"

    if len(all_codes) != TOTAL_TERRITORIES:
        raise ValueError(
            f"Expected {TOTAL_TERRITORIES} municipalities, "
            f"found {len(all_codes)}."
        )

    if total_rows != EXPECTED_TOTAL_ROWS:
        raise ValueError(
            f"Expected {EXPECTED_TOTAL_ROWS} records, "
            f"found {total_rows}."
        )

    logger.info(
        "COMPLETE EXTRACTION APPROVED: %s batches, "
        "%s municipalities, %s records.",
        len(batch_numbers),
        len(all_codes),
        total_rows,
    )

    return "COMPLETE"


# ---------------------------------------------------------
# Main execution
# ---------------------------------------------------------

def main() -> None:
    """Run batch-level and global weather data quality checks."""
    report = create_report()

    batch_numbers: set[int] = set()
    batch_codes: dict[int, set[str]] = {}
    batch_rows: dict[int, int] = {}

    try:
        batch_files = discover_batches()

        report["batches_discovered"] = len(batch_files)

        logger.info(
            "Discovered %s weather batch files.",
            len(batch_files),
        )

        logger.info(
            "Expected weather units from extraction contract: %s",
            EXPECTED_UNITS,
        )

        catalog_codes = load_divipola_codes()

        for path in batch_files:
            prefix = "weather_batch_"
            suffix = path.stem.removeprefix(prefix)

            if (
                not path.stem.startswith(prefix)
                or len(suffix) != 3
                or not suffix.isdigit()
            ):
                raise ValueError(
                    f"Invalid batch filename: {path.name}"
                )

            batch_number = int(suffix)

            if not 1 <= batch_number <= TOTAL_BATCHES:
                raise ValueError(
                    f"{path.name}: batch number must be between "
                    f"001 and {TOTAL_BATCHES:03d}."
                )

            if batch_number in batch_numbers:
                raise ValueError(
                    f"Duplicate batch number: {batch_number:03d}"
                )

            logger.info("Validating batch: %s", path.name)

            df = validate_schema_and_keys(path)

            validate_temporal_coverage(
                df,
                path,
            )

            validate_weather_domains(
                df,
                path,
                report,
            )

            codes = validate_municipality_reference(
                df,
                path,
                catalog_codes,
            )

            batch_numbers.add(batch_number)
            batch_codes[batch_number] = codes
            batch_rows[batch_number] = len(df)

            report["batches_validated"] = len(batch_numbers)
            report["records_validated"] = sum(batch_rows.values())
            report["municipalities_validated"] = len(
                set().union(*batch_codes.values())
            )

            report["batch_results"].append(
                {
                    "batch_file": path.name,
                    "batch_number": batch_number,
                    "status": "PASSED",
                    "records": len(df),
                    "municipalities": len(codes),
                    "date_min": df["date"].min().strftime("%Y-%m-%d"),
                    "date_max": df["date"].max().strftime("%Y-%m-%d"),
                }
            )

        validate_batch_consistency(
            batch_codes,
            batch_rows,
        )

        completeness_status = validate_extraction_completeness(
            batch_numbers,
            batch_codes,
            batch_rows,
            report,
        )

        if completeness_status == "PARTIAL":
            report["status"] = "PARTIAL"
        elif report["warnings"]:
            report["status"] = "COMPLETE_WITH_WARNINGS"
        else:
            report["status"] = "COMPLETE"

        logger.info(
            "Validation run finished with status %s: "
            "%s batches, %s records.",
            report["status"],
            len(batch_numbers),
            sum(batch_rows.values()),
        )

    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

        logger.exception(
            "Weather batch validation failed: %s",
            exc,
        )

        raise

    finally:
        report["finished_at_utc"] = (
            datetime.now(timezone.utc).isoformat()
        )

        try:
            save_report(report)
        except Exception:
            logger.exception(
                "Could not save the validation report."
            )


if __name__ == "__main__":
    main()

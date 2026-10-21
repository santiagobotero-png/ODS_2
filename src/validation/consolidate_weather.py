
from pathlib import Path
import logging
import os
import sys
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------------------------------------
# Project configuration
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "open_meteo_daily_batches"
)

OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

OUTPUT_FILE = OUTPUT_DIR / "weather_daily.parquet"
TEMP_FILE = OUTPUT_DIR / "weather_daily.tmp.parquet"

START_DATE = "2019-01-01"
END_DATE = "2025-12-31"

BATCH_SIZE = 25
TOTAL_TERRITORIES = 1103
TOTAL_BATCHES = 45

DAYS_PER_TERRITORY = len(
    pd.date_range(START_DATE, END_DATE, freq="D")
)

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

NUMERIC_COLUMNS = [
    "location_id",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
    "latitude",
    "longitude",
]

WEATHER_COLUMNS = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------

def validate_batch(
    path: Path,
    batch_number: int,
    previously_seen_codes: set[str],
) -> pd.DataFrame:

    df = pd.read_csv(
        path,
        dtype={"municipality_code": "string"},
    )

    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(
            f"{path.name}: unexpected column names or order."
        )

    df["municipality_code"] = (
        df["municipality_code"]
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(5)
    )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce",
    )

    if df["date"].isna().any():
        raise ValueError(
            f"{path.name}: invalid or missing dates."
        )

    # Parse numeric fields without silently accepting
    # malformed, non-empty values.
    for column in NUMERIC_COLUMNS:
        original = df[column]
        converted = pd.to_numeric(
            original,
            errors="coerce",
        )

        invalid = (
            original.notna()
            & original.astype("string").str.strip().ne("")
            & converted.isna()
        )

        if invalid.any():
            raise ValueError(
                f"{path.name}: invalid numeric values "
                f"in {column}."
            )

        df[column] = converted

    if df["municipality_code"].isna().any():
        raise ValueError(
            f"{path.name}: missing municipality codes."
        )

    if df["municipality_code"].str.fullmatch(r"\d{5}").eq(False).any():
        raise ValueError(
            f"{path.name}: malformed municipality codes."
        )

    expected_territories = (
        BATCH_SIZE
        if batch_number < TOTAL_BATCHES
        else TOTAL_TERRITORIES
        - BATCH_SIZE * (TOTAL_BATCHES - 1)
    )

    codes = set(df["municipality_code"].unique())

    if len(codes) != expected_territories:
        raise ValueError(
            f"{path.name}: expected {expected_territories} "
            f"territories, found {len(codes)}."
        )

    overlap = codes.intersection(previously_seen_codes)

    if overlap:
        raise ValueError(
            f"{path.name}: territories also found in "
            f"previous batches: {sorted(overlap)}"
        )

    if df["municipality_name"].isna().any():
        raise ValueError(
            f"{path.name}: missing municipality names."
        )

    # The coordinates must be numeric and geographically valid.
    if (
        df["latitude"].isna().any()
        or not df["latitude"].between(-90, 90).all()
        or df["longitude"].isna().any()
        or not df["longitude"].between(-180, 180).all()
    ):
        raise ValueError(
            f"{path.name}: invalid coordinates."
        )

    if df.duplicated(
        subset=["municipality_code", "date"]
    ).any():
        raise ValueError(
            f"{path.name}: duplicate municipality/date keys."
        )

    expected_dates = pd.date_range(
        START_DATE,
        END_DATE,
        freq="D",
    )

    for code, group in df.groupby("municipality_code"):
        if (
            len(group) != DAYS_PER_TERRITORY
            or not group["date"].sort_values().reset_index(drop=True).equals(
                pd.Series(expected_dates, name="date")
            )
        ):
            raise ValueError(
                f"{path.name}: incomplete or unexpected "
                f"daily coverage for municipality {code}."
            )

    expected_rows = (
        expected_territories * DAYS_PER_TERRITORY
    )

    if len(df) != expected_rows:
        raise ValueError(
            f"{path.name}: expected {expected_rows} rows, "
            f"found {len(df)}."
        )

    # Do not reject every missing weather value automatically:
    # missing observations must be measured and reported
    # as a separate data-quality metric.
    
    # -----------------------------------------------------
    # DQ-API-04: Temperature consistency
    # -----------------------------------------------------

    complete_temperatures = df[
        [
            "temperature_2m_min",
            "temperature_2m_mean",
            "temperature_2m_max",
        ]
    ].notna().all(axis=1)

    invalid_temperature_order = complete_temperatures & (
        (df["temperature_2m_min"] > df["temperature_2m_mean"])
        | (df["temperature_2m_mean"] > df["temperature_2m_max"])
    )

    if invalid_temperature_order.any():
        logger.warning(
            "%s: %s records violate temperature ordering.",
            path.name,
            int(invalid_temperature_order.sum()),
        )

    incomplete_temperatures = int((~complete_temperatures).sum())

    if incomplete_temperatures:
        logger.warning(
            "%s: %s records have incomplete temperature data.",
            path.name,
            incomplete_temperatures,
        )

    # -----------------------------------------------------
    # DQ-API-05: Non-negative precipitation and ET0
    # -----------------------------------------------------

    negative_precipitation = df["precipitation_sum"] < 0
    negative_et0 = df["et0_fao_evapotranspiration"] < 0

    if negative_precipitation.any():
        logger.warning(
            "%s: %s records have negative precipitation.",
            path.name,
            int(negative_precipitation.sum()),
        )

    if negative_et0.any():
        logger.warning(
            "%s: %s records have negative ET0.",
            path.name,
            int(negative_et0.sum()),
        )

    # -----------------------------------------------------
    # DQ-API-06: Relative humidity domain
    # -----------------------------------------------------

    humidity = df["relative_humidity_2m_mean"]

    invalid_humidity = humidity.notna() & ~humidity.between(0, 100)

    if invalid_humidity.any():
        raise ValueError(
            f"{path.name}: {int(invalid_humidity.sum())} "
            "records have relative humidity outside 0-100%."
        )

    # -----------------------------------------------------
    # DQ-API-07: Missing values by weather variable
    # -----------------------------------------------------

    for column in WEATHER_COLUMNS:
        missing_count = int(df[column].isna().sum())

        if missing_count:
            logger.warning(
                "%s: %s missing values in %s (%.4f%%).",
                path.name,
                missing_count,
                column,
                100 * missing_count / len(df),
            )

    all_missing = df[WEATHER_COLUMNS].isna().all(axis=1)

    if all_missing.any():
        logger.warning(
            "%s: %s rows have all weather variables missing.",
            path.name,
            int(all_missing.sum()),
        )

    
    # -----------------------------------------------------
    # DQ-API-08: Batch provenance
    # -----------------------------------------------------
    source_batch_id = path.stem

    expected_batch_id = f"weather_batch_{batch_number:03d}"

    if source_batch_id != expected_batch_id:
        raise ValueError(
            f"{path.name}: invalid source batch identifier. "
            f"Expected {expected_batch_id}, got {source_batch_id}."
        )

    # Persist the origin of every record.
    df["source_batch_id"] = source_batch_id

    logger.info(
        "%s: source_batch_id=%s.",
        path.name,
        source_batch_id,
    )



    previously_seen_codes.update(codes)

    logger.info(
        "%s validated: %s rows, %s territories.",
        path.name,
        len(df),
        len(codes),
    )

    return df


# ---------------------------------------------------------
# Consolidation
# ---------------------------------------------------------

def main() -> None:

    if len(sys.argv) > 1 and sys.argv[1] == "--validate-only":
        files = sorted(INPUT_DIR.glob("weather_batch_*.csv"))

        if not files:
            raise FileNotFoundError(
                f"No batch files found in {INPUT_DIR}"
            )

        seen_codes: set[str] = set()

        for path in files:
            # Extract the batch number from its filename.
            batch_number = int(
                path.stem.removeprefix("weather_batch_")
            )

            validate_batch(
                path,
                batch_number,
                seen_codes,
            )

        logger.info(
            "Validation-only completed: %s files, %s territories. "
            "No Parquet file was created or replaced.",
            len(files),
            len(seen_codes),
        )
        return


    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if TEMP_FILE.exists():
        TEMP_FILE.unlink()

    files = [
        INPUT_DIR / f"weather_batch_{number:03d}.csv"
        for number in range(1, TOTAL_BATCHES + 1)
    ]

    missing = [
        path.name
        for path in files
        if not path.is_file()
    ]

    if missing:
        raise FileNotFoundError(
            "Consolidation cancelled. Missing batch files: "
            + ", ".join(missing)
        )

    logger.info(
        "Found all %s batch files.",
        TOTAL_BATCHES,
    )

    seen_codes: set[str] = set()
    writer = None
    total_rows = 0

    try:
        for batch_number, path in enumerate(files, start=1):

            df = validate_batch(
                path,
                batch_number,
                seen_codes,
            )

            table = pa.Table.from_pandas(
                df[EXPECTED_COLUMNS],
                preserve_index=False,
            )

            if writer is None:
                writer = pq.ParquetWriter(
                    TEMP_FILE,
                    table.schema,
                    compression="snappy",
                )
            else:
                # Enforce the same schema across every batch.
                table = table.cast(writer.schema)

            writer.write_table(table)
            total_rows += len(df)

            logger.info(
                "Consolidation progress: %s/%s batches.",
                batch_number,
                TOTAL_BATCHES,
            )

        if writer is not None:
            writer.close()
            writer = None

        expected_total_rows = (
            TOTAL_TERRITORIES * DAYS_PER_TERRITORY
        )

        if (
            len(seen_codes) != TOTAL_TERRITORIES
            or total_rows != expected_total_rows
        ):
            raise ValueError(
                "Final validation failed: "
                f"{len(seen_codes)} territories and "
                f"{total_rows} rows."
            )

        # Publish only after every batch has passed validation.
        os.replace(TEMP_FILE, OUTPUT_FILE)

        logger.info(
            "Consolidation completed successfully."
        )
        logger.info(
            "Territories: %s",
            len(seen_codes),
        )
        logger.info(
            "Total rows: %s",
            total_rows,
        )
        logger.info(
            "Output: %s",
            OUTPUT_FILE,
        )

    except Exception:
        if writer is not None:
            writer.close()

        if TEMP_FILE.exists():
            TEMP_FILE.unlink()

        raise


if __name__ == "__main__":
    main()

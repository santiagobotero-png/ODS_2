
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests


# =========================================================
# 1. Configuration
# =========================================================

API_URL = "https://archive-api.open-meteo.com/v1/archive"

START_DATE = "2019-01-01"
END_DATE = "2025-12-31"

BATCH_SIZE = 25
TOTAL_TERRITORIES = 1103
TOTAL_BATCHES = 45

MAX_RETRIES = 3
REQUEST_TIMEOUT = 180
BASE_RETRY_DELAY = 30
BATCH_DELAY = 5

DAILY_VARIABLES = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
]

OUTPUT_COLUMNS = [
    "location_id",
    "date",
    *DAILY_VARIABLES,
    "municipality_code",
    "municipality_name",
    "latitude",
    "longitude",
]

EXPECTED_DATES = pd.date_range(
    start=START_DATE,
    end=END_DATE,
    freq="D",
)

EXPECTED_DAYS = len(EXPECTED_DATES)


# =========================================================
# 2. Project paths
# =========================================================

def find_project_root() -> Path:
    """Find the project root independently of the working directory."""

    candidates = [
        Path(__file__).resolve().parent,
        *Path(__file__).resolve().parents,
        Path.cwd().resolve(),
        *Path.cwd().resolve().parents,
    ]

    for candidate in candidates:
        if (
            (candidate / "data" / "source").is_dir()
            and (candidate / "src").is_dir()
        ):
            return candidate

    raise FileNotFoundError(
        "Project root not found. Expected directories "
        "'data/source/' and 'src/'."
    )


PROJECT_ROOT = find_project_root()

SOURCE_DIR = PROJECT_ROOT / "data" / "source"

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "open_meteo_daily_batches"
)

EVA_FILE = (
    SOURCE_DIR
    / "Evaluaciones_Agropecuarias_Municipales_–_EVA._2019_-_2025._Base_Agrícola_20260905.csv"
)

DIVIPOLA_FILE = SOURCE_DIR / "divipola.csv"


# =========================================================
# 3. Logging
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("open_meteo")


# =========================================================
# 4. General utilities
# =========================================================

def normalize_code(series: pd.Series) -> pd.Series:
    """Normalize DANE codes while preserving leading zeroes."""

    result = (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )

    result = result.mask(
        result.isna()
        | result.eq("")
        | result.str.lower().eq("nan")
    )

    return result.str.zfill(5)


def get_batch_path(batch_id: int) -> Path:
    """Return the CSV path for a batch."""

    return OUTPUT_DIR / f"weather_batch_{batch_id:03d}.csv"


# =========================================================
# 5. Load the municipality catalog
# =========================================================

def load_municipality_catalog() -> pd.DataFrame:
    """
    Build the weather catalog using EVA as the territory scope
    and DIVIPOLA as the geographic reference.
    """

    if not EVA_FILE.is_file():
        raise FileNotFoundError(f"EVA file not found: {EVA_FILE}")

    if not DIVIPOLA_FILE.is_file():
        raise FileNotFoundError(
            f"DIVIPOLA file not found: {DIVIPOLA_FILE}"
        )

    eva = pd.read_csv(
        EVA_FILE,
        dtype="string",
        low_memory=False,
    )

    divipola = pd.read_csv(
        DIVIPOLA_FILE,
        dtype="string",
        low_memory=False,
    )

    eva_code_col = "Código Dane municipio"

    divipola_code_col = "Código Municipio"
    department_code_col = "Código Departamento"
    department_name_col = "Nombre Departamento"
    municipality_name_col = "Nombre Municipio"
    territory_type_col = (
        "Tipo: Municipio / Isla / Área no municipalizada"
    )

    latitude_col = "Latitud"
    longitude_col = "longitud"

    required_eva = {eva_code_col}

    required_divipola = {
        divipola_code_col,
        department_code_col,
        department_name_col,
        municipality_name_col,
        territory_type_col,
        latitude_col,
        longitude_col,
    }

    missing_eva = required_eva - set(eva.columns)
    missing_divipola = required_divipola - set(divipola.columns)

    if missing_eva:
        raise ValueError(
            f"Missing EVA columns: {sorted(missing_eva)}"
        )

    if missing_divipola:
        raise ValueError(
            "Missing DIVIPOLA columns: "
            f"{sorted(missing_divipola)}"
        )

    eva_codes = normalize_code(eva[eva_code_col]).dropna()
    eva_codes = set(eva_codes.unique())

    divipola["municipality_code"] = normalize_code(
        divipola[divipola_code_col]
    )

    if divipola["municipality_code"].isna().any():
        raise ValueError(
            "DIVIPOLA contains missing municipality codes."
        )

    if divipola["municipality_code"].duplicated().any():
        duplicates = divipola.loc[
            divipola["municipality_code"].duplicated(
                keep=False
            ),
            "municipality_code",
        ].unique()

        raise ValueError(
            "Duplicate DIVIPOLA codes found: "
            f"{duplicates.tolist()[:20]}"
        )

    divipola[latitude_col] = pd.to_numeric(
        divipola[latitude_col]
        .astype("string")
        .str.strip()
        .str.replace(",", ".", regex=False),
        errors="coerce",
    )

    divipola[longitude_col] = pd.to_numeric(
        divipola[longitude_col]
        .astype("string")
        .str.strip()
        .str.replace(",", ".", regex=False),
        errors="coerce",
    )

    catalog = divipola.loc[
        divipola["municipality_code"].isin(eva_codes),
        [
            "municipality_code",
            department_code_col,
            department_name_col,
            municipality_name_col,
            territory_type_col,
            latitude_col,
            longitude_col,
        ],
    ].copy()

    catalog = catalog.rename(
        columns={
            department_code_col: "department_code",
            department_name_col: "department_name",
            municipality_name_col: "municipality_name",
            territory_type_col: "territory_type",
            latitude_col: "latitude",
            longitude_col: "longitude",
        }
    )

    missing_codes = eva_codes - set(
        catalog["municipality_code"]
    )

    if missing_codes:
        raise ValueError(
            "EVA codes missing from DIVIPOLA: "
            f"{sorted(missing_codes)[:20]}"
        )

    if catalog["municipality_code"].duplicated().any():
        raise ValueError(
            "Catalog contains duplicate municipality codes."
        )

    if catalog[
        ["latitude", "longitude"]
    ].isna().any().any():
        raise ValueError(
            "Catalog contains missing or invalid coordinates."
        )

    if not catalog["latitude"].between(-90, 90).all():
        raise ValueError("Latitude outside the valid range.")

    if not catalog["longitude"].between(-180, 180).all():
        raise ValueError("Longitude outside the valid range.")

    catalog = (
        catalog.sort_values("municipality_code")
        .reset_index(drop=True)
    )

    # The stable sort and batch assignment must remain consistent
    # with the previously validated batches.
    catalog["batch_id"] = (
        catalog.index // BATCH_SIZE
    ) + 1

    if len(catalog) != TOTAL_TERRITORIES:
        raise ValueError(
            f"Expected {TOTAL_TERRITORIES} territories; "
            f"found {len(catalog)}."
        )

    if catalog["batch_id"].max() != TOTAL_BATCHES:
        raise ValueError(
            f"Expected {TOTAL_BATCHES} batches; "
            f"found {catalog['batch_id'].max()}."
        )

    logger.info(
        "Catalog loaded: %s territories, %s batches.",
        len(catalog),
        catalog["batch_id"].nunique(),
    )

    return catalog


# =========================================================
# 6. Request historical weather data
# =========================================================

def request_weather_batch(
    batch: pd.DataFrame,
) -> list[dict]:
    """Request daily weather for all coordinates in a batch."""

    # Open-Meteo expects multiple coordinates as comma-separated
    # latitude and longitude parameters.
    params = {
        "latitude": ",".join(
            batch["latitude"].map(str).tolist()
        ),
        "longitude": ",".join(
            batch["longitude"].map(str).tolist()
        ),
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "UTC",
    }

    batch_id = int(batch["batch_id"].iloc[0])

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            # Retry rate limiting and temporary server errors.
            if response.status_code == 429:
                logger.error(
                    "Open-Meteo 429 response body: %s",
                    response.text,
                )
                logger.error(
                    "Open-Meteo response headers: %s",
                    dict(response.headers),
                )

            response.raise_for_status()

            payload = response.json()

            # The response for multiple coordinates should contain
            # one result per requested location.
            if not isinstance(payload, list):
                raise ValueError(
                    "Expected a list of responses for multiple "
                    "coordinates. Check the API response format."
                )

            if len(payload) != len(batch):
                raise ValueError(
                    f"Batch {batch_id:03d}: requested "
                    f"{len(batch)} locations but received "
                    f"{len(payload)} responses."
                )

            return payload

        except requests.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            retryable = status == 429 or (
                status is not None and 500 <= status < 600
            )

            if not retryable or attempt == MAX_RETRIES:
                logger.error(
                    "HTTP error for batch %03d: %s",
                    batch_id,
                    exc,
                )
                raise

            retry_after = (
                exc.response.headers.get("Retry-After")
                if exc.response is not None
                else None
            )

            try:
                delay = (
                    float(retry_after)
                    if retry_after is not None
                    else BASE_RETRY_DELAY * (2 ** (attempt - 1))
                )
            except ValueError:
                delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))

            logger.warning(
                "Batch %03d received HTTP %s. "
                "Retry %s/%s in %s seconds.",
                batch_id,
                status,
                attempt,
                MAX_RETRIES,
                delay,
            )

            time.sleep(delay)

        except (requests.RequestException, ValueError) as exc:
            if attempt == MAX_RETRIES:
                logger.error(
                    "Request failed for batch %03d: %s",
                    batch_id,
                    exc,
                )
                raise

            delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))

            logger.warning(
                "Batch %03d attempt %s/%s failed: %s. "
                "Retrying in %s seconds.",
                batch_id,
                attempt,
                MAX_RETRIES,
                exc,
                delay,
            )

            time.sleep(delay)

    raise RuntimeError(
        f"All request attempts failed for batch {batch_id:03d}."
    )


# =========================================================
# 7. Parse the API response
# =========================================================

def parse_weather_batch(
    batch: pd.DataFrame,
    responses: list[dict],
) -> pd.DataFrame:
    """Convert API responses into the project's flat CSV schema."""

    records = []

    for position, (_, municipality) in enumerate(
        batch.reset_index(drop=True).iterrows()
    ):
        response = responses[position]

        if "error" in response:
            raise ValueError(
                f"API error for "
                f"{municipality['municipality_code']}: "
                f"{response.get('reason', response['error'])}"
            )

        daily = response.get("daily")

        if not isinstance(daily, dict):
            raise ValueError(
                "Missing daily data for municipality "
                f"{municipality['municipality_code']}."
            )

        if "time" not in daily:
            raise ValueError(
                "Daily response does not contain the time field."
            )

        frame = pd.DataFrame(daily).rename(
            columns={"time": "date"}
        )

        required = {"date", *DAILY_VARIABLES}
        missing = required - set(frame.columns)

        if missing:
            raise ValueError(
                "Missing daily variables: "
                f"{sorted(missing)}"
            )

        frame["date"] = pd.to_datetime(
            frame["date"],
            errors="raise",
        )

        # location_id is the position within this API request,
        # not a municipality or DANE identifier.
        frame["location_id"] = position

        frame["municipality_code"] = (
            municipality["municipality_code"]
        )
        frame["municipality_name"] = (
            municipality["municipality_name"]
        )

        # Preserve the reference coordinates from DIVIPOLA.
        frame["latitude"] = municipality["latitude"]
        frame["longitude"] = municipality["longitude"]

        records.append(frame[OUTPUT_COLUMNS])

    result = pd.concat(records, ignore_index=True)

    validate_weather_batch(batch, result)

    return result


# =========================================================
# 8. Batch validation
# =========================================================

def validate_weather_batch(
    batch: pd.DataFrame,
    result: pd.DataFrame,
) -> None:
    """Check expected territories, daily coverage and key uniqueness."""

    missing_columns = set(OUTPUT_COLUMNS) - set(result.columns)

    if missing_columns:
        raise ValueError(
            f"Missing output columns: {sorted(missing_columns)}"
        )

    expected_codes = set(batch["municipality_code"])
    actual_codes = set(
        result["municipality_code"].dropna().unique()
    )

    if actual_codes != expected_codes:
        raise ValueError(
            "Municipality mismatch. "
            f"Missing: {sorted(expected_codes - actual_codes)}; "
            f"unexpected: {sorted(actual_codes - expected_codes)}."
        )

    if result["date"].isna().any():
        raise ValueError("Invalid or missing dates.")

    if result.duplicated(
        ["municipality_code", "date"]
    ).any():
        raise ValueError(
            "Duplicate municipality-date keys detected."
        )

    for code, group in result.groupby("municipality_code"):
        dates = pd.DatetimeIndex(
            group["date"]
        ).sort_values()

        if len(dates) != EXPECTED_DAYS:
            raise ValueError(
                f"{code}: expected {EXPECTED_DAYS} rows, "
                f"found {len(dates)}."
            )

        if not dates.equals(EXPECTED_DATES):
            raise ValueError(
                f"{code}: daily date series does not match "
                f"{START_DATE} through {END_DATE}."
            )

    if len(result) != len(batch) * EXPECTED_DAYS:
        raise ValueError(
            f"Expected {len(batch) * EXPECTED_DAYS:,} rows; "
            f"found {len(result):,}."
        )

    # All variables being missing is almost certainly an API issue.
    if result[DAILY_VARIABLES].isna().all(axis=None):
        raise ValueError(
            "All meteorological values are missing."
        )


# =========================================================
# 9. Validate existing batches before skipping
# =========================================================

def is_existing_batch_valid(
    path: Path,
    batch: pd.DataFrame,
) -> bool:
    """Return True only if an existing batch passes validation."""

    if not path.is_file():
        return False

    try:
        existing = pd.read_csv(
            path,
            dtype={"municipality_code": "string"},
        )

        if not set(OUTPUT_COLUMNS).issubset(existing.columns):
            raise ValueError("Unexpected CSV schema.")

        existing["municipality_code"] = normalize_code(
            existing["municipality_code"]
        )

        existing["date"] = pd.to_datetime(
            existing["date"],
            errors="coerce",
        )

        # Check codes, dates, counts and duplicate keys.
        validate_weather_batch(batch, existing)

        # Check that stored coordinates agree with the catalog.
        expected_geo = batch.set_index("municipality_code")[
            ["latitude", "longitude"]
        ]

        actual_geo = (
            existing.drop_duplicates("municipality_code")
            .set_index("municipality_code")[
                ["latitude", "longitude"]
            ]
        )

        actual_geo = actual_geo.reindex(expected_geo.index)

        if not actual_geo.index.equals(expected_geo.index):
            raise ValueError("Geographic codes do not match.")

        for column in ["latitude", "longitude"]:
            if not (
                pd.to_numeric(actual_geo[column], errors="coerce")
                .sub(expected_geo[column])
                .abs()
                .lt(1e-5)
                .all()
            ):
                raise ValueError(
                    f"Stored {column} differs from the catalog."
                )

        logger.info(
            "%s exists and passed validation; skipping.",
            path.name,
        )

        return True

    except Exception as exc:
        # Do not overwrite a file automatically if it exists
        # but fails validation. Investigate it first.
        raise RuntimeError(
            f"Existing batch {path.name} failed validation. "
            "It was not overwritten. "
            f"Details: {exc}"
        ) from exc


# =========================================================
# 10. Save CSV atomically
# =========================================================

def save_batch(
    result: pd.DataFrame,
    path: Path,
) -> None:
    """Write a temporary file and then atomically replace the target."""

    temporary_path = path.with_suffix(".tmp")

    try:
        result.to_csv(
            temporary_path,
            index=False,
            encoding="utf-8",
            date_format="%Y-%m-%d",
        )

        os.replace(temporary_path, path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    logger.info(
        "Saved %s records to %s.",
        f"{len(result):,}",
        path,
    )


# =========================================================
# 11. Execute selected batches
# =========================================================

def run_extraction(
    start_batch: int,
    end_batch: int,
) -> None:
    """Run extraction for a selected inclusive batch range."""

    if not 1 <= start_batch <= TOTAL_BATCHES:
        raise ValueError(
            f"start_batch must be between 1 and {TOTAL_BATCHES}."
        )

    if not 1 <= end_batch <= TOTAL_BATCHES:
        raise ValueError(
            f"end_batch must be between 1 and {TOTAL_BATCHES}."
        )

    if start_batch > end_batch:
        raise ValueError(
            "start_batch cannot exceed end_batch."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog = load_municipality_catalog()

    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("Output directory: %s", OUTPUT_DIR)
    logger.info("Date range: %s to %s", START_DATE, END_DATE)
    logger.info("Days per territory: %s", EXPECTED_DAYS)
    logger.info(
        "Selected batches: %03d through %03d",
        start_batch,
        end_batch,
    )

    for batch_id in range(start_batch, end_batch + 1):
        batch = catalog.loc[
            catalog["batch_id"].eq(batch_id)
        ].copy()

        if batch.empty:
            raise ValueError(
                f"Batch {batch_id:03d} has no territories."
            )

        output_path = get_batch_path(batch_id)

        logger.info(
            "Batch %03d: %s territories; expected rows: %s.",
            batch_id,
            len(batch),
            f"{len(batch) * EXPECTED_DAYS:,}",
        )

        if is_existing_batch_valid(output_path, batch):
            continue

        logger.info(
            "Requesting weather data for batch %03d.",
            batch_id,
        )

        responses = request_weather_batch(batch)
        result = parse_weather_batch(batch, responses)

        # Recheck before writing in case a file appeared meanwhile.
        if output_path.exists():
            raise FileExistsError(
                f"{output_path.name} appeared during extraction. "
                "The file was not overwritten."
            )

        save_batch(result, output_path)

        logger.info(
            "Batch %03d completed successfully.",
            batch_id,
        )

        if batch_id < end_batch:
            time.sleep(BATCH_DELAY)

    logger.info("Selected extraction range completed.")


# =========================================================
# 12. Command-line interface
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract daily historical weather from Open-Meteo "
            "for the EVA municipality catalog."
        )
    )

    parser.add_argument(
        "--batch",
        type=int,
        help="Run a single batch, e.g. --batch 4.",
    )

    parser.add_argument(
        "--start-batch",
        type=int,
        default=1,
        help="First batch, inclusive. Default: 1.",
    )

    parser.add_argument(
        "--end-batch",
        type=int,
        default=TOTAL_BATCHES,
        help="Last batch, inclusive. Default: 45.",
    )

    args = parser.parse_args()

    if args.batch is not None:
        start_batch = args.batch
        end_batch = args.batch
    else:
        start_batch = args.start_batch
        end_batch = args.end_batch

    run_extraction(start_batch, end_batch)


if __name__ == "__main__":
    main()

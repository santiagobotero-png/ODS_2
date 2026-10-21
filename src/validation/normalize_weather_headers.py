from pathlib import Path
import pandas as pd
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "raw" / "open_meteo_daily_batches"

BATCHES_TO_NORMALIZE = [1, 2, 3]

COLUMN_MAP = {
    "time": "date",
    "temperature_2m_mean (°C)": "temperature_2m_mean",
    "temperature_2m_max (°C)": "temperature_2m_max",
    "temperature_2m_min (°C)": "temperature_2m_min",
    "precipitation_sum (mm)": "precipitation_sum",
    "relative_humidity_2m_mean (%)": "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration (mm)": "et0_fao_evapotranspiration",
    "Código Dane municipio": "municipality_code",
    "Municipio": "municipality_name",
    "Latitud": "latitude",
    "longitud": "longitude",
}

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


def main():
    for batch_number in BATCHES_TO_NORMALIZE:
        filename = f"weather_batch_{batch_number:03d}.csv"
        path = DATA_DIR / filename
        backup_path = path.with_suffix(".csv.bak")

        if not path.exists():
            raise FileNotFoundError(f"Missing batch: {path}")

        if backup_path.exists():
            raise FileExistsError(
                f"Backup already exists: {backup_path}. "
                "Review it before proceeding."
            )

        df = pd.read_csv(path, encoding="utf-8-sig")

        if list(df.columns) == EXPECTED_COLUMNS:
            print(f"{filename}: already normalized; no changes.")
            continue

        renamed = df.rename(columns=COLUMN_MAP)

        if list(renamed.columns) != EXPECTED_COLUMNS:
            raise ValueError(
                f"Unexpected schema in {filename}.\n"
                f"Actual: {list(renamed.columns)}\n"
                f"Expected: {EXPECTED_COLUMNS}"
            )

        # Preserve the original file before changing it.
        shutil.copy2(path, backup_path)

        # Write to a temporary file first.
        temp_path = path.with_suffix(".csv.tmp")
        renamed.to_csv(temp_path, index=False, encoding="utf-8")

        # Verify the temporary output before replacing the original.
        check = pd.read_csv(temp_path, encoding="utf-8")
        if list(check.columns) != EXPECTED_COLUMNS:
            temp_path.unlink(missing_ok=True)
            raise ValueError(f"Output verification failed: {filename}")

        if len(check) != len(df):
            temp_path.unlink(missing_ok=True)
            raise ValueError(f"Row count changed: {filename}")

        temp_path.replace(path)

        print(
            f"{filename}: normalized successfully; "
            f"{len(df):,} rows; backup: {backup_path.name}"
        )


if __name__ == "__main__":
    main()

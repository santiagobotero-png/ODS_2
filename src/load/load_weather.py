
from pathlib import Path
import logging
import os

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ARCHIVO_ORIGEN = (
    PROJECT_ROOT / "data" / "processed" / "weather_processed.csv"
)

BATCH_SIZE = 5000

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre",
    "diciembre",
]

DIAS = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def conectar():
    """Conexión a la base de datos de negocio usando variables de entorno."""
    return psycopg2.connect(
        host=os.environ["ETL_DB_HOST"],
        port=os.environ["ETL_DB_PORT"],
        dbname=os.environ["ETL_DB_NAME"],
        user=os.environ["ETL_DB_USER"],
        password=os.environ["ETL_DB_PASSWORD"],
    )


def cargar_csv():
    """Lee el CSV conservando los códigos municipales como texto."""
    if not ARCHIVO_ORIGEN.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo: {ARCHIVO_ORIGEN}"
        )

    df = pd.read_csv(
        ARCHIVO_ORIGEN,
        dtype={
            "municipality_code": "string",
            "municipality_name": "string",
            "source_batch_id": "string",
        },
    )

    df["date"] = pd.to_datetime(
        df["date"], errors="raise"
    ).dt.date

    return df


def ejecutar_lotes(cursor, sql, registros):
    """Inserta o actualiza registros por lotes."""
    total = 0

    for inicio in range(0, len(registros), BATCH_SIZE):
        lote = registros[inicio:inicio + BATCH_SIZE]

        execute_values(
            cursor,
            sql,
            lote,
            page_size=1000,
        )
        total += len(lote)

    return total


def cargar_dim_geografia(cursor, df):
    """Carga los municipios y sus coordenadas."""
    geografia = (
        df[
            [
                "municipality_code",
                "municipality_name",
                "latitude",
                "longitude",
            ]
        ]
        .drop_duplicates(subset=["municipality_code"])
        .sort_values("municipality_code")
    )

    registros = [
        (
            None,  # codigo_departamento: no disponible en el CSV
            None,  # departamento: no disponible en el CSV
            str(row.municipality_code),
            str(row.municipality_name),
            row.latitude,
            row.longitude,
        )
        for row in geografia.itertuples(index=False)
    ]

    sql = """
        INSERT INTO weather.dim_geografia (
            codigo_departamento,
            departamento,
            codigo_municipio,
            municipio,
            latitud,
            longitud
        )
        VALUES %s
        ON CONFLICT (codigo_municipio)
        DO UPDATE SET
            municipio = EXCLUDED.municipio,
            latitud = EXCLUDED.latitud,
            longitud = EXCLUDED.longitud
    """

    ejecutar_lotes(cursor, sql, registros)

    cursor.execute("""
        SELECT codigo_municipio, sk_geografia
        FROM weather.dim_geografia
    """)

    claves = {
        codigo: sk
        for codigo, sk in cursor.fetchall()
    }

    logger.info(
        "Dimensión geográfica procesada: %s municipios.",
        len(registros),
    )

    return claves


def cargar_dim_fecha(cursor, df):
    """Carga el calendario con nombres deterministas en español."""
    fechas = sorted(df["date"].unique())

    registros = []

    for fecha in fechas:
        fecha = pd.Timestamp(fecha)

        registros.append(
            (
                fecha.date(),
                fecha.year,
                fecha.month,
                MESES[fecha.month - 1],
                fecha.quarter,
                fecha.day,
                fecha.weekday() + 1,
                DIAS[fecha.weekday()],
                fecha.weekday() >= 5,
            )
        )

    sql = """
        INSERT INTO weather.dim_fecha (
            fecha,
            anio,
            mes,
            nombre_mes,
            trimestre,
            dia,
            dia_semana,
            nombre_dia,
            es_fin_de_semana
        )
        VALUES %s
        ON CONFLICT (fecha)
        DO UPDATE SET
            anio = EXCLUDED.anio,
            mes = EXCLUDED.mes,
            nombre_mes = EXCLUDED.nombre_mes,
            trimestre = EXCLUDED.trimestre,
            dia = EXCLUDED.dia,
            dia_semana = EXCLUDED.dia_semana,
            nombre_dia = EXCLUDED.nombre_dia,
            es_fin_de_semana = EXCLUDED.es_fin_de_semana
    """

    ejecutar_lotes(cursor, sql, registros)

    cursor.execute("""
        SELECT fecha, sk_fecha
        FROM weather.dim_fecha
    """)

    claves = {
        fecha: sk
        for fecha, sk in cursor.fetchall()
    }

    logger.info(
        "Dimensión temporal procesada: %s fechas.",
        len(registros),
    )

    return claves


def cargar_fact_clima(cursor, df, claves_geo, claves_fecha):
    """Carga hechos diarios y actualiza las claves naturales existentes."""
    columnas = [
        "municipality_code",
        "date",
        "temperature_2m_mean",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "relative_humidity_2m_mean",
        "et0_fao_evapotranspiration",
        "source_batch_id",
    ]

    registros = []

    sql = """
        INSERT INTO weather.fact_clima_diario (
            sk_geografia,
            sk_fecha,
            temperatura_media,
            temperatura_maxima,
            temperatura_minima,
            precipitacion,
            humedad_relativa_media,
            evapotranspiracion_et0,
            source_batch_id
        )
        VALUES %s
        ON CONFLICT (sk_geografia, sk_fecha)
        DO UPDATE SET
            temperatura_media = EXCLUDED.temperatura_media,
            temperatura_maxima = EXCLUDED.temperatura_maxima,
            temperatura_minima = EXCLUDED.temperatura_minima,
            precipitacion = EXCLUDED.precipitacion,
            humedad_relativa_media =
                EXCLUDED.humedad_relativa_media,
            evapotranspiracion_et0 =
                EXCLUDED.evapotranspiracion_et0,
            source_batch_id = EXCLUDED.source_batch_id
    """

    total = 0

    for row in df[columnas].itertuples(index=False, name=None):
        (
            codigo,
            fecha,
            temp_media,
            temp_max,
            temp_min,
            precipitacion,
            humedad,
            et0,
            batch_id,
        ) = row

        codigo = str(codigo)
        fecha = fecha

        if codigo not in claves_geo:
            raise ValueError(
                f"No existe código municipal en dim_geografia: {codigo}"
            )

        if fecha not in claves_fecha:
            raise ValueError(
                f"No existe fecha en dim_fecha: {fecha}"
            )

        valores = [
            temp_media,
            temp_max,
            temp_min,
            precipitacion,
            humedad,
            et0,
        ]

        valores = [
            None if pd.isna(v) else float(v)
            for v in valores
        ]

        registros.append(
            (
                claves_geo[codigo],
                claves_fecha[fecha],
                *valores,
                str(batch_id),
            )
        )

        if len(registros) >= BATCH_SIZE:
            execute_values(cursor, sql, registros, page_size=1000)
            total += len(registros)
            registros.clear()

            if total % 50000 == 0:
                logger.info(
                    "Hechos procesados: %s registros.", total
                )

    if registros:
        execute_values(cursor, sql, registros, page_size=1000)
        total += len(registros)

    logger.info(
        "Hechos procesados: %s registros.", total
    )

    return total


def main():
    logger.info("Iniciando carga de Weather.")

    df = cargar_csv()
    logger.info("Archivo leído: %s registros.", len(df))

    conn = conectar()

    try:
        with conn:
            with conn.cursor() as cursor:
                claves_geo = cargar_dim_geografia(cursor, df)
                claves_fecha = cargar_dim_fecha(cursor, df)

                total_hechos = cargar_fact_clima(
                    cursor,
                    df,
                    claves_geo,
                    claves_fecha,
                )

                cursor.execute("""
                    SELECT
                        (SELECT COUNT(*)
                         FROM weather.dim_geografia),
                        (SELECT COUNT(*)
                         FROM weather.dim_fecha),
                        (SELECT COUNT(*)
                         FROM weather.fact_clima_diario)
                """)

                municipios, fechas, hechos = cursor.fetchone()

                logger.info(
                    "Conteos dentro de la transacción: "
                    "geografía=%s, fechas=%s, hechos=%s.",
                    municipios,
                    fechas,
                    hechos,
                )

                if total_hechos != len(df):
                    raise RuntimeError(
                        "El número de hechos procesados no coincide "
                        "con el número de filas de origen."
                    )

        logger.info("Transacción confirmada correctamente.")
        logger.info("Carga de Weather finalizada.")

    except Exception:
        logger.exception(
            "Falló la carga. La transacción será revertida."
        )
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()

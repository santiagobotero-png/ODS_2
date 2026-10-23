
from datetime import timedelta
from pathlib import Path
import logging
import subprocess
import sys

import pendulum
from airflow.sdk import dag, task


PROJECT_ROOT = Path("/opt/airflow/project")
PYTHON = sys.executable

logger = logging.getLogger(__name__)


def ejecutar_script(ruta_relativa: str) -> None:
    ruta_script = PROJECT_ROOT / ruta_relativa

    if not ruta_script.is_file():
        raise FileNotFoundError(
            f"No se encontró el script requerido: {ruta_script}"
        )

    logger.info("Iniciando script: %s", ruta_script)

    subprocess.run(
        [PYTHON, str(ruta_script)],
        cwd=str(PROJECT_ROOT),
        check=True,
    )

    logger.info("Script finalizado correctamente: %s", ruta_script)


@dag(
    dag_id="dag_weather_open_meteo",
    description="Pipeline ETL de datos meteorológicos de Open-Meteo",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "etl",
        "retries": 0,
        "execution_timeout": timedelta(hours=12),
    },
    tags=["ETL", "Weather", "Open-Meteo", "Clima"],
)
def dag_weather_open_meteo():

    @task
    def extraer_weather():
        ejecutar_script("src/extraction/open_meteo.py")

    @task
    def validar_batches_weather():
        ejecutar_script("src/validation/validate_weather_batches.py")

    @task
    def transformar_weather():
        ejecutar_script("src/transformation/transform_weather.py")

    @task
    def validar_weather_procesado():
        ejecutar_script("src/validation/validate_weather_processed.py")

    @task
    def cargar_weather():
        ejecutar_script("src/load/load_weather.py")

    tarea_extraccion = extraer_weather()
    tarea_validacion_batches = validar_batches_weather()
    tarea_transformacion = transformar_weather()
    tarea_validacion_procesado = validar_weather_procesado()
    tarea_carga = cargar_weather()

    tarea_extraccion >> tarea_validacion_batches
    tarea_validacion_batches >> tarea_transformacion
    tarea_transformacion >> tarea_validacion_procesado
    tarea_validacion_procesado >> tarea_carga


dag_weather_open_meteo()

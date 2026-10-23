
from datetime import timedelta
import logging
import subprocess
import sys
from pathlib import Path
import pendulum
from airflow.sdk import dag, task


PROJECT_ROOT = Path("/opt/airflow/project")
PYTHON = sys.executable

logger = logging.getLogger(__name__)


def run_script(script_path: str, *args: str) -> None:
    """Ejecuta un script y registra su salida en los logs de Airflow."""
    full_path = PROJECT_ROOT / script_path

    if not full_path.is_file():
        raise FileNotFoundError(f"No existe el script: {full_path}")

    command = [PYTHON, str(full_path), *args]
    logger.info("Ejecutando comando: %s", command)

    # La salida se transmite a los logs de la tarea de Airflow.
    # Si el script falla, check=True genera una excepción.
    subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=True,
    )

    logger.info("Script finalizado correctamente: %s", script_path)


@dag(
    dag_id="etl_ods2_pipeline",
    description=(
        "Orquestación ETL de EVA agrícola y datos meteorológicos "
        "de Open-Meteo."
    ),
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "etl",
        "retries": 0,
        "execution_timeout": timedelta(hours=12),
    },
    tags=["ETL", "EVA", "Weather", "PostgreSQL"],
)
def etl_ods2_pipeline():

    # ========================================================
    # RAMA EVA
    # ========================================================

    @task
    def extraer_eva():
        run_script("src/extraction/eva.py")

    @task
    def validar_eva_staging():
        run_script("src/validation/validate_eva.py")
    
    @task
    def transformar_eva():
        run_script("src/transformation/transformacion_eva.py")

    @task
    def validar_eva_procesado():
        run_script("src/validation/validate_eva_processed.py")

    @task
    def cargar_eva():
        run_script("src/load/load_eva.py")

    # ========================================================
    # RAMA WEATHER
    # ========================================================

    @task
    def extraer_weather():
        run_script("src/extraction/open_meteo.py")

    @task
    def validar_batches_weather():
        run_script("src/validation/validate_weather_batches.py")

    @task
    def transformar_weather():
        run_script("src/transformation/transform_weather.py")

    @task
    def validar_weather_procesado():
        run_script("src/validation/validate_weather_processed.py")

    @task
    def cargar_weather():
        run_script("src/load/load_weather.py")

    # ========================================================
    # DEFINICIÓN DE LAS DEPENDENCIAS
    # ========================================================

    # EVA: extracción -> validación staging -> transformación
    # -> validación procesado -> carga.
    eva_extraido = extraer_eva()
    eva_staging_validado = validar_eva_staging()
    eva_transformado = transformar_eva()
    eva_validado = validar_eva_procesado()
    eva_cargado = cargar_eva()

    (
        eva_extraido
        >> eva_staging_validado
        >> eva_transformado
        >> eva_validado
        >> eva_cargado
    )

    # WEATHER: extracción -> validación batches ->
    # transformación -> validación procesado -> carga.
    weather_extraido = extraer_weather()
    weather_batches_validados = validar_batches_weather()
    weather_transformado = transformar_weather()
    weather_validado = validar_weather_procesado()
    weather_cargado = cargar_weather()

    (
        weather_extraido
        >> weather_batches_validados
        >> weather_transformado
        >> weather_validado
        >> weather_cargado
    )

    # Dependencia de integración:
    # Weather solo puede cargar después de que EVA haya
    # inicializado y cargado la dimensión geográfica compartida.
    eva_cargado >> weather_cargado


etl_ods2_pipeline()

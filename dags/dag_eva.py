from datetime import timedelta
from pathlib import Path
import logging
import subprocess
import sys

import pendulum
from airflow.sdk import dag, task


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path("/opt/airflow/project")
PYTHON = sys.executable

logger = logging.getLogger(__name__)


# ============================================================
# FUNCIÓN AUXILIAR
# ============================================================

def ejecutar_script(ruta_relativa: str) -> None:
    """
    Ejecuta un script Python existente dentro del contenedor
    de Airflow y propaga cualquier error al orquestador.
    """
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


# ============================================================
# DAG EVA AGRÍCOLA
# ============================================================

@dag(
    dag_id="dag_eva_agricola",
    description="Pipeline ETL del dataset EVA agrícola",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "etl",
        "retries": 0,
        "execution_timeout": timedelta(hours=2),
    },
    tags=["ETL", "EVA", "Agricultura"],
)
def dag_eva_agricola():

    # --------------------------------------------------------
    # 1. EXTRACCIÓN
    # --------------------------------------------------------

    @task
    def extraer_eva():
        ejecutar_script("src/extraction/eva.py")

    # --------------------------------------------------------
    # 2. TRANSFORMACIÓN
    # --------------------------------------------------------

    @task
    def transformar_eva():
        ejecutar_script("src/transformation/transformacion_eva.py")

    # --------------------------------------------------------
    # 3. VALIDACIÓN DEL DATASET PROCESADO
    # --------------------------------------------------------

    @task
    def validar_eva_procesado():
        ejecutar_script("src/validation/validate_eva_processed.py")

    # --------------------------------------------------------
    # 4. CARGA AL DATA WAREHOUSE
    # --------------------------------------------------------

    @task
    def cargar_eva():
        ejecutar_script("src/load/load_eva.py")

    # --------------------------------------------------------
    # DEPENDENCIAS
    # --------------------------------------------------------

    tarea_extraccion = extraer_eva()
    tarea_transformacion = transformar_eva()
    tarea_validacion = validar_eva_procesado()
    tarea_carga = cargar_eva()

    tarea_extraccion >> tarea_transformacion
    tarea_transformacion >> tarea_validacion
    tarea_validacion >> tarea_carga


# Registrar el DAG
dag_eva_agricola()
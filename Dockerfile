FROM apache/airflow:3.1.8

ARG AIRFLOW_VERSION=3.1.8

USER airflow

COPY requirements-airflow.txt /requirements-airflow.txt

RUN PYTHON_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
    && pip install --no-cache-dir \
       --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
       -r /requirements-airflow.txt
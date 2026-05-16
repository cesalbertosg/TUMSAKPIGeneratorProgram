"""Cliente PostgreSQL para la BD de cédulas (172.17.1.4 / cedula_direccion).

Lee credenciales desde `.env`. Expone un context manager `get_connection()`
que abre y cierra la conexión de forma determinista.

La conexión NO se mantiene abierta entre llamadas — cada pipeline corre una
sola query principal contra esta BD, así que un pool sería overkill.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2

from kpi_generator.config import Config


class PostgresConnectionError(RuntimeError):
    """Falla al conectar a Postgres — usa este error para fallback decisions."""


@contextmanager
def get_connection() -> Iterator[psycopg2.extensions.connection]:
    """Abre una conexión a la BD Cédula DG y la cierra al salir del context.

    Levanta `PostgresConnectionError` si las credenciales están incompletas
    o si la conexión falla (timeout, host inalcanzable, auth, etc.).
    """
    if not Config.PG_CEDULA_USER or not Config.PG_CEDULA_PASSWORD:
        raise PostgresConnectionError(
            "Credenciales Postgres incompletas. Configurar PG_CEDULA_USER y "
            "PG_CEDULA_PASSWORD en .env"
        )

    conn = None
    try:
        conn = psycopg2.connect(
            host=Config.PG_CEDULA_HOST,
            port=Config.PG_CEDULA_PORT,
            dbname=Config.PG_CEDULA_DB,
            user=Config.PG_CEDULA_USER,
            password=Config.PG_CEDULA_PASSWORD,
            connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "10")),
        )
    except psycopg2.OperationalError as e:
        raise PostgresConnectionError(
            f"No se pudo conectar a {Config.PG_CEDULA_HOST}:{Config.PG_CEDULA_PORT}/"
            f"{Config.PG_CEDULA_DB}. ¿VPN activa? Error original: {e}"
        ) from e

    try:
        yield conn
    finally:
        conn.close()


def ping() -> bool:
    """Verifica que la BD responde. Devuelve True/False sin lanzar excepción."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone()[0] == 1
    except PostgresConnectionError:
        return False

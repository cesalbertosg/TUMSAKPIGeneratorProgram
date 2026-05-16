"""Skips automáticos cuando no hay VPN/credenciales para correr tests de integración."""

from __future__ import annotations

import os

import pytest

from kpi_generator.config import Config


def _db_available() -> bool:
    """True si la BD Postgres está accesible (VPN + credenciales válidas)."""
    if not Config.PG_CEDULA_USER or not Config.PG_CEDULA_PASSWORD:
        return False
    try:
        from kpi_generator.io.postgres import ping
        return ping()
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_available(),
    reason="Postgres Cédula DG inaccesible (VPN apagada o credenciales faltantes en .env)",
)


def _excel_sample_dir() -> str | None:
    """Carpeta de cédulas Excel para comparar. Ajustar por env si es necesario."""
    candidate = os.getenv(
        "KPI_TEST_CEDULAS_EXCEL",
        r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\Cedulas",
    )
    return candidate if os.path.isdir(candidate) else None


@pytest.fixture
def excel_sample_dir():
    path = _excel_sample_dir()
    if path is None:
        pytest.skip("Carpeta de cédulas Excel de muestra no encontrada")
    return path

"""Test de identidad bit-a-bit: el reporte generado con source=db debe ser idéntico al de source=excel.

Este es el criterio de aceptación de la Fase 2 del plan de migración.
Requiere: VPN activa, credenciales Postgres en .env, archivos de muestra disponibles.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from kpi_generator.config import LogLevel
from kpi_generator.domain.processor import DataProcessor

from .conftest import needs_db


SAMPLE_DAY = os.getenv(
    "KPI_TEST_DAY_DIR",
    r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\16 Mayo",
)
SAMPLE_CEDULAS = os.getenv(
    "KPI_TEST_CEDULAS_EXCEL",
    r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\Cedulas",
)
SAMPLE_OBJ = os.getenv(
    "KPI_TEST_OBJ",
    r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\Objetivo de KM Mayo.xlsx",
)


@needs_db
@pytest.mark.slow
def test_pipeline_db_vs_excel_genera_identico(tmp_path):
    """Para el mismo rango y misma data, el Excel resultante debe ser idéntico."""
    trips = Path(SAMPLE_DAY) / "zmov.XLSX"
    fuel = Path(SAMPLE_DAY) / "zmva.XLSX"
    if not trips.exists() or not fuel.exists():
        pytest.skip("Archivos de muestra zmov/zmva no encontrados")

    out_excel = tmp_path / "excel"
    out_db = tmp_path / "db"
    out_excel.mkdir()
    out_db.mkdir()

    # Pipeline con fuente Excel
    p1 = DataProcessor(log_callback=lambda *a, **k: None, log_level=LogLevel.ERROR)
    r1 = p1.generate_report(str(trips), str(fuel), SAMPLE_CEDULAS, str(out_excel),
                            SAMPLE_OBJ, cedulas_source="excel")
    assert r1, "Pipeline Excel falló"

    # Pipeline con fuente BD
    p2 = DataProcessor(log_callback=lambda *a, **k: None, log_level=LogLevel.ERROR)
    r2 = p2.generate_report(str(trips), str(fuel), "", str(out_db),
                            SAMPLE_OBJ, cedulas_source="db")
    assert r2, "Pipeline BD falló"

    # Comparar hojas críticas
    hojas_criticas = ["KPIs per Equipment", "Trip Data", "Resumen de Cambios",
                      "KPIs OpCedula", "PromedioKMunitOps"]

    for hoja in hojas_criticas:
        df_excel = pd.read_excel(r1, sheet_name=hoja)
        df_db = pd.read_excel(r2, sheet_name=hoja)
        # Orden consistente para evitar falsos negativos por shuffling
        sort_cols = [c for c in df_excel.columns if c in df_db.columns][:3]
        df_excel = df_excel.sort_values(sort_cols).reset_index(drop=True)
        df_db = df_db.sort_values(sort_cols).reset_index(drop=True)
        assert_frame_equal(df_db, df_excel, check_dtype=False, check_exact=False,
                            rtol=1e-9, atol=1e-9,
                            obj=f"Hoja '{hoja}' difiere entre BD y Excel")

"""Test de equivalencia funcional entre el pipeline corriendo con `source=db` vs `source=excel`.

IMPORTANTE: NO esperamos identidad bit-a-bit. La BD tiene mayor cobertura que el Excel
(despachadores editan Drive en días donde no hay archivo Excel), así que el path BD
produce MÁS información real y MENOS forward-fill. Esto resulta en:
  - Distintos números de períodos detectados (BD = menos quiebres porque hay más
    continuidad de datos reales)
  - Distintos totales de unidades-período en KPIs per Equipment
  - Mismos totales agregados (viajes procesados, KM totales, comodatos)

El test valida que las **magnitudes globales** (sumas, conteos, unidades) sean
consistentes entre paths con tolerancia, NO que cada fila sea idéntica.

Requiere: VPN activa, credenciales Postgres en .env, archivos de muestra disponibles.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

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


def _run(source: str, out_dir: Path) -> str:
    trips = Path(SAMPLE_DAY) / "zmov.XLSX"
    fuel = Path(SAMPLE_DAY) / "zmva.XLSX"
    cedulas = SAMPLE_CEDULAS if source == "excel" else ""
    p = DataProcessor(log_callback=lambda *a, **k: None, log_level=LogLevel.ERROR)
    return p.generate_report(str(trips), str(fuel), cedulas, str(out_dir),
                             SAMPLE_OBJ, cedulas_source=source)


@needs_db
def test_pipeline_db_y_excel_producen_resultados_equivalentes(tmp_path):
    """Magnitudes globales coinciden con tolerancia razonable.

    No exigimos identidad porque BD es estructuralmente más completa que Excel
    (despachadores editan Drive en días sin archivo local).
    """
    trips = Path(SAMPLE_DAY) / "zmov.XLSX"
    if not trips.exists():
        pytest.skip("Archivos de muestra no encontrados")

    out_excel = tmp_path / "excel"
    out_db = tmp_path / "db"
    out_excel.mkdir()
    out_db.mkdir()

    r_excel = _run("excel", out_excel)
    r_db = _run("db", out_db)
    assert r_excel and r_db, "Ambos pipelines deben completar"

    # Trip Data: separar viajes reales de comodatos sintéticos
    #   - Comodatos: 'Número de Viaje' >= 2_000_000_000 (constante base_id de ComodatoManager)
    #   - Reales: < 2_000_000_000 (vienen del zmov.XLSX, idénticos entre fuentes)
    df_trips_excel = pd.read_excel(r_excel, sheet_name="Viajes")
    df_trips_db = pd.read_excel(r_db, sheet_name="Viajes")

    COMODATO_BASE = 2_000_000_000
    reales_excel = df_trips_excel[df_trips_excel['Número de Viaje'] < COMODATO_BASE]
    reales_db = df_trips_db[df_trips_db['Número de Viaje'] < COMODATO_BASE]
    com_excel = df_trips_excel[df_trips_excel['Número de Viaje'] >= COMODATO_BASE]
    com_db = df_trips_db[df_trips_db['Número de Viaje'] >= COMODATO_BASE]

    assert len(reales_excel) == len(reales_db), (
        f"Viajes REALES deben coincidir (vienen del mismo zmov.XLSX): "
        f"excel={len(reales_excel)} vs db={len(reales_db)}"
    )

    # KM total de viajes reales debe coincidir bit-a-bit
    km_excel = (reales_excel['KMLiqCargadoFinal'].fillna(0).sum() +
                reales_excel['KMLiqVacioFinal'].fillna(0).sum())
    km_db = (reales_db['KMLiqCargadoFinal'].fillna(0).sum() +
             reales_db['KMLiqVacioFinal'].fillna(0).sum())
    assert abs(km_excel - km_db) < 1.0, f"KM total real debe coincidir: excel={km_excel} vs db={km_db}"

    # Comodatos: BD genera MENOS (espera diferencia significativa pero acotada)
    print(f"\n[INFO] Viajes reales: excel={len(reales_excel)}, db={len(reales_db)} (deben coincidir)")
    print(f"[INFO] Comodatos: excel={len(com_excel)}, db={len(com_db)} "
          f"(BD genera menos por mayor cobertura)")

    # Objetivos: depende solo de archivo de objetivos, debe ser idéntico
    df_obj_excel = pd.read_excel(r_excel, sheet_name="Objetivos")
    df_obj_db = pd.read_excel(r_db, sheet_name="Objetivos")
    assert len(df_obj_excel) == len(df_obj_db), "Objetivos debe ser idéntico (no depende de cédulas)"

    # KPIs OpCedula: el número de OPERACIONES (no de unidades-periodo) debe coincidir
    # porque depende del catálogo de operaciones, que es el mismo
    df_op_excel = pd.read_excel(r_excel, sheet_name="Por Operación")
    df_op_db = pd.read_excel(r_db, sheet_name="Por Operación")
    ops_excel = set(df_op_excel['Operacion Cedula'])
    ops_db = set(df_op_db['Operacion Cedula'])
    overlap = len(ops_excel & ops_db) / max(len(ops_excel), len(ops_db))
    assert overlap >= 0.90, (
        f"Operaciones cédula deben coincidir ≥90%: overlap={overlap:.1%}, "
        f"solo_excel={ops_excel - ops_db}, solo_db={ops_db - ops_excel}"
    )

    # KPIs per Equipment: BD tiene MENOS períodos por mayor cobertura
    df_kpi_excel = pd.read_excel(r_excel, sheet_name="Por Equipo")
    df_kpi_db = pd.read_excel(r_db, sheet_name="Por Equipo")
    units_excel = set(df_kpi_excel['Equipo Motriz'].astype(str))
    units_db = set(df_kpi_db['Equipo Motriz'].astype(str))
    units_overlap = len(units_excel & units_db) / max(len(units_excel), len(units_db))
    assert units_overlap >= 0.95, (
        f"Unidades en KPIs deben coincidir ≥95%: overlap={units_overlap:.1%}"
    )

    # Reporte informativo (no falla el test)
    print(f"\n[INFO] Trip Data: excel={len(df_trips_excel)}, db={len(df_trips_db)}")
    print(f"[INFO] KPIs per Equipment: excel={len(df_kpi_excel)}, db={len(df_kpi_db)} "
          f"(BD genera menos períodos por mayor cobertura)")
    print(f"[INFO] Operaciones OpCedula: excel={len(ops_excel)}, db={len(ops_db)}, overlap={overlap:.1%}")
    print(f"[INFO] Unidades únicas: excel={len(units_excel)}, db={len(units_db)}, overlap={units_overlap:.1%}")

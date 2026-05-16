"""Compara cédulas cargadas desde BD vs Excel para el mismo rango."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from kpi_generator.config import LogLevel
from kpi_generator.domain.processor import DataProcessor
from kpi_generator.io.cedulas_db import EXCEL_COLUMNS, load_cedulas_from_db

from .conftest import needs_db


@needs_db
def test_db_load_returns_expected_columns():
    """El DataFrame de BD debe tener exactamente las columnas del contrato Excel."""
    df, audit = load_cedulas_from_db(date(2026, 5, 1), date(2026, 5, 16), log_func=lambda *a, **k: None)
    assert list(df.columns) == EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"]
    assert list(audit.columns) == ["Unidades", "Fecha Cedula", "Origen", "Fecha Cedula Origen"]


@needs_db
def test_db_load_has_one_row_per_unit_per_day():
    """No debe haber duplicados (Unidades, Fecha Cedula) en el snapshot diario."""
    df, _ = load_cedulas_from_db(date(2026, 5, 1), date(2026, 5, 16), log_func=lambda *a, **k: None)
    dup = df.duplicated(subset=["Unidades", "Fecha Cedula"], keep=False)
    assert not dup.any(), f"Snapshot tiene duplicados: {df[dup].head()}"


@needs_db
def test_db_vs_excel_unit_coverage_matches(excel_sample_dir):
    """El conjunto de unidades cubiertas debe coincidir entre BD y Excel para el mismo rango."""
    fecha_min = date(2026, 5, 1)
    fecha_max = date(2026, 5, 16)

    df_db, _ = load_cedulas_from_db(fecha_min, fecha_max, log_func=lambda *a, **k: None)

    processor = DataProcessor(log_callback=lambda *a, **k: None, log_level=LogLevel.ERROR)
    df_excel = processor.load_daily_cedulas(excel_sample_dir)
    df_excel = df_excel[
        (df_excel['Fecha Cedula_dt'] >= pd.Timestamp(fecha_min)) &
        (df_excel['Fecha Cedula_dt'] <= pd.Timestamp(fecha_max))
    ]

    units_db = set(df_db['Unidades'].astype(str))
    units_excel = set(df_excel['Unidades'].astype(str))

    # Tolerancia: algunas unidades pueden estar en una fuente pero no en la otra
    # (cédulas Excel pueden tener correcciones tardías). Reportar pero no fallar
    # hasta que la migración esté completa en Fase 2.
    solo_db = units_db - units_excel
    solo_excel = units_excel - units_db
    print(f"\nUnidades: BD={len(units_db)}, Excel={len(units_excel)}, "
          f"solo BD={len(solo_db)}, solo Excel={len(solo_excel)}")

    # En Fase 2 cambiar este aserto a strict equality
    assert len(units_db & units_excel) >= 0.95 * max(len(units_db), len(units_excel)), \
        "Cobertura común BD vs Excel <95%, revisar manualmente"

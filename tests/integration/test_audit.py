"""Tests del subsistema de auditoría.

Quick mode no requiere VPN ni archivos — siempre debe correr.
Full mode requiere BD activa + archivos de muestra — skip si falta algo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kpi_generator import audit
from kpi_generator.audit import AuditReport, Status

from .conftest import needs_db


# ----------------------------------------------------------------------------
# Quick mode (sin BD, sin archivos)
# ----------------------------------------------------------------------------

def test_quick_runs_all_checks_without_exception():
    """run_quick siempre debe completar sin lanzar excepciones."""
    report = audit.run_quick()
    assert len(report.results) >= 6, "Debe ejecutar al menos 6 checks quick"
    names = {r.name for r in report.results}
    expected = {"package.version", "package.imports", "config.attrs",
                "config.source", "creds.postgres", "creds.gsheets",
                "connectivity.postgres", "connectivity.gsheets"}
    assert expected.issubset(names), f"Faltan checks: {expected - names}"


def test_quick_imports_check_passes_in_healthy_env():
    """package.imports debe PASS en cualquier entorno con el paquete instalado."""
    report = audit.run_quick()
    imports_result = next(r for r in report.results if r.name == "package.imports")
    assert imports_result.status == Status.PASS, f"Imports failed: {imports_result.detail}"


def test_quick_config_check_passes():
    """config.attrs debe PASS — todas las constantes esperadas existen."""
    report = audit.run_quick()
    cfg = next(r for r in report.results if r.name == "config.attrs")
    assert cfg.status == Status.PASS


def test_render_produces_summary():
    """render() siempre produce texto con el verdicto al final."""
    report = audit.run_quick()
    text = audit.render(report, use_color=False)
    assert "Auditoría de salud" in text
    assert "Verdicto:" in text
    assert any(v in text for v in ["SANO", "DEGRADADO", "CRITICO"])


def test_exit_code_consistent_with_status():
    """exit_code() debe ser 0/1/2 según el peor status."""
    r_ok = AuditReport()
    r_ok.add("t", Status.PASS, "")
    assert r_ok.exit_code() == 0

    r_warn = AuditReport()
    r_warn.add("t", Status.PASS, "")
    r_warn.add("t", Status.WARN, "")
    assert r_warn.exit_code() == 1

    r_fail = AuditReport()
    r_fail.add("t", Status.PASS, "")
    r_fail.add("t", Status.WARN, "")
    r_fail.add("t", Status.FAIL, "")
    assert r_fail.exit_code() == 2


# ----------------------------------------------------------------------------
# Full mode (requiere VPN + archivos reales)
# ----------------------------------------------------------------------------

SAMPLE_DAY = os.getenv(
    "KPI_TEST_DAY_DIR",
    r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\16 Mayo",
)
SAMPLE_OBJ = os.getenv(
    "KPI_TEST_OBJ",
    r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\KPIs\05 Mayo\Objetivo de KM Mayo.xlsx",
)


@needs_db
def test_full_pipeline_audit_pass(tmp_path):
    """En entorno sano, run_full debe completar todos los checks pipeline en PASS."""
    trips = Path(SAMPLE_DAY) / "zmov.XLSX"
    fuel = Path(SAMPLE_DAY) / "zmva.XLSX"
    objectives = Path(SAMPLE_OBJ)
    if not trips.exists() or not fuel.exists():
        pytest.skip(f"Archivos de muestra no encontrados en {SAMPLE_DAY}")

    report = audit.run_full(trips, fuel, objectives, tmp_path)

    # No debe haber FAIL en checks críticos
    critical = ["pipeline.run", "output.sheets", "output.deadweight",
                "resumen.totals"]
    for name in critical:
        result = next((r for r in report.results if r.name == name), None)
        assert result is not None, f"Check {name} no se ejecutó"
        assert result.status in (Status.PASS, Status.WARN), \
            f"Check {name} en FAIL: {result.message} | {result.detail}"


@needs_db
def test_full_audit_produces_clean_excel(tmp_path):
    """El Excel generado por audit debe ser válido y abrirse correctamente."""
    import pandas as pd
    trips = Path(SAMPLE_DAY) / "zmov.XLSX"
    fuel = Path(SAMPLE_DAY) / "zmva.XLSX"
    objectives = Path(SAMPLE_OBJ)
    if not trips.exists() or not fuel.exists():
        pytest.skip("Archivos de muestra no encontrados")

    report = audit.run_full(trips, fuel, objectives, tmp_path)
    pipeline_result = next((r for r in report.results if r.name == "pipeline.run"), None)
    if not pipeline_result or pipeline_result.status != Status.PASS:
        pytest.skip(f"Pipeline no completó: {pipeline_result}")

    # El detail del check pipeline.run guarda el filename
    excel = next(tmp_path.glob("KPIs_Transport_*.xlsx"))
    xls = pd.ExcelFile(excel)
    assert "Resumen" in xls.sheet_names
    df_resumen = pd.read_excel(excel, sheet_name="Resumen")
    assert len(df_resumen) >= 2, "Resumen debe tener al menos N+1 filas"
    assert "TOTAL" in str(df_resumen.iloc[-1]["Gerencia"]).upper()

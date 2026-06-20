"""Tests para `ChangeTracker._detect_unit_changes`.

Valida la deteccion de transiciones operativas por unidad:
- INGRESO: la unidad aparece despues de la fecha minima global del rango.
- EGRESO: la unidad desaparece antes de la fecha maxima global del rango.
- OPERACIONAL: cambia el par (Operacion + Circuito/Tipo de Unidad) entre dias
  consecutivos de la misma unidad.
- Las transiciones puramente de status (`Operando` -> `Operando` con misma
  operacion/circuito) NO se cuentan.

Schema esperado de `unit_data`:
    Unidades, Fecha Cedula_dt, Operación, Circuito, Tipo de Unidad, Gerencia
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.change_tracker import ChangeTracker


def _df_unit(rows: list[dict]) -> pd.DataFrame:
    """Helper: construye un DataFrame de cedulas para una sola unidad."""
    df = pd.DataFrame(rows)
    df["Fecha Cedula_dt"] = pd.to_datetime(df["Fecha Cedula_dt"])
    return df.sort_values("Fecha Cedula_dt").reset_index(drop=True)


@pytest.fixture
def tracker() -> ChangeTracker:
    return ChangeTracker(log_callback=lambda *_a, **_k: None)


# ---------- Ingresos ----------

def test_ingreso_unidad_aparece_despues_de_fecha_minima(tracker: ChangeTracker) -> None:
    """Unidad cuya primera fecha > fecha_min_global => 1 cambio INGRESO."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-05", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    tipos = [c["Tipo Cambio"] for c in changes]
    assert "INGRESO" in tipos
    ingreso = next(c for c in changes if c["Tipo Cambio"] == "INGRESO")
    assert ingreso["Operacion inicial"] == "POR ASIGNAR FULL"
    assert ingreso["Operacion final"] == "VEND CENTRO"
    assert ingreso["Gerencia inicial"] == "Pendiente"
    assert ingreso["Fecha cambio"] == "05/05/2026"


def test_sin_ingreso_si_primera_fecha_es_la_global(tracker: ChangeTracker) -> None:
    """Si la unidad ya esta en la fecha minima global, no hay INGRESO."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    assert all(c["Tipo Cambio"] != "INGRESO" for c in changes)


# ---------- Egresos ----------

def test_egreso_unidad_desaparece_antes_de_fecha_maxima(tracker: ChangeTracker) -> None:
    """Unidad cuya ultima fecha < fecha_max_global => 1 cambio EGRESO con fecha +1 dia."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-05", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    egreso = next(c for c in changes if c["Tipo Cambio"] == "EGRESO")
    assert egreso["Operacion inicial"] == "VEND CENTRO"
    assert egreso["Operacion final"] == "POR ASIGNAR FULL"
    assert egreso["Gerencia final"] == "Pendiente"
    # El egreso se registra el dia siguiente al ultimo dia activo (06/05).
    assert egreso["Fecha cambio"] == "06/05/2026"


# ---------- Cambios operacionales ----------

def test_cambio_operacional_detectado(tracker: ChangeTracker) -> None:
    """Cambio de Circuito CENTRO -> NORTE en la misma unidad => 1 cambio OPERACIONAL."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-02", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    op_changes = [c for c in changes if c["Tipo Cambio"] == "OPERACIONAL"]
    assert len(op_changes) == 1
    assert op_changes[0]["Operacion inicial"] == "VEND CENTRO"
    assert op_changes[0]["Operacion final"] == "VEND NORTE"
    assert op_changes[0]["Gerencia inicial"] == "CUER"
    assert op_changes[0]["Gerencia final"] == "MEX"
    assert op_changes[0]["Fecha cambio"] == "02/05/2026"


def test_sin_cambio_si_misma_operacion_y_circuito(tracker: ChangeTracker) -> None:
    """Misma Operacion + mismo Circuito en todos los dias => 0 cambios operacionales."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-05", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    assert [c for c in changes if c["Tipo Cambio"] == "OPERACIONAL"] == []


def test_circuito_especial_usa_tipo_unidad(tracker: ChangeTracker) -> None:
    """Circuito DEDICADO (SPECIAL) => OpCedula usa Tipo de Unidad en lugar de Circuito."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "DEDICADO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-02", "Operación": "VEND",
         "Circuito": "DEDICADO", "Tipo de Unidad": "RABON", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "DEDICADO", "Tipo de Unidad": "RABON", "Gerencia": "CUER"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)

    op = next(c for c in changes if c["Tipo Cambio"] == "OPERACIONAL")
    assert op["Operacion inicial"] == "VEND FULL"
    assert op["Operacion final"] == "VEND RABON"


# ---------- Objetivos ----------

def test_objetivos_propagados_a_cambios(tracker: ChangeTracker) -> None:
    """Si `obj_mapping` trae KM y Viajes para la OpCedula, se rellenan en el cambio."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-10")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-02", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
    ])
    obj_mapping = {
        "VEND CENTRO": {"Objetivo KM Diario": 300, "Objetivo Viajes Diario": 4},
        "VEND NORTE": {"Objetivo KM Diario": 500, "Objetivo Viajes Diario": 6},
    }

    changes = tracker._detect_unit_changes(unit, obj_mapping, fecha_min, fecha_max)

    op = next(c for c in changes if c["Tipo Cambio"] == "OPERACIONAL")
    assert op["Objetivo diario inicial KM"] == 300
    assert op["Objetivo diario final KM"] == 500
    assert op["Objetivo diario inicial Viajes"] == 4
    assert op["Objetivo diario final Viajes"] == 6


# ---------- Combinacion completa ----------

def test_ingreso_cambio_y_egreso_juntos(tracker: ChangeTracker) -> None:
    """Una unidad puede registrar INGRESO + OPERACIONAL + EGRESO en el mismo rango."""
    fecha_min = pd.Timestamp("2026-05-01")
    fecha_max = pd.Timestamp("2026-05-15")
    unit = _df_unit([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-05", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-08", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-10", "Operación": "VEND",
         "Circuito": "NORTE", "Tipo de Unidad": "FULL", "Gerencia": "MEX"},
    ])

    changes = tracker._detect_unit_changes(unit, None, fecha_min, fecha_max)
    tipos = sorted(c["Tipo Cambio"] for c in changes)

    assert tipos == ["EGRESO", "INGRESO", "OPERACIONAL"]

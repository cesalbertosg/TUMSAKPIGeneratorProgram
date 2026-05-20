"""Tests unitarios de `build_daily_snapshot` con DataFrames sintéticos.

No requieren VPN ni credenciales — verifican la lógica pura de forward-fill
sobre el shape que devuelve la query SQL.

Casos cubiertos:
1. Día sin revisión en medio del rango → forward-fill
2. Unidad que aparece a mitad del rango (ingreso) → sin filas antes
3. Múltiples revisiones del mismo día → drop_duplicates queda con la última
4. Rango sin cobertura BD (solo semilla previa) → forward-fill cubre todo
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from kpi_generator.io.cedulas_db import EXCEL_COLUMNS, build_daily_snapshot


def _row(unidad: str, fecha: str, origen: str = "rango",
         operacion: str = "VEND", circuito: str = "POR ASIGNAR",
         tipo_unidad: str = "SENCILLO", gerencia: str = "CUERNAVACA",
         estatus_2: str = "Operando") -> dict:
    """Construye una fila como la devolvería la query SQL.

    Las claves son las del schema BD (snake_case) + `origen` + `fecha_dia`.
    """
    return {
        "unidades": unidad,
        "gerencia": gerencia,
        "operacion": operacion,
        "tipo_unidad": tipo_unidad,
        "circuito": circuito,
        "estatus_2": estatus_2,
        "fecha_dia": pd.Timestamp(fecha).date(),
        "origen": origen,
    }


def test_forward_fill_dia_sin_revision_en_medio():
    """Si una unidad tiene revisión el 01 y el 03 pero no el 02, el 02 debe
    rellenarse con los datos del 01 y marcarse como forward_fill."""
    raw = pd.DataFrame([
        _row("T101", "2026-05-01", operacion="OPA"),
        _row("T101", "2026-05-03", operacion="OPB"),
    ])
    snap, audit = build_daily_snapshot(raw, date(2026, 5, 1), date(2026, 5, 3))

    assert len(snap) == 3, f"Esperado 3 filas (1 por día), got {len(snap)}"
    assert list(snap.columns) == EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"]

    by_fecha = {row["Fecha Cedula"]: row for _, row in snap.iterrows()}
    assert by_fecha["01/05/2026"]["Operación"] == "OPA"
    assert by_fecha["02/05/2026"]["Operación"] == "OPA", "02 debe heredar de 01"
    assert by_fecha["03/05/2026"]["Operación"] == "OPB"

    audit_by_fecha = {row["Fecha Cedula"]: row for _, row in audit.iterrows()}
    assert audit_by_fecha["01/05/2026"]["Origen"] == "real"
    assert audit_by_fecha["02/05/2026"]["Origen"] == "forward_fill"
    assert audit_by_fecha["02/05/2026"]["Fecha Cedula Origen"] == "01/05/2026"
    assert audit_by_fecha["03/05/2026"]["Origen"] == "real"


def test_unidad_que_aparece_a_mitad_del_rango_no_genera_filas_antes():
    """Una unidad sin semilla previa y sin registros antes del 05 no debe tener
    filas en 01-04 (la unidad aún no existía en la flota)."""
    raw = pd.DataFrame([
        _row("T200", "2026-05-05", operacion="OPC"),
        _row("T200", "2026-05-06", operacion="OPC"),
    ])
    snap, audit = build_daily_snapshot(raw, date(2026, 5, 1), date(2026, 5, 7))

    fechas_t200 = sorted(snap[snap["Unidades"] == "T200"]["Fecha Cedula"].tolist())
    # 05, 06 reales + 07 forward-fill = 3 filas
    assert fechas_t200 == ["05/05/2026", "06/05/2026", "07/05/2026"]
    assert "01/05/2026" not in fechas_t200
    assert "04/05/2026" not in fechas_t200


def test_duplicados_misma_fecha_se_queda_con_el_ultimo():
    """Si por alguna razón la query devuelve 2 filas para (T101, 2026-05-01),
    drop_duplicates keep='last' conserva la última (mayor en sort)."""
    raw = pd.DataFrame([
        _row("T101", "2026-05-01", operacion="OPA"),
        _row("T101", "2026-05-01", operacion="OPB"),  # duplicado, gana este
    ])
    snap, _ = build_daily_snapshot(raw, date(2026, 5, 1), date(2026, 5, 1))

    assert len(snap) == 1
    assert snap.iloc[0]["Operación"] == "OPB", "Debe quedar el último de los duplicados"


def test_solo_semilla_previa_cubre_todo_el_rango_con_forward_fill():
    """Si la BD tiene solo una semilla 'previa' (anterior al rango) y nada
    dentro del rango, todo el rango debe rellenarse con esa semilla."""
    raw = pd.DataFrame([
        _row("T300", "2026-04-28", origen="previa", operacion="OPX"),
    ])
    snap, audit = build_daily_snapshot(raw, date(2026, 5, 1), date(2026, 5, 3))

    assert len(snap) == 3, "Los 3 días deben rellenarse con la semilla"
    assert (snap["Operación"] == "OPX").all()
    assert (audit["Origen"] == "forward_fill").all(), "Todas las filas son forward_fill"
    assert (audit["Fecha Cedula Origen"] == "28/04/2026").all(), "Todas referencian la semilla del 28/04"


def test_dataframe_vacio_devuelve_shape_correcto():
    """Caso degenerado: BD devuelve 0 filas — el snapshot debe estar vacío pero
    tener las columnas del contrato para que pandas downstream no rompa."""
    raw = pd.DataFrame(columns=["unidades", "gerencia", "operacion", "tipo_unidad",
                                  "circuito", "estatus_2", "fecha_dia", "origen"])
    snap, audit = build_daily_snapshot(raw, date(2026, 5, 1), date(2026, 5, 3))

    assert len(snap) == 0
    assert list(snap.columns) == EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"]
    assert len(audit) == 0
    assert list(audit.columns) == ["Unidades", "Fecha Cedula", "Origen", "Fecha Cedula Origen"]

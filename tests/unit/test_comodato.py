"""Tests para `ComodatoManager._get_operacion_cedula_comodato`.

Valida la regla de negocio para generar el campo `Operacion cedula` en
registros de comodato (dias sin viajes para unidades que SI estan en
cedula).

Reglas:
- Si el circuito esta en SPECIAL_CIRCUITS = {DEDICADO, POR ASIGNAR, SPRINTER,
  TERCERO, VENTA} -> "{OPERACION} {TIPO_UNIDAD}".
- En cualquier otro caso -> "{OPERACION} {CIRCUITO}".
- El metodo siempre devuelve los componentes en MAYUSCULAS.

Tests adicionales:
- `create_comodatos` ignora unidades phantom (no en cedula).
- `create_comodatos` solo cubre el rango [primera_fecha_cedula, ultima_fecha_cedula].
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.comodato import ComodatoManager


# ---------- _get_operacion_cedula_comodato (regla pura) ----------

@pytest.mark.parametrize("circuito,operacion,tipo_unidad,esperado", [
    # SPECIAL_CIRCUITS -> usa tipo_unidad
    ("DEDICADO", "VEND", "SENCILLO", "VEND SENCILLO"),
    ("POR ASIGNAR", "DIST", "FULL", "DIST FULL"),
    ("SPRINTER", "ENT", "SPRINTER", "ENT SPRINTER"),
    ("TERCERO", "ENT", "RABON", "ENT RABON"),
    ("VENTA", "VEND", "SENCILLO", "VEND SENCILLO"),
    # Circuito normal -> usa circuito
    ("CENTRO", "DIST", "FULL", "DIST CENTRO"),
    ("NORTE", "ENT", "RABON", "ENT NORTE"),
])
def test_get_operacion_cedula_comodato_reglas(circuito: str, operacion: str,
                                              tipo_unidad: str, esperado: str) -> None:
    manager = ComodatoManager()
    assert manager._get_operacion_cedula_comodato(operacion, circuito, tipo_unidad) == esperado


def test_get_operacion_cedula_normaliza_mayusculas() -> None:
    """Entradas en minusculas o mixtas siempre se devuelven en mayusculas."""
    manager = ComodatoManager()
    assert manager._get_operacion_cedula_comodato("vend", "centro", "full") == "VEND CENTRO"
    assert manager._get_operacion_cedula_comodato("Dist", "Dedicado", "Sencillo") == "DIST SENCILLO"


# ---------- create_comodatos (integracion ligera) ----------

def _df_cedulas(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["Fecha Cedula_dt"] = pd.to_datetime(df["Fecha Cedula_dt"])
    return df


def _df_trips(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["Equipo Motriz", "Fecha creación_date"])
    return pd.DataFrame(rows)


def test_comodato_genera_dia_faltante() -> None:
    """Unidad en cedula 3 dias pero solo viajo 2: debe generar 1 comodato."""
    cedulas = _df_cedulas([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUERNAVACA",
         "Operando": "Operando"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-02", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUERNAVACA",
         "Operando": "Operando"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-03", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUERNAVACA",
         "Operando": "Operando"},
    ])
    trips = _df_trips([
        {"Equipo Motriz": "C070", "Fecha creación_date": pd.Timestamp("2026-05-01").date(),
         "Número de Viaje": 1},
        {"Equipo Motriz": "C070", "Fecha creación_date": pd.Timestamp("2026-05-03").date(),
         "Número de Viaje": 2},
    ])
    mapping = {"C070": {"En Cedula": True}}

    manager = ComodatoManager()
    out = manager.create_comodatos(trips, cedulas, mapping, log_func=lambda *_: None)

    assert len(out) == 1
    assert out.iloc[0]["Fecha creación_date"] == pd.Timestamp("2026-05-02").date()
    assert out.iloc[0]["Operación cedula"] == "VEND CENTRO"
    assert out.iloc[0]["ClaveCategoria"] == "COM"


def test_phantom_unit_no_genera_comodato() -> None:
    """Unidad solo en viajes (no en cedula) NUNCA debe generar comodato.

    Setup: C070 esta en cedula Y viaja el mismo dia (=> sin comodato).
    T999 (phantom) viaja pero no esta en cedula => debe ser ignorada.
    Resultado esperado: 0 comodatos.
    """
    cedulas = _df_cedulas([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER",
         "Operando": "Operando"},
    ])
    trips = _df_trips([
        # C070 cubre su unico dia de cedula => no necesita comodato
        {"Equipo Motriz": "C070", "Fecha creación_date": pd.Timestamp("2026-05-01").date(),
         "Número de Viaje": 1},
        # FANTASMA: T999 viaja pero no esta en cedula => debe ser ignorada
        {"Equipo Motriz": "T999", "Fecha creación_date": pd.Timestamp("2026-05-01").date(),
         "Número de Viaje": 2},
    ])
    mapping = {"C070": {"En Cedula": True}}

    manager = ComodatoManager()
    out = manager.create_comodatos(trips, cedulas, mapping, log_func=lambda *_: None)

    assert out.empty


def test_unidad_con_en_cedula_false_se_ignora() -> None:
    """Si unit_mapping marca `En Cedula=False`, no se generan comodatos para esa unidad."""
    cedulas = _df_cedulas([
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-01", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER",
         "Operando": "Operando"},
        {"Unidades": "C070", "Fecha Cedula_dt": "2026-05-02", "Operación": "VEND",
         "Circuito": "CENTRO", "Tipo de Unidad": "FULL", "Gerencia": "CUER",
         "Operando": "Operando"},
    ])
    trips = _df_trips([
        {"Equipo Motriz": "C070", "Fecha creación_date": pd.Timestamp("2026-05-01").date(),
         "Número de Viaje": 1},
    ])
    mapping = {"C070": {"En Cedula": False}}

    manager = ComodatoManager()
    out = manager.create_comodatos(trips, cedulas, mapping, log_func=lambda *_: None)

    assert out.empty

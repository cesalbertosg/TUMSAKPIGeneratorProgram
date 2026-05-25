"""Tests para `_contar_remolques_unicos_prorrateado`.

Validan que SUM(Cuenta remolques) por Operación Cedula == # remolques únicos.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.processor import DataProcessor


def _df(rows: list[dict]) -> pd.DataFrame:
    """Helper para construir un DataFrame con columnas mínimas requeridas."""
    return pd.DataFrame(rows)


def test_remolque_duplicado_en_r1_y_r2_cuenta_una_sola_vez():
    """Si el mismo remolque aparece en R1 y R2 del mismo viaje, cuenta 1 (no 2)."""
    df = _df([
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40331', 'Equipo Remolque 2': '40331'},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert s.sum() == 1.0, f"Esperado 1, got {s.sum()}"


def test_dos_viajes_mismo_remolque_misma_opcedula_cuenta_una_vez():
    """Dos viajes en la misma OpCédula que usan el MISMO remolque cuentan 1."""
    df = _df([
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40000', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40000', 'Equipo Remolque 2': ''},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert s.sum() == pytest.approx(1.0), f"Esperado 1, got {s.sum()}"
    # Cada fila debe tener 0.5 (1 único repartido entre 2 viajes)
    assert s.iloc[0] == pytest.approx(0.5)
    assert s.iloc[1] == pytest.approx(0.5)


def test_dos_remolques_distintos_misma_opcedula_cuenta_dos():
    """Dos viajes con remolques distintos en misma OpCédula → 2 únicos."""
    df = _df([
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40001', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40002', 'Equipo Remolque 2': ''},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert s.sum() == pytest.approx(2.0), f"Esperado 2, got {s.sum()}"


def test_opcedulas_distintas_independientes():
    """Cada OpCédula cuenta sus remolques únicos independientemente."""
    df = _df([
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40001', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40002', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_B', 'Equipo Remolque 1': '40003', 'Equipo Remolque 2': ''},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    # SUM por OpCédula
    df_t = pd.concat([df, s.rename('Cuenta remolques')], axis=1)
    suma_a = df_t[df_t['Operación cedula'] == 'OP_A']['Cuenta remolques'].sum()
    suma_b = df_t[df_t['Operación cedula'] == 'OP_B']['Cuenta remolques'].sum()
    assert suma_a == pytest.approx(2.0), f"OP_A esperado 2, got {suma_a}"
    assert suma_b == pytest.approx(1.0), f"OP_B esperado 1, got {suma_b}"


def test_viajes_sin_remolque_reciben_cero():
    """Comodatos / viajes sin remolque registrado deben recibir 0 (no contaminan suma)."""
    df = _df([
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40001', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '', 'Equipo Remolque 2': ''},  # comodato
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': None, 'Equipo Remolque 2': None},  # NaN
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert s.iloc[0] == pytest.approx(1.0), "Viaje con remolque recibe el total"
    assert s.iloc[1] == 0.0, "Viaje sin remolque recibe 0"
    assert s.iloc[2] == 0.0, "NaN tratado como vacío"
    assert s.sum() == pytest.approx(1.0)


def test_caso_real_de_beto_dos_viajes_t667_remolque_40331_en_r1_y_r2():
    """Caso de la imagen: T667 día 18/05 tiene remolque 40331 en R1 Y R2.
    Otro viaje T667 día 22/05 también tiene 40331 en R1 Y R2. Misma OpCédula.

    Esperado: 1 remolque único (40331), prorrateado entre los 2 viajes con remolque.
    """
    df = _df([
        {'Operación cedula': 'SORIANA VILLA', 'Equipo Remolque 1': '40331', 'Equipo Remolque 2': '40331'},
        {'Operación cedula': 'SORIANA VILLA', 'Equipo Remolque 1': '40331', 'Equipo Remolque 2': '40331'},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert s.sum() == pytest.approx(1.0), \
        f"Esperado SUM=1 (un solo remolque único), got {s.sum()} (algoritmo viejo daba 4)"


def test_mezcla_completa_opcedulas_y_remolques():
    """Caso compuesto: múltiples OpCédulas, remolques compartidos, duplicados R1=R2."""
    df = _df([
        # OP_A: 3 viajes, remolques únicos 40001, 40002 → SUM debe ser 2
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40001', 'Equipo Remolque 2': ''},
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '40002', 'Equipo Remolque 2': '40001'},  # 40001 repetido
        {'Operación cedula': 'OP_A', 'Equipo Remolque 1': '', 'Equipo Remolque 2': ''},  # sin remolque
        # OP_B: 1 viaje, 1 remolque → SUM debe ser 1
        {'Operación cedula': 'OP_B', 'Equipo Remolque 1': '50001', 'Equipo Remolque 2': '50001'},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    df_t = pd.concat([df, s.rename('Cuenta remolques')], axis=1)
    suma_a = df_t[df_t['Operación cedula'] == 'OP_A']['Cuenta remolques'].sum()
    suma_b = df_t[df_t['Operación cedula'] == 'OP_B']['Cuenta remolques'].sum()
    assert suma_a == pytest.approx(2.0), f"OP_A: esperado 2 únicos, got {suma_a}"
    assert suma_b == pytest.approx(1.0), f"OP_B: esperado 1 único, got {suma_b}"


def test_dataframe_sin_columna_opcedula_no_truena():
    """Si por alguna razón falta 'Operación cedula', devuelve serie de ceros sin error."""
    df = _df([
        {'Equipo Remolque 1': '40001', 'Equipo Remolque 2': ''},
    ])
    s = DataProcessor._contar_remolques_unicos_prorrateado(df)
    assert (s == 0).all()

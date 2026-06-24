"""Tests para `io.sheets.load_cedulas_for_period` — loader hibrido fisico + Drive API.

Regresion real (2026-06-24): con fuente "sheets", una unidad reasignada a
mitad de mes (ej. C084: asignada hasta el 18/06, "Pendiente" desde el 19/06)
aparecia "Pendiente" los 31 dias del periodo porque el loader anterior
(`load_cedula_from_sheet`) tomaba el valor VIGENTE del sheet y lo aplicaba a
todas las fechas por igual. `load_cedulas_for_period` prioriza los archivos
fisicos diarios (Paso 1, "autoritativo") precisamente para que cada fecha
conserve el valor real de ESE dia. Estos tests cubren ese contrato sin red:
fuerzan que la conexion a Sheets/Drive falle y verifican que, si la carpeta
fisica cubre el rango completo, el resultado es 100% fiel dia por dia.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from kpi_generator.io.sheets import load_cedulas_for_period

_NOLOG = lambda *_a, **_k: None  # noqa: E731

_COLS = ['Unidades', 'Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Operando']


def _write_cedula_xlsx(folder, dia_mes_anio: str, filas: list[dict]) -> None:
    df = pd.DataFrame(filas, columns=_COLS)
    df.to_excel(folder / f"Cedula {dia_mes_anio}.xlsx", engine='openpyxl', index=False)


def test_prioriza_archivos_fisicos_y_preserva_cambio_de_asignacion_dia_por_dia(tmp_path) -> None:
    """C084 asignada los dias 1-2, 'Pendiente' el dia 3 -> el resultado debe
    variar dia a dia, NO heredar un solo valor para todo el rango."""
    folder = tmp_path / "cedulas"
    folder.mkdir()
    _write_cedula_xlsx(folder, "01062026", [
        {'Unidades': 'C084', 'Gerencia': 'Salvador Manuel', 'Operación': 'CUERNAVACA',
         'Tipo de Unidad': 'TORTHON', 'Circuito': 'TERCERO', 'Operando': 'Operando'},
    ])
    _write_cedula_xlsx(folder, "02062026", [
        {'Unidades': 'C084', 'Gerencia': 'Salvador Manuel', 'Operación': 'CUERNAVACA',
         'Tipo de Unidad': 'TORTHON', 'Circuito': 'TERCERO', 'Operando': 'Disponible'},
    ])
    _write_cedula_xlsx(folder, "03062026", [
        {'Unidades': 'C084', 'Gerencia': 'Pendiente', 'Operación': 'Por Asignar',
         'Tipo de Unidad': 'TORTHON', 'Circuito': 'TERCERO', 'Operando': 'Sin Asignación'},
    ])

    # Sin red: forzar que la conexion a Sheets/Drive falle. Con la carpeta
    # cubriendo el rango completo, el loader debe resolver solo con fisicos.
    with patch('kpi_generator.io.sheets.gspread.authorize', side_effect=RuntimeError("offline")):
        df = load_cedulas_for_period(
            sheet_id='fake-id', log=_NOLOG,
            fecha_min=date(2026, 6, 1), fecha_max=date(2026, 6, 3),
            cedulas_folder=str(folder),
        )

    assert df is not None
    df = df.sort_values('Fecha Cedula_dt')
    gerencias = df.loc[df['Unidades'] == 'C084', 'Gerencia'].tolist()
    assert gerencias == ['Salvador Manuel', 'Salvador Manuel', 'Pendiente'], (
        "La asignacion debe variar dia a dia segun el archivo fisico de cada "
        "fecha, no heredar un solo valor para todo el rango"
    )


def test_sin_drive_y_sin_carpeta_devuelve_none(tmp_path) -> None:
    """Si la conexion a Sheets/Drive falla y no hay carpeta de respaldo, no
    hay datos disponibles: debe devolver None en vez de fabricar un resultado."""
    with patch('kpi_generator.io.sheets.gspread.authorize', side_effect=RuntimeError("offline")):
        df = load_cedulas_for_period(
            sheet_id='fake-id', log=_NOLOG,
            fecha_min=date(2026, 6, 1), fecha_max=date(2026, 6, 3),
            cedulas_folder=None,
        )

    assert df is None

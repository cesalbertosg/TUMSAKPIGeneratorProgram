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


# ---------- v0.6.5: fetch_dates_from_revisions (helper compartido) ----------

def test_fetch_dates_offline_devuelve_vacio_con_advertencia() -> None:
    """Best-effort: sin red devuelve {} sin lanzar y deja advertencia en el
    linaje — el modo excel degrada a forward-fill con este contrato."""
    from kpi_generator.io.sheets import fetch_dates_from_revisions
    from kpi_generator.lineage import CedulaLineage

    lineage = CedulaLineage(fuente_solicitada='excel')
    with patch('kpi_generator.io.sheets.gspread.authorize', side_effect=RuntimeError("offline")):
        result = fetch_dates_from_revisions(
            'fake-id', _NOLOG, [date(2026, 6, 2)], lineage=lineage,
        )

    assert result == {}
    assert any('no disponible' in adv for adv in lineage.advertencias)


def test_extract_vertical_acepta_headers_iso_de_revision() -> None:
    """Bug real (09/07/2026): los XLSX exportados del historial de revisiones
    traen los encabezados de fecha como datetime → pandas los rinde
    '2026-07-06 00:00:00' y el extractor (que solo aceptaba DD/MM/YYYY del
    sheet vivo) devolvía 0 registros — las revisiones intermedias se perdían."""
    from kpi_generator.io.sheets import _extract_cedula_vertical_for_date

    rows = [
        ['CEDULA DE UNIDADES', '', '', '', '', '', ''],
        ['Unidad', 'Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito',
         '2026-07-06 00:00:00', '2026-07-07 00:00:00'],
        ['C135', 'Sandra Luna', 'OFICCE MAX', 'TORTHON', 'DEDICADO', 'Operando', 'Taller'],
    ]

    recs6 = _extract_cedula_vertical_for_date(rows, date(2026, 7, 6))
    assert len(recs6) == 1
    assert recs6[0]['Unidades'] == 'C135'
    assert recs6[0]['Operando'] == 'Operando'

    recs7 = _extract_cedula_vertical_for_date(rows, date(2026, 7, 7))
    assert recs7[0]['Operando'] == 'Taller'
    # Los headers ISO no deben colarse como columnas meta
    assert not any('00:00:00' in k for k in recs6[0])

    # El formato del sheet vivo (DD/MM/YYYY) sigue funcionando igual
    rows_vivo = [
        ['Unidad', 'Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', '06/07/2026'],
        ['C135', 'G', 'OP', 'T', 'C', 'Operando'],
    ]
    assert len(_extract_cedula_vertical_for_date(rows_vivo, date(2026, 7, 6))) == 1


def test_fetch_dates_lista_vacia_no_conecta() -> None:
    """Con 0 fechas solicitadas ni siquiera intenta conectar (carpeta completa
    → modo excel 100% offline como siempre)."""
    from kpi_generator.io.sheets import fetch_dates_from_revisions

    with patch('kpi_generator.io.sheets.gspread.authorize',
               side_effect=AssertionError("no debió conectar")) as mock_auth:
        result = fetch_dates_from_revisions('fake-id', _NOLOG, [])

    assert result == {}
    mock_auth.assert_not_called()

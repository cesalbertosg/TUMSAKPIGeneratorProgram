"""Opción "Completar cédulas faltantes del periodo desde Google Sheets" (v0.7.1).

Fuente excel: la base son las cédulas físicas de la carpeta; con la opción
activada, los días del periodo sin archivo se piden a Google Sheets (historial
de revisiones) y lo que Sheets no tenga se rellena con la cédula del día
anterior, con aviso visible. Con la opción apagada no se consulta Sheets.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from kpi_generator.cli import _build_parser
from kpi_generator.config import Config
from kpi_generator.domain.processor import DataProcessor
from kpi_generator.lineage import CedulaLineage

_NOLOG = lambda *_a, **_k: None  # noqa: E731
_PATCH_FETCH = 'kpi_generator.domain.processor.sheets_io.fetch_dates_from_revisions'


def _fila(dia: date, operacion: str = 'VEND') -> dict:
    return {'Unidades': 'C070', 'Gerencia': 'CUE', 'Operación': operacion,
            'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Fecha Cedula': dia.strftime('%d/%m/%Y'), 'Fecha Cedula_dt': pd.Timestamp(dia)}


def _escenario(tmp_path, dias_fisicos, viajes) -> tuple[str, str]:
    """Carpeta con cédulas físicas + zmov con las fechas de viaje dadas."""
    carpeta = tmp_path / 'Cedulas'
    carpeta.mkdir()
    for dia in dias_fisicos:
        pd.DataFrame([_fila(dia)]).drop(columns=['Fecha Cedula', 'Fecha Cedula_dt']).to_excel(
            carpeta / f"Cedula {dia.strftime('%d%m%Y')}.xlsx", engine='openpyxl', index=False)
    trips = tmp_path / 'zmov.xlsx'
    pd.DataFrame({'Fecha creación': [pd.Timestamp(d) for d in viajes]}).to_excel(
        trips, engine='openpyxl', index=False)
    return str(trips), str(carpeta)


def _cargar(trips, carpeta, completar: bool, lineage: CedulaLineage):
    proc = DataProcessor(log_callback=_NOLOG)
    df, _audit = proc._load_cedulas_by_source(
        'excel', trips, carpeta, None, None, lineage, completar_desde_sheets=completar)
    return df


def test_desactivado_no_consulta_sheets_y_rellena_con_dia_anterior(tmp_path) -> None:
    trips, carpeta = _escenario(tmp_path, [date(2026, 6, 1), date(2026, 6, 2)],
                                [date(2026, 6, 1), date(2026, 6, 4)])
    lineage = CedulaLineage(fuente_solicitada='excel')

    with patch.object(Config, 'CEDULA_SHEET_ID', 'SHEET123'), patch(_PATCH_FETCH) as fetch:
        df = _cargar(trips, carpeta, completar=False, lineage=lineage)

    fetch.assert_not_called()
    assert df is not None and df['Fecha Cedula_dt'].nunique() == 4  # 3 y 4 con la del 2
    assert lineage.completar_sheets is False
    assert not any('SHEETS_ID_CEDULAS' in a for a in lineage.advertencias)
    assert any('sin cédula física (' in a for a in lineage.advertencias)
    assert 'completar desde Sheets: no' in lineage.resumen_linea()
    corrida = lineage.to_dataframe().query("Categoría == 'CORRIDA'")['Detalle']
    assert corrida.str.contains('desde Google Sheets: No').any()


def test_activado_sin_sheet_id_avisa_y_usa_relleno_regular(tmp_path) -> None:
    trips, carpeta = _escenario(tmp_path, [date(2026, 6, 1)],
                                [date(2026, 6, 1), date(2026, 6, 2)])
    lineage = CedulaLineage(fuente_solicitada='excel')

    with patch.object(Config, 'CEDULA_SHEET_ID', ''), patch(_PATCH_FETCH) as fetch:
        df = _cargar(trips, carpeta, completar=True, lineage=lineage)

    fetch.assert_not_called()
    assert df is not None and df['Fecha Cedula_dt'].nunique() == 2
    assert any('falta SHEETS_ID_CEDULAS' in a for a in lineage.advertencias)


def test_activado_pide_a_sheets_los_faltantes_del_periodo_desde_el_dia_1(tmp_path) -> None:
    """El periodo arranca el día 1 del mes aunque el zmov empiece el 3: se piden
    a Sheets los días 1, 2 y 5; Sheets solo tiene el 5 → 1-2 quedan sin cédula
    (no hay una anterior) y se avisa."""
    trips, carpeta = _escenario(tmp_path, [date(2026, 6, 3), date(2026, 6, 4)],
                                [date(2026, 6, 3), date(2026, 6, 5)])
    lineage = CedulaLineage(fuente_solicitada='excel')

    def fetch(_sid, _log, faltantes, **_kw):
        return {d: pd.DataFrame([_fila(d, 'SHEETS')]) for d in faltantes if d.day == 5}

    with patch.object(Config, 'CEDULA_SHEET_ID', 'SHEET123'), \
            patch(_PATCH_FETCH, side_effect=fetch) as mock_fetch:
        df = _cargar(trips, carpeta, completar=True, lineage=lineage)

    assert mock_fetch.call_args.args[2] == [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 5)]
    assert mock_fetch.call_args.kwargs['save_folder'] == carpeta
    assert df is not None
    dia5 = df[df['Fecha Cedula_dt'] == pd.Timestamp('2026-06-05')]
    assert dia5.iloc[0]['Operación'] == 'SHEETS'
    assert lineage.fechas_drive == [date(2026, 6, 5)]
    assert lineage.fechas_sin_cedula == [date(2026, 6, 1), date(2026, 6, 2)]
    assert lineage.completar_sheets is True


def test_cli_completar_cedulas_activo_por_default() -> None:
    base = ['run', '--trips', 'zmov.xlsx', '--fuel', 'zmva.xlsx']
    assert _build_parser().parse_args(base).completar_cedulas is True
    assert _build_parser().parse_args(base + ['--no-completar-cedulas']).completar_cedulas is False

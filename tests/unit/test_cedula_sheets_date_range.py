"""Tests para `_load_cedulas_by_source` (fuente "sheets"): deriva el rango del
zmov y lo delega a `sheets_io.load_cedulas_for_period` (físicos + Drive API).

Beto: "LAS CEDULAS que escribe 'save_cedula_as_completa' SOLO DEBEN SER LAS QUE
PERTENEZCAN AL RANGO DE FECHAS QUE ABARCA EL ZMOV SELECCIONADO" — desde
v0.6.0 esto se garantiza pasando `fecha_min`/`fecha_max` directamente a
`load_cedulas_for_period`, que construye `all_dates` a partir de ese rango
(no hay paso posterior de "acotar"). El guardado de respaldo "Completa" para
fechas resueltas vía Drive API vive dentro de esa función — ver
`test_load_cedulas_for_period.py`.

Tambien cubre el aviso (sin bloquear el pipeline) cuando no se selecciona
carpeta de cedulas, y que sí se aplica el cruce de Operador/No Operador/etc
cuando hay carpeta.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from kpi_generator.domain.processor import DataProcessor

_NOLOG = lambda *_a, **_k: None  # noqa: E731

_PATCH_LOADER = 'kpi_generator.domain.processor.sheets_io.load_cedulas_for_period'


def _df_cedula_periodo(fechas) -> pd.DataFrame:
    """Simula el resultado de `load_cedulas_for_period` para 1 unidad."""
    return pd.DataFrame([
        {
            'Unidades': 'C070', 'Gerencia': 'CUE', 'Operación': 'VEND',
            'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Fecha Cedula': f.strftime('%d/%m/%Y'), 'Fecha Cedula_dt': f,
        }
        for f in fechas
    ])


def _write_trips_xlsx(tmp_path, fechas) -> str:
    df = pd.DataFrame({'Fecha creación': fechas})
    path = tmp_path / "zmov.xlsx"
    df.to_excel(path, engine='openpyxl', index=False)
    return str(path)


def test_sheets_deriva_rango_de_zmov_y_lo_pasa_al_loader(tmp_path) -> None:
    """zmov va del 01 al 09/06 -> load_cedulas_for_period se llama con ese rango exacto."""
    trips_file = _write_trips_xlsx(tmp_path, [
        pd.Timestamp('2026-06-01'), pd.Timestamp('2026-06-09'),
    ])
    proc = DataProcessor(log_callback=_NOLOG)
    fechas = pd.date_range('2026-06-01', '2026-06-09', freq='D')

    with patch(_PATCH_LOADER, return_value=_df_cedula_periodo(fechas)) as mock_loader:
        df, _df_audit = proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder='',
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    assert df is not None
    _args, kwargs = mock_loader.call_args
    assert _args[2] == date(2026, 6, 1)
    assert _args[3] == date(2026, 6, 9)
    assert kwargs['cedulas_folder'] == ''


def test_sheets_sin_carpeta_loggea_warning_sin_abortar(tmp_path) -> None:
    trips_file = _write_trips_xlsx(tmp_path, [pd.Timestamp('2026-06-01')])
    logs: list[str] = []
    proc = DataProcessor(log_callback=logs.append)
    fechas = pd.date_range('2026-06-01', '2026-06-01', freq='D')

    with patch(_PATCH_LOADER, return_value=_df_cedula_periodo(fechas)):
        df, _df_audit = proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder='',
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    assert df is not None
    assert any('[WARN]' in msg and 'Sin carpeta de cédulas' in msg for msg in logs)


def test_sheets_con_carpeta_aplica_crossfill_de_units_extra(tmp_path) -> None:
    """Con carpeta de cedulas, se cruzan Operador/No Operador/Estatus Operador/
    Observaciones desde los archivos locales guardados previamente."""
    trips_file = _write_trips_xlsx(tmp_path, [
        pd.Timestamp('2026-06-01'), pd.Timestamp('2026-06-02'),
    ])
    cedulas_folder = tmp_path / "cedulas"
    cedulas_folder.mkdir()

    proc = DataProcessor(log_callback=_NOLOG)
    fechas = pd.date_range('2026-06-01', '2026-06-02', freq='D')
    df_periodo = _df_cedula_periodo(fechas)
    df_local_no_vacio = pd.DataFrame({'Unidades': ['C070'], 'Operador': ['Juan']})

    with patch(_PATCH_LOADER, return_value=df_periodo), \
         patch('kpi_generator.domain.processor.excel_io.load_local_cedulas_for_crossfill',
               return_value=df_local_no_vacio) as mock_load_local, \
         patch('kpi_generator.domain.processor.excel_io.crossfill_cedulas',
               return_value=(df_periodo, [])) as mock_crossfill:
        df, _df_audit = proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder=str(cedulas_folder),
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    assert df is not None
    mock_load_local.assert_called_once_with(str(cedulas_folder), proc.log)
    mock_crossfill.assert_called_once()

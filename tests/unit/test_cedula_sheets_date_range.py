"""Tests para `_load_cedulas_by_source` (fuente "sheets"): acotar al rango del zmov.

Beto: "LAS CEDULAS que escribe 'save_cedula_as_completa' SOLO DEBEN SER LAS QUE
PERTENEZCAN AL RANGO DE FECHAS QUE ABARCA EL ZMOV SELECCIONADO" — el respaldo
local nunca debe generar "Completa" para dias sin viajes todavia (no asigna
futuro), pero si el sheet ya cubre ese rango la "foto" se usa tal cual.

Tambien cubre el aviso (sin bloquear el pipeline) cuando no se selecciona
carpeta de cedulas: "no dejes implicito el uso" (Beto).
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from kpi_generator.domain.processor import DataProcessor

_NOLOG = lambda *_a, **_k: None  # noqa: E731


def _df_cedula_sheet_mes_completo() -> pd.DataFrame:
    """Simula `load_cedula_from_sheet`: cubre todo junio (30 dias) para 1 unidad."""
    fechas = pd.date_range('2026-06-01', '2026-06-30', freq='D')
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


def test_sheets_acota_cedula_al_rango_de_zmov(tmp_path) -> None:
    """Cedula del sheet trae los 30 dias de junio; zmov solo llega al dia 9."""
    trips_file = _write_trips_xlsx(tmp_path, [
        pd.Timestamp('2026-06-01'), pd.Timestamp('2026-06-09'),
    ])
    proc = DataProcessor(log_callback=_NOLOG)

    with patch.object(proc, 'load_cedula_from_sheets', return_value=_df_cedula_sheet_mes_completo()):
        df, _df_audit = proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder='',
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    assert df is not None
    assert df['Fecha Cedula_dt'].min() == pd.Timestamp('2026-06-01')
    assert df['Fecha Cedula_dt'].max() == pd.Timestamp('2026-06-09')
    assert df['Fecha Cedula_dt'].nunique() == 9


def test_sheets_sin_carpeta_loggea_warning_sin_abortar(tmp_path) -> None:
    trips_file = _write_trips_xlsx(tmp_path, [pd.Timestamp('2026-06-01')])
    logs: list[str] = []
    proc = DataProcessor(log_callback=logs.append)

    with patch.object(proc, 'load_cedula_from_sheets', return_value=_df_cedula_sheet_mes_completo()):
        df, _df_audit = proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder='',
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    assert df is not None
    assert any('[WARN]' in msg and 'Sin carpeta de cédulas' in msg for msg in logs)


def test_sheets_con_carpeta_escribe_completa_solo_para_rango_zmov(tmp_path) -> None:
    trips_file = _write_trips_xlsx(tmp_path, [
        pd.Timestamp('2026-06-01'), pd.Timestamp('2026-06-03'),
    ])
    cedulas_folder = tmp_path / "cedulas"
    cedulas_folder.mkdir()

    proc = DataProcessor(log_callback=_NOLOG)

    with patch.object(proc, 'load_cedula_from_sheets', return_value=_df_cedula_sheet_mes_completo()):
        proc._load_cedulas_by_source(
            'sheets', trips_file, cedulas_folder=str(cedulas_folder),
            cedulas_sheet_id=None, cedulas_tab=None,
        )

    archivos = sorted(p.name for p in cedulas_folder.glob("*.xlsx"))
    assert archivos == [
        "Cedula 01062026 Completa.xlsx",
        "Cedula 02062026 Completa.xlsx",
        "Cedula 03062026 Completa.xlsx",
    ]

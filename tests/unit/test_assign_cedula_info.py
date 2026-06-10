"""Tests para `DataProcessor._assign_cedula_info_optimized`.

Cubre el bug de la cédula vía Sheets (2026-06-10): `load_cedula_from_sheet`
arrastra columnas-metadato extra del header del Sheet (ej. "Denominación")
que también existen en `df_trips`. El merge sin filtrar columnas renombra
los duplicados a `_x`/`_y` y `cols_to_keep` revienta con
`KeyError: "['Denominación'] not in index"`.
"""

from __future__ import annotations

import pandas as pd

from kpi_generator.domain.processor import DataProcessor


def _trips() -> pd.DataFrame:
    df = pd.DataFrame([
        {
            'Equipo Motriz': 'C070',
            'Fecha creación': pd.Timestamp('2026-06-01'),
            'Denominación': 'RUTA CUERNAVACA',
        },
    ])
    df['Fecha creación_date'] = df['Fecha creación'].dt.date
    return df


def _cedulas_con_columna_extra() -> pd.DataFrame:
    """Simula la cédula desde Sheets: incluye 'Denominación' (colisiona con df_trips)."""
    df = pd.DataFrame([
        {
            'Unidades': 'C070',
            'Gerencia': 'GERENCIA NORTE',
            'Operación': 'CUERNAVACA',
            'Tipo de Unidad': 'TRACTOCAMION FULL',
            'Circuito': 'DEDICADO',
            'Operando': 'Operando',
            'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Denominación': 'COLISION CON TRIPS',
        },
    ])
    return df


def test_merge_no_truena_con_columna_colisionante_de_sheets() -> None:
    processor = DataProcessor(log_callback=lambda *_a, **_k: None)

    df_trips = _trips()
    df_cedulas = _cedulas_con_columna_extra()
    unit_mapping = processor.create_unit_mapping(df_cedulas, df_trips['Fecha creación'].max())

    merged = processor._assign_cedula_info_optimized(df_trips, df_cedulas, unit_mapping)

    assert merged.loc[0, 'Denominación'] == 'RUTA CUERNAVACA'
    assert merged.loc[0, 'Operación cedula'] == 'CUERNAVACA TRACTOCAMION FULL'

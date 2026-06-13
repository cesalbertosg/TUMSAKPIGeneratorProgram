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


def _cedula_row(
    unidad: str,
    fecha: str,
    operacion: str = 'CUERNAVACA',
    circuito: str = 'DEDICADO',
    tipo: str = 'TRACTOCAMION FULL',
    gerencia: str = 'GERENCIA NORTE',
    operando: str = 'Operando',
) -> dict:
    return {
        'Unidades': unidad,
        'Gerencia': gerencia,
        'Operación': operacion,
        'Tipo de Unidad': tipo,
        'Circuito': circuito,
        'Operando': operando,
        'Fecha Cedula_dt': pd.Timestamp(fecha),
    }


def _trips_multi(rows: list[tuple[str, str]]) -> pd.DataFrame:
    df = pd.DataFrame([
        {'Equipo Motriz': unidad, 'Fecha creación': pd.Timestamp(fecha)}
        for unidad, fecha in rows
    ])
    df['Fecha creación_date'] = df['Fecha creación'].dt.date
    return df


def test_fantasma_dia_usa_pendiente_no_vigente() -> None:
    """Hueco puntual: la cedula tiene fecha D (de OTRA unidad), pero esta
    unidad no -> Pendiente/POR ASIGNAR para D, no su asignacion vigente."""
    processor = DataProcessor(log_callback=lambda *_a, **_k: None)

    df_cedulas = pd.DataFrame([
        _cedula_row('C070', '2026-06-01'),
        _cedula_row('C999', '2026-06-02', operacion='TOLUCA'),
        _cedula_row('C070', '2026-06-03'),
    ])
    df_trips = _trips_multi([
        ('C070', '2026-06-02'),
        ('C070', '2026-06-03'),
    ])

    unit_mapping = processor.create_unit_mapping(df_cedulas, df_trips['Fecha creación'].max())
    merged = processor._assign_cedula_info_optimized(df_trips, df_cedulas, unit_mapping)

    fantasma = merged[merged['Fecha creación_date'] == pd.Timestamp('2026-06-02').date()].iloc[0]
    assert fantasma['Gerencia'] == 'PENDIENTE'
    assert fantasma['Operando'] == 'SIN ASIGNACIÓN'
    assert fantasma['Operación cedula'] == 'POR ASIGNAR TRACTOCAMION FULL'

    asignado = merged[merged['Fecha creación_date'] == pd.Timestamp('2026-06-03').date()].iloc[0]
    assert asignado['Operación cedula'] == 'CUERNAVACA TRACTOCAMION FULL'


def test_desfase_temporal_usa_vigente() -> None:
    """Desfase de captura: la cedula no tiene NINGUNA fila para la fecha D ->
    se mantiene la asignacion vigente de la unidad (comportamiento actual)."""
    processor = DataProcessor(log_callback=lambda *_a, **_k: None)

    df_cedulas = pd.DataFrame([
        _cedula_row('C070', '2026-06-01'),
    ])
    df_trips = _trips_multi([
        ('C070', '2026-06-01'),
        ('C070', '2026-06-02'),
    ])

    unit_mapping = processor.create_unit_mapping(df_cedulas, df_trips['Fecha creación'].max())
    merged = processor._assign_cedula_info_optimized(df_trips, df_cedulas, unit_mapping)

    desfase = merged[merged['Fecha creación_date'] == pd.Timestamp('2026-06-02').date()].iloc[0]
    assert desfase['Operación cedula'] == 'CUERNAVACA TRACTOCAMION FULL'
    assert desfase['Gerencia'] == 'GERENCIA NORTE'
    assert desfase['Operando'] == 'Operando'

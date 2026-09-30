"""Tests para `DataProcessor._apply_cedula_fallbacks`.

Cubre el "fill adaptativo" del plan "Cedula: fuente versatil + normalizacion +
respaldo local + hoja de inconsistencias":

1. Gerencia/Operación/Circuito faltantes -> `Config.CEDULA_FIELD_DEFAULTS`,
   con inconsistencia registrada.
2. Tipo de Unidad faltante, unidad con histórico de viajes -> inferido vía
   `CLAVE_CATEGORIA_A_TIPO_UNIDAD`.
3. Tipo de Unidad faltante, unidad sin viajes -> inferido por prefijo del
   número económico (`Config.CEDULA_TIPO_UNIDAD_POR_PREFIJO`).
4. `units_extra` (Operador/No Operador/Estatus Operador/Observaciones):
   - si NINGUNA columna está presente, el paso se omite por completo
     (sin ruido en Inconsistencias para fuentes db/excel clásico).
   - si alguna está presente, ffill/bfill por Unidades + "Sin Info" para lo
     que sigue vacío.
5. Acentos/Ñ en columnas categóricas -> texto sin acentos tras el fallback.
6. Tipo de Unidad sin TRACTOCAMION (v0.7.0): se homologa antes de calcular.
7. TORTHON RF en FEDEX / MERCADO LIBRE / DHL (v0.7.1): la limpieza conserva el
   tipo RF; solo la Operación Cedula usa TORTHON.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from kpi_generator.domain.processor import DataProcessor


def _processor() -> DataProcessor:
    return DataProcessor(log_callback=lambda *_a, **_k: None)


def _trips(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty:
        df['Fecha creación'] = pd.to_datetime(df['Fecha creación'])
    return df


# ---------- 1. Defaults Gerencia/Operación/Circuito ----------

def test_defaults_gerencia_operacion_circuito() -> None:
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': '', 'Operación': '', 'Tipo de Unidad': 'FULL',
            'Circuito': '', 'Operando': 'Operando',
        },
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    fila = result.iloc[0]
    assert fila['Gerencia'] == 'Pendiente'
    assert fila['Operación'] == 'SIN ASIGNAR'
    assert fila['Circuito'] == 'TERCERO'

    motivos = {(i['Campo'], i['Valor Aplicado'], i['Motivo']) for i in proc._inconsistencias}
    assert ('Gerencia', 'Pendiente', 'Faltante en cédula') in motivos
    assert ('Operación', 'SIN ASIGNAR', 'Faltante en cédula') in motivos
    assert ('Circuito', 'TERCERO', 'Faltante en cédula') in motivos


# ---------- 2. Tipo de Unidad desde histórico de viajes ----------

def test_tipo_unidad_inferido_de_historico_de_viajes() -> None:
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': np.nan,
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
        },
    ])
    df_trips = _trips([
        {'Equipo Motriz': 'C999', 'Fecha creación': '2026-06-01', 'ClaveCategoria': 'FULL'},
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, df_trips)

    # Vocabulario de la cedula: ClaveCategoria FULL -> 'FULL' (sin TRACTOCAMION, v0.7.0)
    assert result.iloc[0]['Tipo de Unidad'] == 'FULL'
    motivos = {(i['Campo'], i['Valor Aplicado'], i['Motivo']) for i in proc._inconsistencias}
    assert ('Tipo de Unidad', 'FULL', 'Tipo de Unidad inferido de histórico de viajes') in motivos


# ---------- 3. Tipo de Unidad por prefijo de numero economico ----------

def test_tipo_unidad_inferido_de_prefijo_sin_viajes() -> None:
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'L05', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': np.nan,
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
        },
        {
            'Unidades': 'C123', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': np.nan,
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
        },
        {
            'Unidades': 'T045', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': np.nan,
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
        },
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    tipos = result.set_index('Unidades')['Tipo de Unidad']
    assert tipos['L05'] == 'CAMIONETA'
    assert tipos['C123'] == 'TORTHON'
    assert tipos['T045'] == 'SENCILLO'

    motivos = [i['Motivo'] for i in proc._inconsistencias if i['Campo'] == 'Tipo de Unidad']
    assert all(m == 'Tipo de Unidad inferido de prefijo de número económico' for m in motivos)


# ---------- 4. units_extra: gating + ffill/bfill ----------

def test_units_extra_ausentes_no_genera_inconsistencias() -> None:
    """Sin ninguna columna units_extra en la cedula original, el paso 4 se omite."""
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': 'FULL',
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
        },
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    assert 'Operador' not in result.columns
    campos = {i['Campo'] for i in proc._inconsistencias}
    assert not campos & {'Operador', 'No Operador', 'Estatus Operador', 'Observaciones'}


def test_units_extra_presente_aplica_ffill_bfill_y_sin_info() -> None:
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': 'FULL',
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Operador': 'Juan Perez', 'Observaciones': np.nan,
        },
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-02'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': 'FULL',
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Operador': np.nan, 'Observaciones': np.nan,
        },
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    result = result.sort_values('Fecha Cedula_dt').reset_index(drop=True)
    # Operador del dia 1 se propaga (ffill) al dia 2.
    assert result.loc[0, 'Operador'] == 'Juan Perez'
    assert result.loc[1, 'Operador'] == 'Juan Perez'
    # Observaciones no tiene ningun valor real -> "Sin Info" en ambos dias.
    assert result.loc[0, 'Observaciones'] == 'Sin Info'
    assert result.loc[1, 'Observaciones'] == 'Sin Info'
    # No Operador / Estatus Operador tampoco tenian valores -> "Sin Info".
    assert (result['No Operador'] == 'Sin Info').all()
    assert (result['Estatus Operador'] == 'Sin Info').all()

    motivos_operador = [i['Motivo'] for i in proc._inconsistencias if i['Campo'] == 'Operador']
    assert 'Completado por ffill/bfill' in motivos_operador


# ---------- 5. Normalizacion de acentos ----------

def test_normaliza_acentos_en_columnas_categoricas() -> None:
    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C999', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'Gerencia Cuernavaca Súr', 'Operación': 'Distribución',
            'Tipo de Unidad': 'Tórthon RF', 'Circuito': 'Dedicado',
            'Operando': 'Gestoría',
        },
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    fila = result.iloc[0]
    assert fila['Gerencia'] == 'Gerencia Cuernavaca Sur'
    assert fila['Operación'] == 'Distribucion'
    assert fila['Tipo de Unidad'] == 'Torthon RF'
    assert fila['Circuito'] == 'Dedicado'
    assert fila['Operando'] == 'Gestoria'


# ---------- 6. Tipo de Unidad sin TRACTOCAMION (v0.7.0) ----------

def test_tipo_unidad_tractocamion_se_homologa_antes_de_calcular() -> None:
    """No existe TRACTOCAMION: 'TRACTOCAMION <tipo>' -> <tipo>, solo -> SENCILLO."""
    base = {'Fecha Cedula_dt': pd.Timestamp('2026-06-01'), 'Gerencia': 'CUE',
            'Operación': 'VEND', 'Circuito': 'DEDICADO', 'Operando': 'Operando'}
    df_cedulas = pd.DataFrame([
        {**base, 'Unidades': 'T001', 'Tipo de Unidad': 'TRACTOCAMION'},
        {**base, 'Unidades': 'T002', 'Tipo de Unidad': 'TRACTOCAMION FULL'},
        {**base, 'Unidades': 'T003', 'Tipo de Unidad': 'Tractocamión Patio'},
        {**base, 'Unidades': 'T004', 'Tipo de Unidad': 'SENCILLO'},
    ])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    tipos = result.set_index('Unidades')['Tipo de Unidad']
    assert tipos.to_dict() == {'T001': 'SENCILLO', 'T002': 'FULL', 'T003': 'PATIO', 'T004': 'SENCILLO'}
    homologados = {(i['Unidad'], i['Valor Original'], i['Valor Aplicado']) for i in proc._inconsistencias
                   if i['Motivo'] == 'Tipo de Unidad homologado (no existe TRACTOCAMION)'}
    assert homologados == {('T001', 'TRACTOCAMION', 'SENCILLO'),
                           ('T002', 'TRACTOCAMION FULL', 'FULL'),
                           ('T003', 'Tractocamion Patio', 'PATIO')}


# ---------- 7. TORTHON RF en operaciones sin refrigerado (v0.7.1) ----------

def test_torthon_rf_sustituto_conserva_tipo_y_su_operacion_cedula_usa_torthon() -> None:
    """Caso real C153 (Sheet: TORTHON RF en MERCADO LIBRE, circuito especial).
    Decisión Beto (2026-09-30): el Tipo de Unidad sigue diciendo RF; solo la
    Operación Cedula es MERCADO LIBRE TORTHON. La limpieza no toca el dato ni
    lo manda a Inconsistencias."""
    df_cedulas = pd.DataFrame([{
        'Unidades': 'C153', 'Fecha Cedula_dt': pd.Timestamp('2026-09-25'),
        'Gerencia': 'Sandra Luna', 'Operación': 'MERCADO LIBRE', 'Tipo de Unidad': 'TORTHON RF',
        'Circuito': 'TERCERO', 'Operando': 'Operando',
    }])
    proc = _processor()

    result = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())
    mapping = proc.create_unit_mapping(result, datetime(2026, 9, 25))

    assert result.iloc[0]['Tipo de Unidad'] == 'TORTHON RF'
    assert mapping['C153']['Tipo de Unidad'] == 'TORTHON RF'
    assert mapping['C153']['Operación cedula'] == 'MERCADO LIBRE TORTHON'
    assert not [i for i in proc._inconsistencias if i['Campo'] == 'Tipo de Unidad']

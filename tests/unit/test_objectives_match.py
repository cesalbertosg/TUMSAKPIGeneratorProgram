"""Test de integración: match de `Operación Cedula` pese a acentos.

Antes del plan "Cedula: fuente versatil + normalizacion + respaldo local +
hoja de inconsistencias", un acento en `Tipo de Unidad` (cédula) o en
`Operación Cedula` (objetivos) rompía el match silenciosamente -> Objetivo
KM/Viajes caía a 0.

Este test reproduce el flujo real:
1. `_apply_cedula_fallbacks` normaliza `Tipo de Unidad` de la cédula
   ("Tractocamión Full" -> "Tractocamion Full").
2. `create_unit_mapping` arma `Operación cedula` = "CUERNAVACA TRACTOCAMION FULL"
   (DEDICADO es circuito especial -> usa Tipo de Unidad).
3. `Operación Cedula` de objetivos ("Cuernavaca Tractocamión Full") se
   normaliza igual que en `load_data` (normalize_text + upper).
4. `process_objectives` debe encontrar el match -> Objetivo KM Diario > 0.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from kpi_generator.domain.equipment import normalize_text
from kpi_generator.domain.processor import DataProcessor


def test_objetivo_match_pese_a_acento_en_tipo_de_unidad() -> None:
    proc = DataProcessor(log_callback=lambda *_a, **_k: None)

    df_cedulas = pd.DataFrame([
        {
            'Unidades': 'C070', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'CUERNAVACA',
            'Tipo de Unidad': 'Tractocamión Full', 'Circuito': 'DEDICADO',
            'Operando': 'Operando',
        },
    ])
    df_cedulas = proc._apply_cedula_fallbacks(df_cedulas, pd.DataFrame())

    analysis_date = datetime(2026, 6, 1)
    unit_mapping = proc.create_unit_mapping(df_cedulas, analysis_date)

    assert unit_mapping['C070']['Operación cedula'] == 'CUERNAVACA TRACTOCAMION FULL'

    df_objectives = pd.DataFrame([
        {
            'Gerencia': 'CUE', 'Operación Cedula': 'Cuernavaca Tractocamión Full',
            'Objetivo KM': 3000, 'Objetivo Viajes': 60,
        },
    ])
    # Misma normalizacion que `load_data` aplica a df_objectives.
    df_objectives['Operación Cedula'] = (
        df_objectives['Operación Cedula'].astype(str).str.strip().map(normalize_text).str.upper()
    )

    obj_mapping = proc.process_objectives(df_objectives, unit_mapping, analysis_date)

    assert 'CUERNAVACA TRACTOCAMION FULL' in obj_mapping
    info = obj_mapping['CUERNAVACA TRACTOCAMION FULL']
    assert info['Objetivo KM'] == 3000
    assert info['Objetivo Viajes'] == 60
    assert info['Objetivo KM Diario'] > 0
    assert info['Objetivo Viajes Diario'] > 0

"""Cuadre Por Operacion vs Por Equipo (v0.7.0): todo se asigna dia por dia.

Corre `EquipmentAggregator` + `OpcedulaAggregator` + `post_calcular_tendencia`
sobre un escenario con reasignacion a mitad de periodo, una OpCedula retirada,
una unidad que termina Sin Asignacion, una unidad sin cedula y arrastres, y
verifica que las sumas de Por Operacion (incluidos POR ASIGNAR y Pendiente)
cuadran con Por Equipo.

Corte 04/06 (dias_corrientes=4, dias_restantes=26). Objetivo diario:
VEND CENTRO 100, VEND NORTE 50, OLD OP 30 (retirada), POR ASIGNAR SENCILLO 0.
  C070: 4 dias VEND CENTRO
  C071: dias 1-2 VEND NORTE, 3-4 VEND CENTRO (reasignada)
  C072: dias 1-2 OLD OP, dia 3 VEND NORTE, dia 4 Sin Asignacion -> POR ASIGNAR SENCILLO
  C100: 4 dias VEND NORTE
  FL27: sin cedula, viaja como POR ASIGNAR SENCILLO
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.equipment import EquipmentAggregator
from kpi_generator.domain.opcedula import OpcedulaAggregator, post_calcular_tendencia
from kpi_generator.domain.period import PeriodContext

SPECIAL_CIRCUITS = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}
_NOLOG = lambda *_a, **_k: None  # noqa: E731

_ASIGNADA = {
    'VEND CENTRO': {'Gerencia': 'CUE', 'Operación': 'VEND', 'Circuito': 'CENTRO'},
    'VEND NORTE': {'Gerencia': 'CUE', 'Operación': 'VEND', 'Circuito': 'NORTE'},
    'OLD OP': {'Gerencia': 'CUE', 'Operación': 'OLD', 'Circuito': 'OP'},
}


def _cedula() -> pd.DataFrame:
    dias = {
        'C070': ['VEND CENTRO'] * 4,
        'C071': ['VEND NORTE', 'VEND NORTE', 'VEND CENTRO', 'VEND CENTRO'],
        'C072': ['OLD OP', 'OLD OP', 'VEND NORTE', None],
        'C100': ['VEND NORTE'] * 4,
    }
    rows = []
    for unidad, ops in dias.items():
        for dia, op in enumerate(ops, start=1):
            base = {'Unidades': unidad, 'Fecha Cedula_dt': pd.Timestamp(f'2026-06-0{dia}'),
                    'Tipo de Unidad': 'SENCILLO'}
            if op is None:
                rows.append({**base, 'Gerencia': 'Pendiente', 'Operación': 'Por Asignar',
                             'Circuito': 'POR ASIGNAR', 'Operando': 'Sin Asignacion'})
            else:
                rows.append({**base, **_ASIGNADA[op], 'Operando': 'Operando'})
    return pd.DataFrame(rows)


def _viajes() -> pd.DataFrame:
    rows = [
        # (equipo, dia, OpCedula del dia, km cargado, km vacio, remolque, dolly)
        ('C070', 1, 'VEND CENTRO', 100, 20, '40331', None),
        ('C070', 3, 'VEND CENTRO', 80, 10, '40331', None),
        ('C071', 1, 'VEND NORTE', 50, 5, None, None),
        ('C071', 4, 'VEND CENTRO', 70, 0, None, 'D001'),
        ('C072', 1, 'OLD OP', 40, 10, None, None),
        ('C072', 4, 'POR ASIGNAR SENCILLO', 30, 0, None, None),
        ('C100', 2, 'VEND NORTE', 60, 6, None, None),
        ('FL27', 2, 'POR ASIGNAR SENCILLO', 25, 5, '40332', None),
    ]
    df = pd.DataFrame([{
        'Número de Viaje': n, 'Equipo Motriz': eq,
        'Fecha creación': pd.Timestamp(f'2026-06-0{dia} 10:00'),
        'Operación cedula': opc, 'Tipo de Unidad': 'SENCILLO', 'ClaveCategoria': 'SENCILLO',
        'KM_cargado': kc, 'KM_vacio': kv, 'KM_total': kc + kv, 'Diesel_LTS': (kc + kv) / 2,
        'Viajes_count': 1, 'Equipo Remolque 1': rem, 'Equipo Remolque 2': None, 'Equipo Dolly': dolly,
    } for n, (eq, dia, opc, kc, kv, rem, dolly) in enumerate(rows, start=1)])
    df['Fecha creación_date'] = df['Fecha creación'].dt.date
    return df


OBJ = {
    'VEND CENTRO': {'Objetivo KM Diario': 100, 'Objetivo Viajes Diario': 2},
    'VEND NORTE': {'Objetivo KM Diario': 50, 'Objetivo Viajes Diario': 1},
    'OLD OP': {'Objetivo KM Diario': 30, 'Objetivo Viajes Diario': 1},
    'POR ASIGNAR SENCILLO': {'Objetivo KM Diario': 0, 'Objetivo Viajes Diario': 0},
}


@pytest.fixture(scope='module')
def resultado():
    period = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp('2026-06-04'))
    agg = EquipmentAggregator(_cedula(), _viajes(), OBJ, period, SPECIAL_CIRCUITS, log_callback=_NOLOG)
    df_kpi = agg.aggregate()
    df_op = OpcedulaAggregator(
        df_kpi, OBJ, period,
        df_detalle_opcedula=agg.aggregate_detalle_opcedula(),
        df_objetivo_opcedula=agg.aggregate_objetivo_opcedula(),
        log_callback=_NOLOG,
    ).aggregate()
    post_calcular_tendencia(df_kpi, df_op, period, OBJ)
    return df_kpi, df_op


def test_filas_por_asignar_por_tipo_y_pendiente_para_retiradas(resultado) -> None:
    _, df_op = resultado
    assert list(df_op['Operacion Cedula']) == ['VEND CENTRO', 'VEND NORTE', 'POR ASIGNAR SENCILLO', 'Pendiente']
    por_op = df_op.set_index('Operacion Cedula')
    assert por_op.loc['POR ASIGNAR SENCILLO', 'Motrices Titulares'] == 2   # C072 y FL27
    assert por_op.loc['POR ASIGNAR SENCILLO', 'KM Total'] == 60            # C072 dia 4 (30) + FL27 (30)
    assert por_op.loc['Pendiente', 'Motrices Titulares'] == 0
    assert por_op.loc['Pendiente', 'KM Total'] == 50                       # C072 en OLD OP
    assert por_op.loc['Pendiente', 'Objetivo KM'] == 60                    # 2 dias × 30


def test_objetivo_dia_por_dia(resultado) -> None:
    _, df_op = resultado
    por_op = df_op.set_index('Operacion Cedula')
    assert por_op.loc['VEND CENTRO', 'Objetivo KM Corte'] == 600   # C070 400 + C071 200
    assert por_op.loc['VEND CENTRO', 'Objetivo KM'] == 5800        # + 100 × 2 titulares × 26
    assert por_op.loc['VEND NORTE', 'Objetivo KM Corte'] == 350    # C071 100 + C072 50 + C100 200
    assert por_op.loc['VEND NORTE', 'Objetivo KM'] == 1650         # + 50 × 1 titular × 26


@pytest.mark.parametrize('col_op,col_eq', [
    ('Objetivo KM Corte', 'Objetivo KM Corte'),
    ('Complemento KM Objetivo', 'Complemento KM Objetivo'),
    ('Objetivo KM', 'Objetivo KM Total'),
    ('Objetivo Viajes', 'Objetivo Viajes Total'),
    ('KM Cargado', 'KM Cargado'),
    ('KM Vacio', 'KM Vacio'),
    ('KM Total', 'KM Total'),
    ('Diesel LTS', 'Diesel LTS'),
    ('Viajes', 'Viajes'),
    ('Tendencia KM', 'Tendencia KM'),
    ('Tendencia Viajes', 'Tendencia Viajes'),
    ('Potencial KM', 'Potencial KM'),
])
def test_sumas_cuadran_con_por_equipo(resultado, col_op: str, col_eq: str) -> None:
    df_kpi, df_op = resultado
    motrices = df_kpi[df_kpi['Tipo Equipo'] == 'Motriz']
    assert df_op[col_op].sum() == pytest.approx(motrices[col_eq].sum(), abs=0.05)


def test_conteos_de_equipos_cuadran(resultado) -> None:
    df_kpi, df_op = resultado
    motrices = df_kpi[df_kpi['Tipo Equipo'] == 'Motriz']
    arrastres = df_kpi[df_kpi['Tipo Equipo'] != 'Motriz']
    assert df_op['Motrices Titulares'].sum() == len(motrices) == 5
    assert df_op['Remolques Titulares'].sum() == (arrastres['Tipo Equipo'] == 'Remolque').sum() == 2
    assert df_op['Dollies Titulares'].sum() == (arrastres['Tipo Equipo'] == 'Dolly').sum() == 1
    por_op = df_op.set_index('Operacion Cedula')
    assert por_op.loc['POR ASIGNAR SENCILLO', 'Remolques Titulares'] == 1  # 40332 (motriz dominante FL27)

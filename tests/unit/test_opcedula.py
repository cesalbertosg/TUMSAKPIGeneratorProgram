"""Tests para `domain.opcedula.OpcedulaAggregator` y `post_calcular_tendencia`.

Cubre la reforma de Por Operacion v0.5.0: agregacion por OpCedula desde
df_equipos, conteos de status, objetivos consolidados, % Operativo,
promedios y propagacion de tendencia.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.equipment import EQUIPO_OUTPUT_COLS, _NUMERIC_EQUIPO_COLS
from kpi_generator.domain.opcedula import (
    OPCEDULA_OUTPUT_COLS,
    OpcedulaAggregator,
    post_calcular_tendencia,
)
from kpi_generator.domain.period import PeriodContext


def _period(corte: str = '2026-06-10') -> PeriodContext:
    return PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp(corte))


def _equipos(rows: list[dict]) -> pd.DataFrame:
    """Construye df_equipos con columnas minimas + defaults para el resto.

    Defaults numericos como 0.0 (float), no 0 (int): las columnas que
    `post_calcular_tendencia` rellena via `.at[]` (Tendencia/Potencial
    KM/Viajes) reciben valores con decimales — un default int64 revienta
    con "Invalid value ... for dtype 'int64'" en pandas al asignar un float
    fraccionario. La produccion real nunca pisa esto porque
    `_consolidar_fila` ya inicializa esas columnas como 0.0.
    """
    df = pd.DataFrame(rows)
    for col in EQUIPO_OUTPUT_COLS:
        if col not in df.columns:
            df[col] = 0.0 if col in _NUMERIC_EQUIPO_COLS else ''
    return df[EQUIPO_OUTPUT_COLS]


_DETALLE_COLS = ['Equipo Motriz', 'Operación cedula', 'KM Cargado', 'KM Vacio',
                  'KM Total', 'Diesel LTS', 'Rendimiento', 'Viajes', 'Densidad Viaje',
                  'Dias Activo']


def _detalle(rows: list[dict]) -> pd.DataFrame:
    """Construye df_detalle_opcedula (salida de aggregate_detalle_opcedula)."""
    df = pd.DataFrame(rows)
    for col in _DETALLE_COLS:
        if col not in df.columns:
            df[col] = 0
    return df[_DETALLE_COLS]


# ---------- aggregate() basico ----------

def test_agrega_una_fila_por_opcedula() -> None:
    """3 motrices: 2 en VEND CENTRO, 1 en VEND NORTE -> 2 filas."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 800, 'Viajes': 40},
        {'Equipo Motriz': 'C071', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Taller', 'Dias Asignado': 10, 'Dias Activo': 2, 'KM Total': 200, 'Viajes': 10},
        {'Equipo Motriz': 'C100', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND NORTE',
         'Gerencia': 'MEX', 'Operacion': 'VEND', 'Circuito': 'NORTE', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 9, 'KM Total': 900, 'Viajes': 45},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()

    assert len(df_op) == 2
    vend_centro = df_op[df_op['Operacion Cedula'] == 'VEND CENTRO'].iloc[0]
    assert vend_centro['Motrices Titulares'] == 2
    assert vend_centro['Operando'] == 1
    assert vend_centro['Taller'] == 1
    assert vend_centro['KM Total'] == 1000
    assert vend_centro['Viajes'] == 50
    assert vend_centro['Dias unidad asignados'] == 20
    assert vend_centro['Dias unidad activos'] == 10


def test_excluye_por_asignar() -> None:
    """Equipos con OpCedula que arranca con POR ASIGNAR van a la fila 'Pendiente'."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'POR ASIGNAR FULL',
         'Estatus': 'Sin Asignacion'},
        {'Equipo Motriz': 'C200', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 100, 'Viajes': 5},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert len(df_op) == 2
    assert 'VEND CENTRO' in set(df_op['Operacion Cedula'])
    pendiente = df_op[df_op['Operacion Cedula'] == 'Pendiente'].iloc[0]
    assert pendiente['Gerencia'] == 'Pendiente'
    assert pendiente['Motrices Titulares'] == 1


def test_excluye_arrastres() -> None:
    """Arrastres no son titulares de OpCedula."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 800, 'Viajes': 40},
        {'Equipo Motriz': '40331', 'Tipo Equipo': 'Remolque', 'Operacion Cedula': 'VEND CENTRO',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 0, 'Viajes': 0},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert df_op.iloc[0]['Motrices Titulares'] == 1  # solo el motriz


# ---------- Atribucion dia-por-dia (df_detalle_opcedula) ----------

def test_split_dia_por_dia_huerfana_va_a_pendiente() -> None:
    """Caso C135: parte de su historico fue bajo una OpCedula ya retirada.

    El KM de esos dias debe caer en 'Pendiente', no en su vigente actual.
    """
    df_eq = _equipos([
        {'Equipo Motriz': 'C135', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'ZORRO TORTHON',
         'Gerencia': 'CUE', 'Operacion': 'ZORRO', 'Circuito': 'TORTHON', 'Tipo de Unidad': 'TORTHON',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 10, 'KM Total': 614, 'Viajes': 30},
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'C135', 'Operación cedula': 'OFICCE MAX TORTHON',
         'KM Total': 114, 'Viajes': 5},
        {'Equipo Motriz': 'C135', 'Operación cedula': 'ZORRO TORTHON',
         'KM Total': 500, 'Viajes': 25},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()

    zorro = df_op[df_op['Operacion Cedula'] == 'ZORRO TORTHON'].iloc[0]
    assert zorro['KM Total'] == 500  # ya no incluye los 114 km de la huerfana
    assert zorro['Motrices Utilizadas'] == 1
    assert zorro['Motrices Titulares'] == 1  # vigente no cambia

    pendiente = df_op[df_op['Operacion Cedula'] == 'Pendiente'].iloc[0]
    assert pendiente['KM Total'] == 114
    assert pendiente['Motrices Utilizadas'] == 1  # C135 cuenta aqui tambien
    assert pendiente['Motrices Titulares'] == 0  # nadie tiene Pendiente como vigente


def test_split_dia_por_dia_dos_opcedulas_reales() -> None:
    """Caso L7: parte de su historico fue bajo otra OpCedula vigente de OTRO equipo.

    El KM debe repartirse en ambas filas (no concentrarse 100% en su propia
    vigente), y 'Motrices Utilizadas' debe contar L7 en ambas mientras
    'Motrices Titulares' sigue siendo solo por vigente.
    """
    df_eq = _equipos([
        {'Equipo Motriz': 'L7', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'MARS CAMIONETA',
         'Gerencia': 'CUE', 'Operacion': 'MARS', 'Circuito': 'CAMIONETA', 'Tipo de Unidad': 'CAMIONETA',
         'Estatus': 'Operando', 'Dias Asignado': 14, 'Dias Activo': 14, 'KM Total': 900, 'Viajes': 40},
        {'Equipo Motriz': 'L8', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'AXION LOG CAMIONETA',
         'Gerencia': 'CUE', 'Operacion': 'AXION', 'Circuito': 'LOG', 'Tipo de Unidad': 'CAMIONETA',
         'Estatus': 'Operando', 'Dias Asignado': 14, 'Dias Activo': 14, 'KM Total': 700, 'Viajes': 35},
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'L7', 'Operación cedula': 'AXION LOG CAMIONETA',
         'KM Total': 641, 'Viajes': 28},
        {'Equipo Motriz': 'L7', 'Operación cedula': 'MARS CAMIONETA',
         'KM Total': 259, 'Viajes': 12},
        {'Equipo Motriz': 'L8', 'Operación cedula': 'AXION LOG CAMIONETA',
         'KM Total': 700, 'Viajes': 35},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()

    assert len(df_op) == 2  # sin huerfanas/fantasmas en este caso -> sin fila Pendiente

    mars = df_op[df_op['Operacion Cedula'] == 'MARS CAMIONETA'].iloc[0]
    assert mars['KM Total'] == 259  # ya no incluye los 641 km que L7 hizo bajo AXION
    assert mars['Motrices Utilizadas'] == 1
    assert mars['Motrices Titulares'] == 1

    axion = df_op[df_op['Operacion Cedula'] == 'AXION LOG CAMIONETA'].iloc[0]
    assert axion['KM Total'] == 1341  # 700 (L8, su vigente) + 641 (L7, reasignado)
    assert axion['Motrices Utilizadas'] == 2  # L7 y L8 viajaron bajo AXION
    assert axion['Motrices Titulares'] == 1  # solo L8 es vigente de AXION


# ---------- Objetivos consolidados ----------

def test_objetivos_consolidados_al_cierre() -> None:
    """Obj al CIERRE = corte + complemento futuro.

    Setup: corte 10/06 (dias_corrientes=10, dias_restantes=20, dias_mes=30),
    obj_diario=100, 2 titulares.
      Obj Corte = 100 × 2 × 10 = 2,000
      Complemento = 100 × 2 × 20 = 4,000
      Obj Total cierre = 6,000
    """
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 500, 'Viajes': 25},
        {'Equipo Motriz': 'C071', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 9, 'KM Total': 600, 'Viajes': 30},
    ])
    obj = {'VEND CENTRO': {'Objetivo KM Diario': 100, 'Objetivo Viajes Diario': 5}}
    df_op = OpcedulaAggregator(df_eq, obj_mapping=obj, period=_period('2026-06-10'),
                                log_callback=lambda *_a, **_k: None).aggregate()
    fila = df_op.iloc[0]
    assert fila['Objetivo KM Corte'] == 2000
    assert fila['Complemento KM Objetivo'] == 4000
    assert fila['Objetivo KM'] == 6000
    assert fila['Objetivo Viajes Corte'] == 100
    assert fila['Complemento Viajes Objetivo'] == 200
    assert fila['Objetivo Viajes'] == 300
    # Cumplimiento = KM real total / Obj cierre = 1100 / 6000 = 18.33
    assert fila['Cumplimiento KM %'] == 18.33
    # Viajes total = 55; Cump = 55 / 300 = 18.33
    assert fila['Cumplimiento Viajes %'] == 18.33


# ---------- % Operativo y promedios ----------

def test_porcentaje_operativo_consolidado() -> None:
    """% Op = Σ Dias activos / (titulares × dias corrientes)."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 8, 'KM Total': 800, 'Viajes': 40},
        {'Equipo Motriz': 'C071', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 2, 'KM Total': 200, 'Viajes': 10},
    ])
    # corte = dia 10 -> denom = 2*10 = 20; activos = 10 -> 50%
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period('2026-06-10'),
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert df_op.iloc[0]['% Operativo'] == 50.0


def test_promedio_km_dia_unidad() -> None:
    """Promedio = KM Total / Σ Dias Activo (v0.6.9; sin detalle historico cae
    a Dias Activo de df_equipos, legacy). En este fixture Dias Activo == Dias
    Asignado para ambas unidades (nadie tiene dias muertos), asi que coincide
    numericamente con la formula vieja — la dilucion se cubre aparte en
    `test_promedio_no_diluye_por_unidad_totalmente_inactiva`."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 10, 'KM Total': 500, 'Viajes': 25},
        {'Equipo Motriz': 'C071', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 5, 'Dias Activo': 5, 'KM Total': 300, 'Viajes': 15},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()
    # 800 km / 15 dias-unidad ACTIVOS = 53.3333
    assert df_op.iloc[0]['Promedio KM dia unidad'] == pytest.approx(53.3333, abs=0.01)


def test_promedio_no_diluye_por_unidad_totalmente_inactiva() -> None:
    """v0.6.9 — caso MARS TORTHON RF real (2026-07-13): una unidad con 0 dias
    activos NO debe rebajar el rendimiento/dia de las unidades que si
    trabajaron. Antes se dividia entre TODOS los dias asignados (incluidos
    los 12 de la unidad muerta); ahora solo entre dias con actividad real."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C109', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'MARS TORTHON RF',
         'Gerencia': 'VB', 'Operacion': 'MARS', 'Circuito': 'TORTHON RF', 'Tipo de Unidad': 'TORTHON',
         'Estatus': 'Sin Asignacion', 'Dias Asignado': 12, 'Dias Activo': 0, 'KM Total': 0, 'Viajes': 0},
        {'Equipo Motriz': 'C123', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'MARS TORTHON RF',
         'Gerencia': 'VB', 'Operacion': 'MARS', 'Circuito': 'TORTHON RF', 'Tipo de Unidad': 'TORTHON',
         'Estatus': 'Operando', 'Dias Asignado': 12, 'Dias Activo': 10, 'KM Total': 643, 'Viajes': 8},
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'C123', 'Operación cedula': 'MARS TORTHON RF',
         'KM Total': 643, 'Viajes': 8, 'Dias Activo': 10},
        # C109 no aparece en el detalle: cero viajes reales ese mes.
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()
    # Antes: 643 / (12+12) = 26.79. Ahora: 643 / 10 = 64.3 (solo dias de C123).
    assert df_op.iloc[0]['Promedio KM dia unidad'] == pytest.approx(64.3, abs=0.01)


# ---------- post_calcular_tendencia ----------

def test_tendencia_individual_y_agregada() -> None:
    """Tendencia KM = KM + restantes × promedio × %Op/100."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 10, 'KM Total': 500, 'Viajes': 25,
         '% Operativo': 100.0},
    ])
    period = _period('2026-06-10')  # dias_restantes = 20
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=period,
                                log_callback=lambda *_a, **_k: None).aggregate()
    # promedio = 500 / 10 = 50
    assert df_op.iloc[0]['Promedio KM dia unidad'] == 50.0

    post_calcular_tendencia(df_eq, df_op, period)
    # KM Real (500) + 20 dias × 50 × 1.0 = 1500
    assert df_eq.iloc[0]['Tendencia KM'] == 1500.0
    # Agregado en df_op
    assert df_op.iloc[0]['Tendencia KM'] == 1500.0


def test_tendencia_por_asignar_no_proyecta() -> None:
    """Equipo POR ASIGNAR no tiene promedio -> tendencia = KM real."""
    df_eq = _equipos([
        {'Equipo Motriz': 'T999', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'POR ASIGNAR FULL',
         'Estatus': 'Sin Asignacion', 'KM Total': 100, 'Viajes': 5, '% Operativo': 10.0},
    ])
    period = _period('2026-06-10')
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=period,
                                log_callback=lambda *_a, **_k: None).aggregate()
    post_calcular_tendencia(df_eq, df_op, period)
    assert df_eq.iloc[0]['Tendencia KM'] == 100  # solo KM real
    assert df_eq.iloc[0]['Tendencia Viajes'] == 5
    # v0.6.9: tambien el Potencial queda en 0 (no solo la Tendencia)
    assert df_eq.iloc[0]['Potencial KM'] == 0.0
    assert df_eq.iloc[0]['Potencial Viajes'] == 0.0


def test_tendencia_no_duplica_descuento_de_capacidad() -> None:
    """v0.6.9 — el bug real (Beto, 2026-07-13): el promedio de grupo diluido
    por una unidad muerta (`test_promedio_no_diluye...`) YA arregla la mitad
    del problema; este test verifica la otra mitad — que el rendimiento
    limpio de grupo NO se vuelve a diluir por el %Operativo PROPIO de cada
    unidad activa por partida doble.

    Corte dia 12 de julio (dias_mes=31) -> peso_evidencia = min(12/4.96,1)=1.0,
    el piso de Objetivo no influye — aisla Pasos 1-3 puros.

    3 unidades en 'TEST OP': U1 totalmente inactiva (0 dias activos), U2 y U3
    con 6 dias activos cada una (de 12 asignados) y 300 km/15 viajes cada una.
      Rendimiento_dia_activo = (300+300) / (0+6+6) = 50 km/dia
      Potencial(U2) = 19 dias_restantes × 50 × 50%Op = 475 -> Tendencia=775
    (la formula vieja diluia con Σ Dias Asignado=36 -> promedio 16.67 ->
    potencial=158.35 -> Tendencia≈458.37, mas de 300 km menos por unidad).
    """
    df_eq = _equipos([
        {'Equipo Motriz': 'U1', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'TEST OP',
         'Gerencia': 'G', 'Operacion': 'OP', 'Circuito': 'C', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Sin Asignacion', 'Dias Asignado': 12, 'Dias Activo': 0,
         'KM Total': 0, 'Viajes': 0, '% Operativo': 0.0},
        {'Equipo Motriz': 'U2', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'TEST OP',
         'Gerencia': 'G', 'Operacion': 'OP', 'Circuito': 'C', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 12, 'Dias Activo': 6,
         'KM Total': 300, 'Viajes': 15, '% Operativo': 50.0},
        {'Equipo Motriz': 'U3', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'TEST OP',
         'Gerencia': 'G', 'Operacion': 'OP', 'Circuito': 'C', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 12, 'Dias Activo': 6,
         'KM Total': 300, 'Viajes': 15, '% Operativo': 50.0},
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'U2', 'Operación cedula': 'TEST OP',
         'KM Total': 300, 'Viajes': 15, 'Dias Activo': 6},
        {'Equipo Motriz': 'U3', 'Operación cedula': 'TEST OP',
         'KM Total': 300, 'Viajes': 15, 'Dias Activo': 6},
    ])
    period = PeriodContext(anio=2026, mes=7, fecha_ultimo_viaje=pd.Timestamp('2026-07-12'))
    # julio: dias_mes=31, dias_corrientes=12, dias_restantes=19
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=period,
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert df_op.iloc[0]['Promedio KM dia unidad'] == pytest.approx(50.0, abs=0.01)

    post_calcular_tendencia(df_eq, df_op, period)

    u2 = df_eq[df_eq['Equipo Motriz'] == 'U2'].iloc[0]
    assert u2['Potencial KM'] == pytest.approx(475.0, abs=0.5)
    assert u2['Tendencia KM'] == pytest.approx(775.0, abs=0.5)
    u1 = df_eq[df_eq['Equipo Motriz'] == 'U1'].iloc[0]
    assert u1['Tendencia KM'] == 0.0  # unidad muerta sigue en 0, sin cambio
    # Total OpCedula: 0 + 775 + 775 = 1550 (vs ~1417 con la formula vieja)
    assert df_op.iloc[0]['Tendencia KM'] == pytest.approx(1550.0, abs=1.0)


def test_reasignacion_no_resetea_confiabilidad_por_un_solo_dia() -> None:
    """v0.6.9 — el %Operativo que usa Tendencia es el mismo que calcula
    EquipmentAggregator sobre TODO el historial de viajes del mes de la
    unidad (agnostico de OpCedula) — `post_calcular_tendencia` solo lo
    REUTILIZA (`row['% Operativo']`), nunca lo recalcula por OpCedula
    vigente. Este test fija ese contrato explicitamente: una unidad recien
    reasignada con 11/11 dias activos en su operacion anterior y 0/1 en la
    nueva NO debe verse forzada a %Operativo=0% solo por el ultimo dia malo
    — el valor ya viene blindado desde df_equipos (91.67%, no se toca aqui).
    """
    df_eq = _equipos([
        {'Equipo Motriz': 'R1', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'NUEVA OP',
         'Gerencia': 'G', 'Operacion': 'OP', 'Circuito': 'C', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Sin Asignacion', 'Dias Asignado': 12, 'Dias Activo': 11,
         'KM Total': 0, 'Viajes': 0, '% Operativo': 91.67},  # 11 de 12 dias del PERIODO
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'R1', 'Operación cedula': 'NUEVA OP',
         'KM Total': 0, 'Viajes': 0, 'Dias Activo': 0},  # 0 dias activos EN esta OpCedula
    ])
    period = _period('2026-06-12')
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=period,
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()
    post_calcular_tendencia(df_eq, df_op, period)
    r1 = df_eq.iloc[0]
    # Rendimiento de NUEVA OP es 0 (nadie trabajo ahi todavia) -> Potencial=0
    # de todas formas en este caso (sin evidencia de la operacion nueva), PERO
    # el %Operativo=91.67 (historial completo del equipo) llego intacto a
    # esta funcion sin que se resetee a 0 por el mal dia en la reasignacion.
    assert float(df_eq.iloc[0]['% Operativo']) == pytest.approx(91.67, abs=0.01)


def test_piso_objetivo_domina_en_corte_muy_temprano() -> None:
    """v0.6.9 — corte dia 2 de un mes de 31 dias: peso_evidencia =
    min(2/(0.16*31), 1) = min(2/4.96, 1) = 0.4032. El potencial mezcla el
    ritmo observado (poca evidencia, inestable) con el piso de Objetivo KM
    diario, ponderado por esa evidencia."""
    df_eq = _equipos([
        {'Equipo Motriz': 'E1', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'TEMPRANO OP',
         'Gerencia': 'G', 'Operacion': 'OP', 'Circuito': 'C', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 2, 'Dias Activo': 2,
         'KM Total': 20, 'Viajes': 1, '% Operativo': 100.0},
    ])
    df_detalle = _detalle([
        {'Equipo Motriz': 'E1', 'Operación cedula': 'TEMPRANO OP',
         'KM Total': 20, 'Viajes': 1, 'Dias Activo': 2},
    ])
    obj = {'TEMPRANO OP': {'Objetivo KM Diario': 40, 'Objetivo Viajes Diario': 2}}
    period = PeriodContext(anio=2026, mes=7, fecha_ultimo_viaje=pd.Timestamp('2026-07-02'))
    # dias_mes=31, dias_corrientes=2, dias_restantes=29
    df_op = OpcedulaAggregator(df_eq, obj_mapping=obj, period=period,
                                df_detalle_opcedula=df_detalle,
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert df_op.iloc[0]['Promedio KM dia unidad'] == pytest.approx(10.0, abs=0.01)

    post_calcular_tendencia(df_eq, df_op, period, obj)

    # peso=0.403226; obs=29*10*1=290; piso=29*40*1=1160
    # potencial = 0.403226*290 + 0.596774*1160 = 809.29
    e1 = df_eq.iloc[0]
    assert e1['Potencial KM'] == pytest.approx(809.29, abs=0.5)
    assert e1['Tendencia KM'] == pytest.approx(829.29, abs=0.5)
    # Sin piso (peso=1, como en los tests de corte avanzado) daria solo
    # 20+290=310 — el piso lo eleva porque hay poca evidencia todavia.
    assert e1['Tendencia KM'] > 310


def test_potencial_km_es_diferencia_entre_tendencia_y_real() -> None:
    """Potencial KM/Viajes = Tendencia - Real, consistentemente en df_equipos
    y en el agregado de df_opcedula."""
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 10, 'Dias Activo': 10, 'KM Total': 500, 'Viajes': 25,
         '% Operativo': 100.0},
    ])
    period = _period('2026-06-10')
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=period,
                                log_callback=lambda *_a, **_k: None).aggregate()
    post_calcular_tendencia(df_eq, df_op, period)

    fila = df_eq.iloc[0]
    assert fila['Potencial KM'] == pytest.approx(fila['Tendencia KM'] - fila['KM Total'], abs=0.01)
    assert fila['Potencial Viajes'] == pytest.approx(fila['Tendencia Viajes'] - fila['Viajes'], abs=0.01)
    fila_op = df_op.iloc[0]
    assert fila_op['Potencial KM'] == pytest.approx(fila_op['Tendencia KM'] - fila_op['KM Total'], abs=0.01)


# ---------- Schema ----------

def test_schema_de_salida_completo() -> None:
    df_eq = _equipos([
        {'Equipo Motriz': 'C070', 'Tipo Equipo': 'Motriz', 'Operacion Cedula': 'VEND CENTRO',
         'Gerencia': 'CUE', 'Operacion': 'VEND', 'Circuito': 'CENTRO', 'Tipo de Unidad': 'FULL',
         'Estatus': 'Operando', 'Dias Asignado': 5, 'Dias Activo': 4, 'KM Total': 100, 'Viajes': 5},
    ])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert list(df_op.columns) == OPCEDULA_OUTPUT_COLS


def test_aggregate_vacio_devuelve_schema_vacio() -> None:
    df_eq = _equipos([])
    df_op = OpcedulaAggregator(df_eq, obj_mapping={}, period=_period(),
                                log_callback=lambda *_a, **_k: None).aggregate()
    assert df_op.empty
    assert list(df_op.columns) == OPCEDULA_OUTPUT_COLS

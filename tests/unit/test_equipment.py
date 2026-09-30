"""Tests para `domain.equipment.EquipmentAggregator` y helpers.

Cubre las reglas clave de v0.5.0:
- Clasificacion Motriz/Remolque/Dolly desde Tipo de Unidad BD.
- Mapeo de status BD a categorias canonicas (incluye Otros Status resiliente).
- Asignacion vigente motriz (ultimo dia, egreso, nunca asignado).
- Motriz dominante para arrastres (mayor numero de co-viajes).
- Conteo de dias: ejes 1+2+3 (Asignado, sub-status, Activo).
- Objetivo prorrateado por dia asignado (sin importar status).
- Arrastres: status reconstruido desde viajes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.equipment import (
    CLAVE_CATEGORIA_A_TIPO_UNIDAD,
    EQUIPO_OUTPUT_COLS,
    AsignacionVigente,
    EquipmentAggregator,
    categoria_status,
    clasificar_tipo_equipo,
    normalizar_opcedula,
    normalizar_tipo_unidad,
    normalize_text,
    operacion_cedula,
)
from kpi_generator.domain.period import PeriodContext


SPECIAL_CIRCUITS = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}


# ---------- Helpers ----------

def _ced(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty:
        df['Fecha Cedula_dt'] = pd.to_datetime(df['Fecha Cedula_dt'])
    return df


def _trips(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty and 'Fecha creación' in df.columns:
        df['Fecha creación'] = pd.to_datetime(df['Fecha creación'])
        df['Fecha creación_date'] = df['Fecha creación'].dt.date
    return df


def _period(corte: str = '2026-06-05') -> PeriodContext:
    """PeriodContext de junio 2026 con corte configurable."""
    return PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp(corte))


def _agg(df_cedulas, df_trips, obj_mapping=None, corte='2026-06-05'):
    return EquipmentAggregator(
        df_cedulas=df_cedulas, df_trips=df_trips,
        obj_mapping=obj_mapping, period=_period(corte),
        special_circuits=SPECIAL_CIRCUITS,
        log_callback=lambda *_a, **_k: None,
    )


# ---------- clasificar_tipo_equipo ----------

@pytest.mark.parametrize("tipo_unidad,esperado", [
    ('SENCILLO', 'Motriz'),
    ('FULL', 'Motriz'),
    ('TORTHON RF', 'Motriz'),
    ('CAMIONETA', 'Motriz'),
    ('DESCONOCIDO', 'Motriz'),  # default
    ('', 'Motriz'),              # vacio = Motriz por defecto
    ('EQUIPO REMOLQUE', 'Remolque'),
    ('REMOLQUE', 'Remolque'),
    ('CAJA', 'Remolque'),
    ('THERMO', 'Remolque'),
    ('EQUIPO DOLLY', 'Dolly'),
    ('DOLLY', 'Dolly'),
])
def test_clasificar_tipo_equipo(tipo_unidad: str, esperado: str) -> None:
    assert clasificar_tipo_equipo(tipo_unidad) == esperado


# ---------- categoria_status ----------

@pytest.mark.parametrize("estatus,esperado", [
    ('Operando', 'Operando'),
    ('Taller', 'Taller'),
    ('Gestoria', 'Gestoria'),
    ('Puesto A Punto', 'Puesto A Punto'),
    ('Sin Asignacion', 'Sin Asignacion'),
    # Resilientes -> Otros Status
    ('Activo', 'Otros Status'),
    ('Baja', 'Otros Status'),
    ('Inhabilitado', 'Otros Status'),
    ('Cargada', 'Otros Status'),
    ('Renovacion Licencia', 'Otros Status'),
    ('Venta', 'Otros Status'),
    ('Operador Incapacitado', 'Otros Status'),
    ('Status Desconocido Futuro', 'Otros Status'),
    ('', 'Otros Status'),
    (None, 'Otros Status'),
    # Acentos (Excel/Sheets) -> canonico sin acento
    ('Gestoría', 'Gestoria'),
    ('Sin Asignación', 'Sin Asignacion'),
    # Caso "a" minuscula (Excel/Sheets) vs canonico "A" mayuscula
    ('Puesto a Punto', 'Puesto A Punto'),
    ('puesto a punto', 'Puesto A Punto'),
    # Espacios extra
    ('  Operando  ', 'Operando'),
])
def test_categoria_status(estatus: str, esperado: str) -> None:
    assert categoria_status(estatus or '') == esperado


# ---------- NaN directo (bug real 10/07/2026) ----------
#
# Una celda vacia leida de Excel sin dtype=str/fillna llega como NaN (float),
# no ''. NaN es truthy en Python (`not float('nan')` == False), asi que un
# guard `if not valor:` no lo atrapa y `.strip()` revienta con
# "'float' object has no attribute 'strip'". io/excel.py ya blinda la lectura
# (fillna('') en columnas de units), pero estas funciones deben ser a prueba
# de NaN por si mismas ante cualquier otra fuente futura.

def test_clasificar_tipo_equipo_con_nan_no_truena() -> None:
    assert clasificar_tipo_equipo(float('nan')) == 'Motriz'
    assert clasificar_tipo_equipo(pd.NA) == 'Motriz'


def test_categoria_status_con_nan_no_truena() -> None:
    assert categoria_status(float('nan')) == 'Otros Status'
    assert categoria_status(pd.NA) == 'Otros Status'


# ---------- normalize_text ----------

@pytest.mark.parametrize("value,esperado", [
    ('Gestoría', 'Gestoria'),
    ('Sin Asignación', 'Sin Asignacion'),
    ('Núñez', 'Nunez'),
    ('TRACTOCAMIÓN FULL', 'TRACTOCAMION FULL'),
    ('Operando', 'Operando'),
    ('', ''),
])
def test_normalize_text(value: str, esperado: str) -> None:
    assert normalize_text(value) == esperado


# ---------- Vocabulario de Tipo de Unidad (v0.7.0: no existe TRACTOCAMION) ----------

@pytest.mark.parametrize("valor,esperado", [
    ('TRACTOCAMION', 'SENCILLO'),
    ('TRACTOCAMION SENCILLO', 'SENCILLO'),
    ('TRACTOCAMION FULL', 'FULL'),
    ('Tractocamión Patio', 'PATIO'),
    ('  tractocamion  ', 'SENCILLO'),
    ('SENCILLO', 'SENCILLO'),
    ('TORTHON RF', 'TORTHON RF'),
    ('Camioneta', 'Camioneta'),  # sin TRACTOCAMION: sin cambios
    ('', ''),
])
def test_normalizar_tipo_unidad(valor: str, esperado: str) -> None:
    assert normalizar_tipo_unidad(valor) == esperado


def test_normalizar_tipo_unidad_con_nan_no_truena() -> None:
    assert pd.isna(normalizar_tipo_unidad(float('nan')))


@pytest.mark.parametrize("clave,esperado", [
    ('CUERNAVACA TRACTOCAMION FULL', 'CUERNAVACA FULL'),
    ('CUERNAVACA TRACTOCAMION', 'CUERNAVACA SENCILLO'),
    ('POR ASIGNAR TRACTOCAMION SENCILLO', 'POR ASIGNAR SENCILLO'),
    ('STB SENCILLO', 'STB SENCILLO'),
])
def test_normalizar_opcedula(clave: str, esperado: str) -> None:
    assert normalizar_opcedula(clave) == esperado


def test_clave_categoria_usa_vocabulario_de_cedula() -> None:
    assert not any('TRACTOCAMION' in tipo for tipo in CLAVE_CATEGORIA_A_TIPO_UNIDAD.values())
    assert CLAVE_CATEGORIA_A_TIPO_UNIDAD['SENCILLO'] == 'SENCILLO'


@pytest.mark.parametrize("operacion,circuito,tipo,esperado", [
    ('MERCADO LIBRE', 'DEDICADO', 'TORTHON RF', 'MERCADO LIBRE TORTHON'),
    ('Mercado Libre', 'Tercero', 'Torthon RF', 'MERCADO LIBRE TORTHON'),
    ('MERCADO LIBRE DFP', 'TERCERO', 'TORTHON RF', 'MERCADO LIBRE DFP TORTHON'),  # variante
    ('FEDEX', 'DEDICADO', 'TORTHON RF', 'FEDEX TORTHON'),
    ('FEDEX VILLA', 'DEDICADO', 'TORTHON RF', 'FEDEX VILLA TORTHON'),
    ('DHL', 'TERCERO', 'TORTHON RF', 'DHL TORTHON'),
    ('SORIANA', 'DEDICADO', 'TORTHON RF', 'SORIANA TORTHON RF'),        # sí maneja RF
    ('MERCADO LIBRE', 'DEDICADO', 'SENCILLO RF', 'MERCADO LIBRE SENCILLO RF'),  # solo torthon
    ('FEDEXPRESS', 'DEDICADO', 'TORTHON RF', 'FEDEXPRESS TORTHON RF'),  # no es FEDEX
    ('FEDEX', 'CANCUN', 'TORTHON RF', 'FEDEX CANCUN'),                   # circuito normal
])
def test_operacion_cedula_torthon_rf_sustituto(operacion, circuito, tipo, esperado) -> None:
    """FEDEX, MERCADO LIBRE y DHL no manejan torthon refrigerado (Beto,
    2026-09-30): un TORTHON RF ahí es sustituto → su OpCedula usa TORTHON."""
    assert operacion_cedula(operacion, circuito, tipo, SPECIAL_CIRCUITS) == esperado


def test_opcedula_misma_regla_en_todo_el_pipeline() -> None:
    """Las cuatro rutas que arman la Operación Cedula dan el mismo resultado
    (antes cada una tenía su copia de la regla)."""
    from kpi_generator.domain.change_tracker import ChangeTracker
    from kpi_generator.domain.comodato import ComodatoManager
    from kpi_generator.domain.processor import DataProcessor

    args = ('MERCADO LIBRE', 'DEDICADO', 'TORTHON RF')
    esperado = 'MERCADO LIBRE TORTHON'
    assert operacion_cedula(*args) == esperado
    assert DataProcessor(log_callback=lambda *_a, **_k: None)._get_operacion_cedula(*args) == esperado
    assert ChangeTracker(lambda *_a, **_k: None)._get_operacion_cedula(*args) == esperado
    assert ComodatoManager()._get_operacion_cedula_comodato(*args) == esperado


def test_por_equipo_conserva_rf_y_opcedula_sin_rf() -> None:
    """Por Equipo: el camión sigue siendo TORTHON RF; su Operación Cedula es
    MERCADO LIBRE TORTHON y le toca el objetivo de MERCADO LIBRE TORTHON."""
    ced = _ced([
        {'Unidades': 'C153', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'Sandra Luna',
         'Operación': 'MERCADO LIBRE', 'Tipo de Unidad': 'TORTHON RF', 'Circuito': 'TERCERO',
         'Operando': 'Operando'},
    ])
    obj = {'MERCADO LIBRE TORTHON': {'Objetivo KM Diario': 150, 'Objetivo Viajes Diario': 1}}
    fila = _agg(ced, _trips([]), obj_mapping=obj, corte='2026-06-01').aggregate().iloc[0]
    assert fila['Tipo de Unidad'] == 'TORTHON RF'
    assert fila['Operacion Cedula'] == 'MERCADO LIBRE TORTHON'
    assert fila['Objetivo KM Corte'] == 150


def test_pendiente_opcedula_en_mayusculas() -> None:
    """La OpCedula POR ASIGNAR empata con la que el procesador da a los viajes."""
    assert AsignacionVigente.pendiente('Sencillo').operacion_cedula == 'POR ASIGNAR SENCILLO'
    assert AsignacionVigente.pendiente().operacion_cedula == 'POR ASIGNAR'
    assert AsignacionVigente.pendiente(float('nan')).operacion_cedula == 'POR ASIGNAR'


# ---------- Asignacion vigente motriz ----------

def test_asignacion_vigente_ultimo_dia() -> None:
    """Equipo con cambio Operacion CENTRO -> NORTE: vigente = NORTE."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-05', 'Gerencia': 'MEX',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'NORTE',
         'Operando': 'Taller'},
    ])
    agg = _agg(ced, _trips([]))
    df = agg.aggregate()
    fila = df[df['Equipo Motriz'] == 'C070'].iloc[0]
    assert fila['Operacion'] == 'VEND'
    assert fila['Circuito'] == 'NORTE'
    assert fila['Gerencia'] == 'MEX'
    assert fila['Operacion Cedula'] == 'VEND NORTE'
    assert fila['Estatus'] == 'Taller'


def test_asignacion_vigente_egreso_por_asignar() -> None:
    """Ultimo dia es 'Sin Asignacion' -> POR ASIGNAR / Pendiente."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-05', 'Gerencia': '',
         'Operación': '', 'Tipo de Unidad': 'FULL', 'Circuito': '',
         'Operando': 'Sin Asignacion'},
    ])
    agg = _agg(ced, _trips([]))
    fila = agg.aggregate().iloc[0]
    assert fila['Gerencia'] == 'Pendiente'
    assert fila['Operacion'] == 'POR ASIGNAR'
    assert fila['Estatus'] == 'Sin Asignacion'


def test_phantom_sin_cedula_por_asignar() -> None:
    """Unidad solo en viajes (nunca en cedula) -> POR ASIGNAR."""
    trips = _trips([
        {'Equipo Motriz': 'T999', 'Fecha creación': '2026-06-03', 'Número de Viaje': 1,
         'KMLiqCargadoFinal': 100, 'KMLiqVacioFinal': 50, 'ClaveCategoria': 'X'},
    ])
    agg = _agg(_ced([]), trips)
    fila = agg.aggregate().iloc[0]
    assert fila['Equipo Motriz'] == 'T999'
    assert fila['Tipo Equipo'] == 'Motriz'
    assert fila['Gerencia'] == 'Pendiente'
    assert fila['Operacion'] == 'POR ASIGNAR'
    assert fila['Dias Asignado'] == 0
    assert fila['Dias Sin Asignacion'] == 5  # corte = dia 5
    assert fila['Dias Activo'] == 1


def test_sin_cedula_titular_de_la_asignacion_de_su_ultimo_viaje() -> None:
    """v0.7.0: sin cedula en el periodo, la foto es la asignacion de su ultimo
    dia con viaje ('POR ASIGNAR <tipo>'), la misma fila donde cuenta como
    utilizada (caso real FL27/UT002)."""
    trips = _trips([
        {'Equipo Motriz': 'FL27', 'Fecha creación': '2026-06-02', 'Número de Viaje': 1,
         'ClaveCategoria': 'SENCILLO', 'Tipo de Unidad': 'SENCILLO',
         'Operación cedula': 'POR ASIGNAR SENCILLO'},
        {'Equipo Motriz': 'FL27', 'Fecha creación': '2026-06-04', 'Número de Viaje': 2,
         'ClaveCategoria': 'SENCILLO', 'Tipo de Unidad': 'SENCILLO',
         'Operación cedula': 'POR ASIGNAR SENCILLO'},
    ])
    fila = _agg(_ced([]), trips).aggregate().iloc[0]
    assert fila['Operacion Cedula'] == 'POR ASIGNAR SENCILLO'
    assert fila['Tipo de Unidad'] == 'SENCILLO'
    assert fila['Gerencia'] == 'Pendiente'
    assert fila['Estatus'] == 'Sin Asignacion'


def test_arrastre_de_motriz_sin_cedula_hereda_su_foto() -> None:
    trips = _trips([
        {'Equipo Motriz': 'UT002', 'Equipo Remolque 1': '40777', 'Fecha creación': '2026-06-03',
         'Número de Viaje': 1, 'ClaveCategoria': 'CAMIONETA', 'Tipo de Unidad': 'CAMIONETA'},
    ])
    df = _agg(_ced([]), trips).aggregate()
    fila = df[df['Equipo Motriz'] == '40777'].iloc[0]
    assert fila['Tipo Equipo'] == 'Remolque'
    assert fila['Operacion Cedula'] == 'POR ASIGNAR CAMIONETA'


# ---------- Dias por status ----------

def test_conteo_dias_motriz_basico() -> None:
    """5 dias del periodo: 3 Operando, 1 Taller, 1 sin cedula -> Sin Asignacion."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-02', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-03', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Taller'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-04', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        # Dia 5 sin cedula -> Sin Asignacion
    ])
    fila = _agg(ced, _trips([])).aggregate().iloc[0]
    assert fila['Dias Asignado'] == 4
    assert fila['Dias Sin Asignacion'] == 1
    assert fila['Dias Operando'] == 3
    assert fila['Dias Taller'] == 1
    assert fila['Dias Otros Status'] == 0
    # Suma de Eje 1
    assert fila['Dias Asignado'] + fila['Dias Sin Asignacion'] == 5
    # Suma de Eje 2 dentro de Asignado
    eje2 = sum(fila[f'Dias {s}'] for s in
               ['Operando', 'Disponible', 'Sin Operador', 'Taller', 'Gestoria',
                'Descanso', 'Rescate', 'Puesto A Punto', 'Otros Status'])
    assert eje2 == fila['Dias Asignado']


def test_status_raro_va_a_otros_status() -> None:
    """Status BD 'Activo' / 'Baja' / nuevo -> Dias Otros Status, NO se pierde."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Activo'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-02', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Baja'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-03', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'NUEVO STATUS DEL FUTURO'},
    ])
    fila = _agg(ced, _trips([]), corte='2026-06-03').aggregate().iloc[0]
    assert fila['Dias Asignado'] == 3
    assert fila['Dias Otros Status'] == 3
    assert fila['Dias Operando'] == 0


# ---------- Dias Activo ----------

def test_dias_activo_solo_viajes_validos() -> None:
    """Dias Activo cuenta dias unicos con viaje no-comodato."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
    ])
    trips = _trips([
        {'Equipo Motriz': 'C070', 'Fecha creación': '2026-06-01', 'Número de Viaje': 1,
         'ClaveCategoria': 'X'},
        {'Equipo Motriz': 'C070', 'Fecha creación': '2026-06-01', 'Número de Viaje': 2,
         'ClaveCategoria': 'X'},  # mismo dia -> no duplica
        {'Equipo Motriz': 'C070', 'Fecha creación': '2026-06-03', 'Número de Viaje': 3,
         'ClaveCategoria': 'COM'},  # comodato -> NO cuenta
        {'Equipo Motriz': 'C070', 'Fecha creación': '2026-06-04', 'Número de Viaje': 4,
         'ClaveCategoria': 'X'},
    ])
    fila = _agg(ced, trips).aggregate().iloc[0]
    assert fila['Dias Activo'] == 2  # 01 y 04 (03 es comodato)


def test_porcentaje_operativo() -> None:
    """% Operativo = Dias Activo / Dias Corrientes * 100."""
    trips = _trips([
        {'Equipo Motriz': 'T999', 'Fecha creación': '2026-06-01', 'Número de Viaje': 1,
         'ClaveCategoria': 'X'},
        {'Equipo Motriz': 'T999', 'Fecha creación': '2026-06-02', 'Número de Viaje': 2,
         'ClaveCategoria': 'X'},
    ])
    # corte = dia 5 -> Dias corrientes = 5, Dias Activo = 2 -> 40%
    fila = _agg(_ced([]), trips, corte='2026-06-05').aggregate().iloc[0]
    assert fila['% Operativo'] == 40.0


# ---------- Objetivos prorrateados ----------

def test_objetivo_prorrateado_mezcla_opcedulas() -> None:
    """Objetivo al CIERRE = corte (dias asignados) + complemento (vigente × dias_restantes).

    Setup: corte 04/06 (dias_corrientes=4, dias_restantes=26).
    Dias 1-2 en VEND CENTRO (100/dia), Dias 3-4 en VEND NORTE (10/dia).
    Asignacion vigente: VEND NORTE.
    """
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-02', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Taller'},  # Taller dentro de VEND CENTRO sigue aportando
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-03', 'Gerencia': 'MEX',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'NORTE',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-04', 'Gerencia': 'MEX',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'NORTE',
         'Operando': 'Operando'},
    ])
    obj = {
        'VEND CENTRO': {'Objetivo KM Diario': 100, 'Objetivo Viajes Diario': 2},
        'VEND NORTE': {'Objetivo KM Diario': 10, 'Objetivo Viajes Diario': 1},
    }
    fila = _agg(ced, _trips([]), obj_mapping=obj, corte='2026-06-04').aggregate().iloc[0]
    # Corte: 2*100 + 2*10 = 220
    assert fila['Objetivo KM Corte'] == 220
    # Complemento: vigente=VEND NORTE (10/dia) × 26 dias restantes
    assert fila['Complemento KM Objetivo'] == 260
    # Total al cierre = 220 + 260
    assert fila['Objetivo KM Total'] == 480
    # Viajes: corte 2*2 + 2*1 = 6; complemento 1×26 = 26; total = 32
    assert fila['Objetivo Viajes Corte'] == 6
    assert fila['Complemento Viajes Objetivo'] == 26
    assert fila['Objetivo Viajes Total'] == 32


def test_aggregate_objetivo_opcedula_dia_por_dia() -> None:
    """v0.7.0: el objetivo de corte se reparte por la OpCedula de cada dia y su
    suma cuadra con el `Objetivo KM Corte` de Por Equipo. Dias Sin Asignacion
    no aportan; los arrastres no tienen objetivo."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-02', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Taller'},  # Taller sigue aportando
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-03', 'Gerencia': 'MEX',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'NORTE',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-04', 'Gerencia': 'MEX',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'NORTE',
         'Operando': 'Sin Asignacion'},  # no aporta
        {'Unidades': 'R100', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'REMOLQUE', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},  # arrastre: fuera del detalle
    ])
    obj = {
        'VEND CENTRO': {'Objetivo KM Diario': 100, 'Objetivo Viajes Diario': 2},
        'VEND NORTE': {'Objetivo KM Diario': 10, 'Objetivo Viajes Diario': 1},
    }
    agg = _agg(ced, _trips([]), obj_mapping=obj, corte='2026-06-04')
    df_obj = agg.aggregate_objetivo_opcedula()

    por_op = df_obj.set_index('Operación cedula')
    assert set(df_obj['Equipo Motriz']) == {'C070'}
    assert por_op.loc['VEND CENTRO', 'Objetivo KM Corte'] == 200
    assert por_op.loc['VEND CENTRO', 'Objetivo Viajes Corte'] == 4
    assert por_op.loc['VEND NORTE', 'Objetivo KM Corte'] == 10
    motriz = agg.aggregate().set_index('Equipo Motriz').loc['C070']
    assert df_obj['Objetivo KM Corte'].sum() == motriz['Objetivo KM Corte'] == 210


def test_aggregate_objetivo_opcedula_sin_cedula_vacio_con_schema() -> None:
    df_obj = _agg(_ced([]), _trips([])).aggregate_objetivo_opcedula()
    assert df_obj.empty
    assert list(df_obj.columns) == ['Equipo Motriz', 'Operación cedula',
                                    'Objetivo KM Corte', 'Objetivo Viajes Corte']


def test_objetivo_dia_sin_objetivo_aporta_cero() -> None:
    """OpCedula sin entry en obj_mapping -> 0 aporte (no rompe)."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'NUEVA', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
    ])
    fila = _agg(ced, _trips([]), obj_mapping={}, corte='2026-06-01').aggregate().iloc[0]
    assert fila['Objetivo KM Total'] == 0
    assert fila['Cump KM %'] is None


# ---------- Arrastres ----------

def test_arrastre_hereda_motriz_dominante() -> None:
    """Arrastre 40331 viaja 3 veces con C070 y 1 con C200 -> dominante = C070."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C200', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'MEX',
         'Operación': 'DIST', 'Tipo de Unidad': 'TORTHON', 'Circuito': 'NORTE',
         'Operando': 'Operando'},
    ])
    trips = _trips([
        {'Equipo Motriz': 'C070', 'Equipo Remolque 1': '40331',
         'Fecha creación': '2026-06-01', 'Número de Viaje': 1, 'ClaveCategoria': 'X'},
        {'Equipo Motriz': 'C070', 'Equipo Remolque 1': '40331',
         'Fecha creación': '2026-06-02', 'Número de Viaje': 2, 'ClaveCategoria': 'X'},
        {'Equipo Motriz': 'C070', 'Equipo Remolque 1': '40331',
         'Fecha creación': '2026-06-03', 'Número de Viaje': 3, 'ClaveCategoria': 'X'},
        {'Equipo Motriz': 'C200', 'Equipo Remolque 1': '40331',
         'Fecha creación': '2026-06-04', 'Número de Viaje': 4, 'ClaveCategoria': 'X'},
    ])
    df = _agg(ced, trips, corte='2026-06-04').aggregate()
    fila = df[df['Equipo Motriz'] == '40331'].iloc[0]
    assert fila['Tipo Equipo'] == 'Remolque'  # heuristica: aparece solo en Eq Remolque 1
    assert fila['Operacion'] == 'VEND'  # heredo de C070
    assert fila['Circuito'] == 'CENTRO'
    assert fila['Dias Activo'] == 4  # viajo en 4 dias


def test_arrastre_estatus_reconstruido() -> None:
    """Arrastre tiene Operando = dias con viaje, Disponible = Asignado - Activo."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-02', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-03', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
    ])
    trips = _trips([
        {'Equipo Motriz': 'C070', 'Equipo Remolque 1': '40331',
         'Fecha creación': '2026-06-01', 'Número de Viaje': 1, 'ClaveCategoria': 'X'},
        # 02 y 03 sin viaje del remolque
    ])
    df = _agg(ced, trips, corte='2026-06-03').aggregate()
    fila = df[df['Equipo Motriz'] == '40331'].iloc[0]
    assert fila['Dias Asignado'] == 3  # hereda de C070
    assert fila['Dias Activo'] == 1
    assert fila['Dias Operando'] == 1  # solo el dia con viaje
    assert fila['Dias Disponible'] == 2  # asignado - activo
    assert fila['Dias Taller'] == 0  # NO hereda taller del motriz


# ---------- Schema ----------

def test_schema_de_salida_completo() -> None:
    """El DataFrame siempre devuelve EQUIPO_OUTPUT_COLS en el orden correcto."""
    ced = _ced([
        {'Unidades': 'C070', 'Fecha Cedula_dt': '2026-06-01', 'Gerencia': 'CUE',
         'Operación': 'VEND', 'Tipo de Unidad': 'FULL', 'Circuito': 'CENTRO',
         'Operando': 'Operando'},
    ])
    df = _agg(ced, _trips([]), corte='2026-06-01').aggregate()
    assert list(df.columns) == EQUIPO_OUTPUT_COLS
    assert len(df) == 1


# ---------- aggregate_detalle_opcedula ----------

def test_aggregate_detalle_opcedula_agrupa_por_equipo_y_opcedula() -> None:
    """1 fila por combinacion (Equipo Motriz, Operación cedula), SUM via _metricas_operativas.

    Caso L7: viajo bajo 2 OpCedulas distintas en el periodo (reasignacion
    mid-mes) -> 2 filas, cada una con el KM/Diesel/Viajes solo de esos dias.
    """
    trips = _trips([
        {'Equipo Motriz': 'l7', 'Fecha creación': '2026-06-01', 'Número de Viaje': 1,
         'ClaveCategoria': 'X', 'Operación cedula': 'MARS CAMIONETA',
         'KM_cargado': 100, 'KM_vacio': 20, 'KM_total': 120, 'Diesel_LTS': 30, 'Viajes_count': 1},
        {'Equipo Motriz': 'l7', 'Fecha creación': '2026-06-02', 'Número de Viaje': 2,
         'ClaveCategoria': 'X', 'Operación cedula': 'AXION LOG CAMIONETA',
         'KM_cargado': 200, 'KM_vacio': 40, 'KM_total': 240, 'Diesel_LTS': 50, 'Viajes_count': 1},
        {'Equipo Motriz': 'l7', 'Fecha creación': '2026-06-03', 'Número de Viaje': 3,
         'ClaveCategoria': 'X', 'Operación cedula': 'AXION LOG CAMIONETA',
         'KM_cargado': 50, 'KM_vacio': 10, 'KM_total': 60, 'Diesel_LTS': 12, 'Viajes_count': 1},
        # Comodato bajo AXION: excluido de df_trips_validos, no debe sumar.
        {'Equipo Motriz': 'l7', 'Fecha creación': '2026-06-04', 'Número de Viaje': 4,
         'ClaveCategoria': 'COM', 'Operación cedula': 'AXION LOG CAMIONETA',
         'KM_cargado': 999, 'KM_vacio': 999, 'KM_total': 1998, 'Diesel_LTS': 999, 'Viajes_count': 1},
    ])
    agg = _agg(_ced([]), trips, corte='2026-06-04')
    df_detalle = agg.aggregate_detalle_opcedula()

    assert len(df_detalle) == 2
    mars = df_detalle[df_detalle['Operación cedula'] == 'MARS CAMIONETA'].iloc[0]
    assert mars['Equipo Motriz'] == 'L7'
    assert mars['KM Total'] == 120
    assert mars['Viajes'] == 1
    assert mars['Dias Activo'] == 1
    axion = df_detalle[df_detalle['Operación cedula'] == 'AXION LOG CAMIONETA'].iloc[0]
    assert axion['KM Total'] == 300  # 240 + 60; el comodato (1998) queda excluido
    assert axion['Viajes'] == 2
    # 2 fechas distintas (06-02, 06-03) con viaje real bajo AXION — v0.6.9,
    # insumo de Rendimiento_dia_activo en opcedula.py (no cuenta el dia del
    # comodato, que ya esta excluido de df_trips_validos).
    assert axion['Dias Activo'] == 2


def test_aggregate_detalle_opcedula_vacio_sin_columna() -> None:
    """Sin 'Operación cedula' en viajes (tests legacy) -> DataFrame vacio con schema."""
    trips = _trips([
        {'Equipo Motriz': 'C070', 'Fecha creación': '2026-06-01', 'Número de Viaje': 1,
         'ClaveCategoria': 'X'},
    ])
    agg = _agg(_ced([]), trips)
    df_detalle = agg.aggregate_detalle_opcedula()
    assert df_detalle.empty
    assert list(df_detalle.columns) == [
        'Equipo Motriz', 'Operación cedula', 'KM Cargado', 'KM Vacio',
        'KM Total', 'Diesel LTS', 'Rendimiento', 'Viajes', 'Densidad Viaje',
        'Dias Activo',
    ]

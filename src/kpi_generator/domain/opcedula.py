"""Agregador por OpCedula: 1 fila por OpCedula vigente.

Reform v0.5.0 de la hoja `Por Operacion`. Toma el DataFrame de equipos
producido por `EquipmentAggregator.aggregate()` y lo agrega por
`Operacion Cedula`.

Reglas:
- Una fila por OpCedula que tenga ≥1 motriz titular (asignacion vigente).
- Contadores de status (`Operando`, `Taller`, ...) son COUNT de motrices
  titulares con esa Estatus vigente.
- Metricas operativas (KM, Viajes, Diesel) son SUM desde df_equipos.
- `Objetivo KM` consolidado = Objetivo KM Diario de la OpCedula × motrices
  titulares × dias corrientes.
- `Tendencia KM` = Σ Tendencia KM individual (se calcula despues, en post-pass).
- `Promedio KM/dia/unidad` = KM Total / Σ Dias Asignado (sirve como insumo
  para calcular `Tendencia KM` individual en el post-pass del processor).

Tambien se calcula `_post_calcular_tendencia_equipos` que toma df_equipos +
df_opcedula y rellena `Tendencia KM` / `Tendencia Viajes` en cada equipo.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from kpi_generator.domain.period import PeriodContext

# Columnas de la hoja Por Operacion (orden final).
OPCEDULA_OUTPUT_COLS = [
    # Identidad
    'Operacion Cedula', 'Gerencia', 'Operacion', 'Circuito', 'Tipo de Unidad',
    # Conteos por status (motrices titulares al corte)
    'Motrices Titulares',
    'Operando', 'Disponible', 'Sin Operador', 'Taller',
    'Gestoria', 'Descanso', 'Rescate', 'Puesto A Punto', 'Otros Status',
    # Dias unidad
    'Dias unidad asignados', 'Dias unidad activos',
    # Operativos (suma desde equipos)
    'KM Cargado', 'KM Vacio', 'KM Total', 'Diesel LTS', 'Rendimiento',
    'Viajes', 'Densidad Viaje',
    # Objetivos al cierre del mes: corte + complemento futuro
    # (obj_diario × titulares × dias_corrientes / dias_restantes / dias_mes)
    'Objetivo KM Corte', 'Objetivo Viajes Corte',
    'Complemento KM Objetivo', 'Complemento Viajes Objetivo',
    'Objetivo KM', 'Objetivo Viajes',
    'Cumplimiento KM %', 'Cumplimiento Viajes %',
    # Eficiencia
    '% Operativo',
    # Insumo para Tendencia individual + agregado
    'Promedio KM dia unidad', 'Promedio Viajes dia unidad',
    'Tendencia KM', 'Tendencia Viajes',
]


class OpcedulaAggregator:
    """Agrega `df_equipos` (salida de EquipmentAggregator) por OpCedula vigente.

    Uso:
        op_agg = OpcedulaAggregator(df_equipos, obj_mapping, period)
        df_opcedula = op_agg.aggregate()
    """

    def __init__(self, df_equipos: pd.DataFrame,
                 obj_mapping: Optional[Dict[str, Dict[str, float]]],
                 period: PeriodContext,
                 log_callback=print):
        self.df_equipos = df_equipos
        self.obj_mapping = obj_mapping or {}
        self.period = period
        self.log = log_callback

    def aggregate(self) -> pd.DataFrame:
        """Construye el DataFrame agregado por OpCedula."""
        if self.df_equipos.empty:
            return pd.DataFrame(columns=OPCEDULA_OUTPUT_COLS)

        # Solo motrices (los arrastres no son titulares de OpCedula)
        motrices = self.df_equipos[self.df_equipos['Tipo Equipo'] == 'Motriz'].copy()

        # Excluir POR ASIGNAR del listado de OpCedulas (no es una operacion real)
        es_real = ~motrices['Operacion Cedula'].astype(str).str.startswith('POR ASIGNAR')

        registros = []
        for opcedula, grupo in motrices[es_real].groupby('Operacion Cedula'):
            registros.append(self._fila_opcedula(opcedula, grupo))

        df = pd.DataFrame(registros)
        for col in OPCEDULA_OUTPUT_COLS:
            if col not in df.columns:
                df[col] = 0
        df = df[OPCEDULA_OUTPUT_COLS]
        self.log(f'[OP] Por Operacion: {len(df)} operaciones')
        return df

    def _fila_opcedula(self, opcedula: str, grupo: pd.DataFrame) -> dict:
        """Construye una fila agregando motrices titulares de la OpCedula."""
        primera = grupo.iloc[0]
        n_titulares = len(grupo)

        # Counts por status vigente (al corte)
        status_counts = grupo['Estatus'].value_counts().to_dict()
        statuses_canon = ['Operando', 'Disponible', 'Sin Operador', 'Taller',
                          'Gestoria', 'Descanso', 'Rescate', 'Puesto A Punto']
        counts_status = {s: int(status_counts.get(s, 0)) for s in statuses_canon}
        # Cualquier Estatus distinto cae en "Otros Status"
        otros = sum(int(v) for k, v in status_counts.items()
                    if k not in statuses_canon and k != 'Sin Asignacion')
        counts_status['Otros Status'] = otros

        # Dias unidad (sumas sobre titulares)
        dias_asignados = int(grupo['Dias Asignado'].sum())
        dias_activos = int(grupo['Dias Activo'].sum())

        # Operativos (sumas desde equipos)
        km_cargado = float(grupo['KM Cargado'].sum())
        km_vacio = float(grupo['KM Vacio'].sum())
        km_total = float(grupo['KM Total'].sum())
        diesel = float(grupo['Diesel LTS'].sum())
        viajes = int(grupo['Viajes'].sum())
        rendimiento = round(km_total / diesel, 2) if diesel > 0 else 0.0
        densidad = round(km_total / viajes, 2) if viajes > 0 else 0.0

        # Objetivos consolidados al CIERRE del mes (corte + complemento futuro).
        # Asume titulares estables hasta cierre (proyeccion simple).
        #   Obj corte = obj_diario × titulares × dias_corrientes
        #   Compl    = obj_diario × titulares × dias_restantes_mes
        #   Obj total = obj_diario × titulares × dias_mes
        obj_entry = self.obj_mapping.get(opcedula, {})
        obj_km_diario = float(obj_entry.get('Objetivo KM Diario', 0) or 0)
        obj_viajes_diario = float(obj_entry.get('Objetivo Viajes Diario', 0) or 0)
        obj_km_corte = round(obj_km_diario * n_titulares * self.period.dias_corrientes, 2)
        obj_v_corte = round(obj_viajes_diario * n_titulares * self.period.dias_corrientes, 2)
        compl_km = round(obj_km_diario * n_titulares * self.period.dias_restantes, 2)
        compl_v = round(obj_viajes_diario * n_titulares * self.period.dias_restantes, 2)
        obj_km_total = round(obj_km_corte + compl_km, 2)
        obj_v_total = round(obj_v_corte + compl_v, 2)
        # Cumplimiento al cierre: tendencia (KM real + proyeccion) vs objetivo al cierre.
        # Aqui usamos km_total (KM real) y luego post_calcular_tendencia lo refina.
        cump_km = round(km_total / obj_km_total * 100, 2) if obj_km_total > 0 else 0.0
        cump_v = round(viajes / obj_v_total * 100, 2) if obj_v_total > 0 else 0.0

        # % Operativo: dias unidad activos / (titulares * dias corrientes)
        denom = max(n_titulares * self.period.dias_corrientes, 1)
        pct_operativo = round(dias_activos / denom * 100, 2)

        # Insumo para Tendencia individual: promedio KM/dia/unidad asignado
        promedio_km = round(km_total / dias_asignados, 4) if dias_asignados > 0 else 0.0
        promedio_v = round(viajes / dias_asignados, 4) if dias_asignados > 0 else 0.0

        return {
            'Operacion Cedula': opcedula,
            'Gerencia': primera['Gerencia'],
            'Operacion': primera['Operacion'],
            'Circuito': primera['Circuito'],
            'Tipo de Unidad': primera['Tipo de Unidad'],
            'Motrices Titulares': n_titulares,
            **counts_status,
            'Dias unidad asignados': dias_asignados,
            'Dias unidad activos': dias_activos,
            'KM Cargado': round(km_cargado, 2),
            'KM Vacio': round(km_vacio, 2),
            'KM Total': round(km_total, 2),
            'Diesel LTS': round(diesel, 2),
            'Rendimiento': rendimiento,
            'Viajes': viajes,
            'Densidad Viaje': densidad,
            'Objetivo KM Corte': obj_km_corte,
            'Objetivo Viajes Corte': obj_v_corte,
            'Complemento KM Objetivo': compl_km,
            'Complemento Viajes Objetivo': compl_v,
            'Objetivo KM': obj_km_total,
            'Objetivo Viajes': obj_v_total,
            'Cumplimiento KM %': cump_km,
            'Cumplimiento Viajes %': cump_v,
            '% Operativo': pct_operativo,
            'Promedio KM dia unidad': promedio_km,
            'Promedio Viajes dia unidad': promedio_v,
            'Tendencia KM': 0.0,         # se rellena en post_calcular_tendencia
            'Tendencia Viajes': 0.0,
        }


def post_calcular_tendencia(df_equipos: pd.DataFrame, df_opcedula: pd.DataFrame,
                            period: PeriodContext) -> None:
    """Rellena `Tendencia KM` / `Tendencia Viajes` en df_equipos in-place.

    Formula (Beto v0.5.0):
        Tendencia KM = KM Real + Dias restantes mes × Promedio KM dia unidad × % Operativo / 100

    El promedio se toma de la OpCedula vigente del equipo (en df_opcedula). Si la
    OpCedula vigente es POR ASIGNAR o no aparece en df_opcedula, la tendencia
    es igual al KM real.

    Despues actualiza la tendencia agregada en df_opcedula (Σ tendencias individuales).
    """
    if df_equipos.empty:
        return

    promedios_km = df_opcedula.set_index('Operacion Cedula')['Promedio KM dia unidad'].to_dict() \
        if not df_opcedula.empty else {}
    promedios_v = df_opcedula.set_index('Operacion Cedula')['Promedio Viajes dia unidad'].to_dict() \
        if not df_opcedula.empty else {}

    restantes = period.dias_restantes

    for idx, row in df_equipos.iterrows():
        km_real = float(row['KM Total'])
        viajes_real = float(row['Viajes'])
        pct_op = float(row['% Operativo']) / 100.0
        opcedula = row['Operacion Cedula']
        prom_km = float(promedios_km.get(opcedula, 0) or 0)
        prom_v = float(promedios_v.get(opcedula, 0) or 0)
        df_equipos.at[idx, 'Tendencia KM'] = round(
            km_real + restantes * prom_km * pct_op, 2
        )
        df_equipos.at[idx, 'Tendencia Viajes'] = round(
            viajes_real + restantes * prom_v * pct_op, 2
        )

    # Actualiza Tendencia agregada en df_opcedula
    if df_opcedula.empty:
        return
    motrices = df_equipos[df_equipos['Tipo Equipo'] == 'Motriz']
    tend_por_opcedula = motrices.groupby('Operacion Cedula').agg(
        Tendencia_KM=('Tendencia KM', 'sum'),
        Tendencia_Viajes=('Tendencia Viajes', 'sum'),
    )
    for opcedula, row in tend_por_opcedula.iterrows():
        mask = df_opcedula['Operacion Cedula'] == opcedula
        df_opcedula.loc[mask, 'Tendencia KM'] = round(row['Tendencia_KM'], 2)
        df_opcedula.loc[mask, 'Tendencia Viajes'] = round(row['Tendencia_Viajes'], 2)

"""Agregador por OpCedula: 1 fila por OpCedula vigente + 1 fila 'Pendiente'.

Reform v0.5.0 de la hoja `Por Operacion`, extendida en v0.6.0 con atribucion
dia-por-dia. Toma `df_equipos` (salida de `EquipmentAggregator.aggregate()`)
para identidad/status/dias por asignacion vigente, y opcionalmente
`df_detalle_opcedula` (salida de `aggregate_detalle_opcedula()`) para KM/
Diesel/Viajes/Motrices Utilizadas atribuidos a la OpCedula que realmente
tenia la unidad cada dia (no la vigente al cierre).

Reglas:
- Una fila por OpCedula vigente de ≥1 motriz, + 1 fila 'Pendiente' que
  consolida motrices sin vigente real (POR ASIGNAR) y cualquier KM/Viajes
  historico atribuido a una OpCedula huerfana (no vigente de nadie al corte).
- Contadores de status (`Operando`, `Taller`, ...) y `Motrices Titulares` son
  por asignacion VIGENTE (sin cambios por el split dia-por-dia).
- `Motrices Utilizadas` = unidades distintas con ≥1 viaje real (no comodato)
  atribuido a esa OpCedula segun el detalle historico.
- KM/Diesel/Viajes/Rendimiento/Densidad: SUM desde `df_detalle_opcedula` si
  se provee (dia-por-dia); si no, fallback a SUM desde `df_equipos` (legacy,
  usado por tests sin distincion historica).
- `Objetivo KM` consolidado = Objetivo KM Diario de la OpCedula × motrices
  titulares × dias corrientes.
- `Tendencia KM` = Σ Tendencia KM individual (se calcula despues, en post-pass).
- `Promedio KM/dia/unidad` = KM Total / Σ Dias Activo de ESTA OpCedula (v0.6.9;
  antes dividia entre Σ Dias Asignado, diluyendo el promedio con unidades
  totalmente inactivas — ver `post_calcular_tendencia`). Sirve como insumo
  para calcular `Tendencia KM`/`Potencial KM` individual en el post-pass.

Tambien se calcula `post_calcular_tendencia` que toma df_equipos + df_opcedula
y rellena `Tendencia KM` / `Tendencia Viajes` en cada equipo y su agregado.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from kpi_generator.domain.period import PeriodContext

# Columnas de la hoja Por Operacion (orden final).
OPCEDULA_OUTPUT_COLS = [
    # Identidad
    'Operacion Cedula', 'Gerencia', 'Operacion', 'Circuito', 'Tipo de Unidad',
    # Conteos por status (motrices titulares al corte) + uso real dia-por-dia
    'Motrices Titulares', 'Motrices Utilizadas',
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
    # v0.6.9: proyeccion aislada (Tendencia - Real), al final para no romper
    # posiciones existentes en Looker Studio.
    'Potencial KM', 'Potencial Viajes',
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
                 df_detalle_opcedula: Optional[pd.DataFrame] = None,
                 log_callback=print):
        self.df_equipos = df_equipos
        self.obj_mapping = obj_mapping or {}
        self.period = period
        # Detalle motriz x OpCedula historica (dia-por-dia), salida de
        # `EquipmentAggregator.aggregate_detalle_opcedula()`. Si es None, KM/
        # Diesel/Viajes/Motrices Utilizadas se calculan desde `df_equipos`
        # (asignacion vigente), igual que antes de v0.6.0 — usado por tests
        # que no necesitan la distincion dia-por-dia.
        self.df_detalle_opcedula = df_detalle_opcedula
        self.log = log_callback

    def aggregate(self) -> pd.DataFrame:
        """Construye el DataFrame agregado por OpCedula."""
        if self.df_equipos.empty:
            return pd.DataFrame(columns=OPCEDULA_OUTPUT_COLS)

        # Solo motrices (los arrastres no son titulares de OpCedula)
        motrices = self.df_equipos[self.df_equipos['Tipo Equipo'] == 'Motriz'].copy()

        # Excluir POR ASIGNAR del listado de OpCedulas (no es una operacion real)
        es_real = ~motrices['Operacion Cedula'].astype(str).str.startswith('POR ASIGNAR')
        claves_reales = set(motrices.loc[es_real, 'Operacion Cedula'].unique())

        # Detalle historico saneado: toda OpCedula del dia que no sea vigente
        # de NINGUN equipo al corte (huerfana, ej. catalogo retirado, o
        # 'POR ASIGNAR *') se reetiqueta a 'Pendiente'.
        detalle = self.df_detalle_opcedula
        if detalle is not None and not detalle.empty:
            detalle = detalle.copy()
            detalle['Operación cedula'] = detalle['Operación cedula'].where(
                detalle['Operación cedula'].isin(claves_reales), 'Pendiente'
            )

        registros = []
        for opcedula, grupo in motrices[es_real].groupby('Operacion Cedula'):
            grupo_dia = None
            if detalle is not None:
                grupo_dia = detalle[detalle['Operación cedula'] == opcedula]
            registros.append(self._fila_opcedula(opcedula, grupo, grupo_dia))

        # Fila consolidada 'Pendiente': motrices con vigente fantasma (POR
        # ASIGNAR) + cualquier KM/Viajes historico atribuido a una OpCedula
        # huerfana (aunque la vigente del equipo sea otra, ej. unidad
        # reasignada o catalogo retirado a mitad de periodo).
        grupo_pendiente_vigente = motrices[~es_real]
        grupo_dia_pendiente = None
        if detalle is not None:
            grupo_dia_pendiente = detalle[detalle['Operación cedula'] == 'Pendiente']
        hay_vigente_pendiente = not grupo_pendiente_vigente.empty
        hay_historico_pendiente = grupo_dia_pendiente is not None and not grupo_dia_pendiente.empty
        if hay_vigente_pendiente or hay_historico_pendiente:
            identidad_pendiente = {
                'Gerencia': 'Pendiente', 'Operacion': 'POR ASIGNAR',
                'Circuito': 'POR ASIGNAR', 'Tipo de Unidad': 'VARIOS',
            }
            registros.append(self._fila_opcedula(
                'Pendiente', grupo_pendiente_vigente, grupo_dia_pendiente,
                identidad=identidad_pendiente,
            ))

        df = pd.DataFrame(registros)
        for col in OPCEDULA_OUTPUT_COLS:
            if col not in df.columns:
                df[col] = 0
        df = df[OPCEDULA_OUTPUT_COLS]
        self.log(f'[OP] Por Operacion: {len(df)} operaciones')
        return df

    def _fila_opcedula(self, opcedula: str, grupo: pd.DataFrame,
                       grupo_dia: Optional[pd.DataFrame] = None,
                       identidad: Optional[Dict[str, str]] = None) -> dict:
        """Construye una fila agregando motrices titulares de la OpCedula.

        `grupo` (subset de df_equipos por asignacion vigente) alimenta
        identidad, conteos de status y dias unidad. `grupo_dia` (subset del
        detalle historico dia-por-dia, ya saneado) alimenta KM/Diesel/Viajes/
        Motrices Utilizadas. Si `grupo_dia` es None (modo legacy sin detalle
        historico), las metricas operativas caen de vuelta a `grupo`.
        """
        n_titulares = len(grupo)

        if identidad is not None:
            id_gerencia, id_operacion = identidad['Gerencia'], identidad['Operacion']
            id_circuito, id_tipo_unidad = identidad['Circuito'], identidad['Tipo de Unidad']
        elif n_titulares > 0:
            primera = grupo.iloc[0]
            id_gerencia, id_operacion = primera['Gerencia'], primera['Operacion']
            id_circuito, id_tipo_unidad = primera['Circuito'], primera['Tipo de Unidad']
        else:
            id_gerencia = id_operacion = id_circuito = id_tipo_unidad = ''

        # Counts por status vigente (al corte)
        status_counts = grupo['Estatus'].value_counts().to_dict() if n_titulares > 0 else {}
        statuses_canon = ['Operando', 'Disponible', 'Sin Operador', 'Taller',
                          'Gestoria', 'Descanso', 'Rescate', 'Puesto A Punto']
        counts_status = {s: int(status_counts.get(s, 0)) for s in statuses_canon}
        # Cualquier Estatus distinto cae en "Otros Status"
        otros = sum(int(v) for k, v in status_counts.items()
                    if k not in statuses_canon and k != 'Sin Asignacion')
        counts_status['Otros Status'] = otros

        # Dias unidad (sumas sobre titulares, siempre por asignacion vigente)
        dias_asignados = int(grupo['Dias Asignado'].sum()) if n_titulares > 0 else 0
        dias_activos = int(grupo['Dias Activo'].sum()) if n_titulares > 0 else 0

        # Operativos: dia-por-dia si hay detalle, sino fallback a vigente (legacy)
        fuente_operativa = grupo_dia if grupo_dia is not None else grupo
        if fuente_operativa is not None and not fuente_operativa.empty:
            km_cargado = float(fuente_operativa['KM Cargado'].sum())
            km_vacio = float(fuente_operativa['KM Vacio'].sum())
            km_total = float(fuente_operativa['KM Total'].sum())
            diesel = float(fuente_operativa['Diesel LTS'].sum())
            viajes = int(fuente_operativa['Viajes'].sum())
            motrices_utilizadas = int(fuente_operativa['Equipo Motriz'].nunique())
            # Dias con actividad real ESPECIFICA de esta OpCedula (no el total
            # del mes del equipo, que puede incluir otra OpCedula si fue
            # reasignado) — insumo del Promedio KM/Viajes dia unidad de abajo,
            # v0.6.9.
            dias_activos_opcedula = (
                int(fuente_operativa['Dias Activo'].sum())
                if 'Dias Activo' in fuente_operativa.columns else 0
            )
        else:
            km_cargado = km_vacio = km_total = diesel = 0.0
            viajes = 0
            motrices_utilizadas = 0
            dias_activos_opcedula = 0
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

        # Insumo para Tendencia individual: rendimiento de un dia REALMENTE
        # trabajado en esta OpCedula (v0.6.9). Antes dividia entre TODOS los
        # dias asignados por cedula (`dias_asignados`, linea arriba) —
        # incluidos los de unidades totalmente inactivas ese mes— diluyendo
        # el promedio y descontando la capacidad operativa del grupo DOS
        # veces (una aqui, otra al multiplicar por el %Operativo propio de
        # cada unidad en `post_calcular_tendencia`). Ahora divide solo entre
        # dias con actividad real atribuida a esta OpCedula especificamente.
        promedio_km = round(km_total / dias_activos_opcedula, 4) if dias_activos_opcedula > 0 else 0.0
        promedio_v = round(viajes / dias_activos_opcedula, 4) if dias_activos_opcedula > 0 else 0.0

        return {
            'Operacion Cedula': opcedula,
            'Gerencia': id_gerencia,
            'Operacion': id_operacion,
            'Circuito': id_circuito,
            'Tipo de Unidad': id_tipo_unidad,
            'Motrices Titulares': n_titulares,
            'Motrices Utilizadas': motrices_utilizadas,
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
            'Potencial KM': 0.0,         # idem
            'Potencial Viajes': 0.0,
        }


def post_calcular_tendencia(df_equipos: pd.DataFrame, df_opcedula: pd.DataFrame,
                            period: PeriodContext,
                            obj_mapping: Optional[Dict[str, Dict[str, float]]] = None) -> None:
    """Rellena `Tendencia KM`/`Tendencia Viajes`/`Potencial KM`/`Potencial Viajes`
    en df_equipos in-place, y actualiza los agregados en df_opcedula.

    Formula (v0.6.9 — corrige doble descuento de capacidad operativa que
    tenia la formula v0.5.0 original; ver docs/v0.5.0-design.md):

        Potencial_obs  = Dias restantes × Rendimiento_dia_activo(OpCedula) × %Operativo(equipo)/100
        Potencial_piso = Dias restantes × Objetivo_diario(OpCedula)        × %Operativo(equipo)/100
        peso_evidencia = min(Dias_corrientes / (0.16 × Dias_mes), 1.0)
        Potencial      = peso_evidencia × Potencial_obs + (1 − peso_evidencia) × Potencial_piso
        Tendencia      = Real + Potencial

    `Rendimiento_dia_activo` = 'Promedio KM/Viajes dia unidad' de la OpCedula
    vigente del equipo (df_opcedula — ya viene libre de dilucion de grupo,
    ver `_fila_opcedula`: divide solo entre dias con actividad real de esa
    OpCedula, no entre todos los dias asignados por cedula).

    `%Operativo(equipo)` es una caracteristica propia de la unidad — ya
    calculada en `EquipmentAggregator` sobre TODO su historial de viajes del
    mes, agnostica de bajo que OpCedula estuvo cada dia — se reutiliza tal
    cual, no se recalcula aqui. Esto evita que una unidad recien reasignada
    "resetee" su confiabilidad probada por 1 solo dia bueno/malo en la
    asignacion nueva (Beto, 2026-07-13).

    `peso_evidencia` (Paso 4) evita proyecciones inestables en cortes muy
    tempranos del mes: mezcla el potencial "observado" (Paso 3) con un piso
    basado en el Objetivo diario de la OpCedula, y el peso del observado
    crece conforme avanza el mes hasta ponderar 100% a partir del 16% de
    `dias_mes` transcurridos.

    Si la OpCedula vigente es POR ASIGNAR o no aparece en df_opcedula (sin
    `Rendimiento_dia_activo` ni Objetivo coherentes), ambos terminos de
    Potencial dan 0 de forma natural y la tendencia queda igual al real —
    mismo comportamiento que la version anterior para este caso borde.

    Despues actualiza los agregados en df_opcedula (Σ por OpCedula).
    """
    if df_equipos.empty:
        return

    obj_mapping = obj_mapping or {}
    promedios_km = df_opcedula.set_index('Operacion Cedula')['Promedio KM dia unidad'].to_dict() \
        if not df_opcedula.empty else {}
    promedios_v = df_opcedula.set_index('Operacion Cedula')['Promedio Viajes dia unidad'].to_dict() \
        if not df_opcedula.empty else {}

    restantes = period.dias_restantes
    umbral_dias = max(0.16 * period.dias_mes, 1e-9)
    peso_evidencia = min(period.dias_corrientes / umbral_dias, 1.0)

    for idx, row in df_equipos.iterrows():
        km_real = float(row['KM Total'])
        viajes_real = float(row['Viajes'])
        pct_op = float(row['% Operativo']) / 100.0
        opcedula = row['Operacion Cedula']

        rendimiento_km = float(promedios_km.get(opcedula, 0) or 0)
        rendimiento_v = float(promedios_v.get(opcedula, 0) or 0)
        obj_entry = obj_mapping.get(opcedula, {})
        obj_km_diario = float(obj_entry.get('Objetivo KM Diario', 0) or 0)
        obj_v_diario = float(obj_entry.get('Objetivo Viajes Diario', 0) or 0)

        potencial_km = (
            peso_evidencia * (restantes * rendimiento_km * pct_op)
            + (1 - peso_evidencia) * (restantes * obj_km_diario * pct_op)
        )
        potencial_v = (
            peso_evidencia * (restantes * rendimiento_v * pct_op)
            + (1 - peso_evidencia) * (restantes * obj_v_diario * pct_op)
        )

        df_equipos.at[idx, 'Potencial KM'] = round(potencial_km, 2)
        df_equipos.at[idx, 'Potencial Viajes'] = round(potencial_v, 2)
        df_equipos.at[idx, 'Tendencia KM'] = round(km_real + potencial_km, 2)
        df_equipos.at[idx, 'Tendencia Viajes'] = round(viajes_real + potencial_v, 2)

    # Actualiza agregados en df_opcedula. Equipos cuya OpCedula vigente no es
    # clave de ninguna fila de df_opcedula (ej. 'POR ASIGNAR FULL') se
    # reagrupan bajo 'Pendiente' si esa fila existe, para que su Tendencia KM
    # (= KM real, sin proyeccion, ya calculada arriba) no se pierda
    # silenciosamente.
    if df_opcedula.empty:
        return
    motrices = df_equipos[df_equipos['Tipo Equipo'] == 'Motriz'].copy()
    claves_reales = set(df_opcedula['Operacion Cedula'])
    motrices['_bucket'] = motrices['Operacion Cedula'].where(
        motrices['Operacion Cedula'].isin(claves_reales), 'Pendiente'
    )
    agregados = motrices.groupby('_bucket').agg(
        Tendencia_KM=('Tendencia KM', 'sum'),
        Tendencia_Viajes=('Tendencia Viajes', 'sum'),
        Potencial_KM=('Potencial KM', 'sum'),
        Potencial_Viajes=('Potencial Viajes', 'sum'),
    )
    for opcedula, row in agregados.iterrows():
        mask = df_opcedula['Operacion Cedula'] == opcedula
        df_opcedula.loc[mask, 'Tendencia KM'] = round(row['Tendencia_KM'], 2)
        df_opcedula.loc[mask, 'Tendencia Viajes'] = round(row['Tendencia_Viajes'], 2)
        df_opcedula.loc[mask, 'Potencial KM'] = round(row['Potencial_KM'], 2)
        df_opcedula.loc[mask, 'Potencial Viajes'] = round(row['Potencial_Viajes'], 2)

"""Agregador por OpCedula: 1 fila por OpCedula vigente, 1 por cada 'POR ASIGNAR
<tipo>' y 1 fila 'Pendiente' para OpCedulas retiradas.

Reform v0.5.0 de la hoja `Por Operacion`, extendida en v0.6.0 (KM/Diesel/
Viajes dia por dia) y v0.7.0 (objetivo y tendencia dia por dia, POR ASIGNAR
por tipo, remolques/dollies titulares).

Principio rector (Beto, 2026-09-29): TODOS LOS CALCULOS SE ASIGNAN
DIARIAMENTE. Cada unidad-dia aporta a la OpCedula que tenia ESE dia; solo las
fotos del corte (titulares y su status) y la proyeccion a futuro usan la
asignacion vigente.

Toma `df_equipos` (salida de `EquipmentAggregator.aggregate()`) para la foto
del corte, `df_detalle_opcedula` (`aggregate_detalle_opcedula()`) para KM/
Diesel/Viajes/Motrices Utilizadas dia por dia y `df_objetivo_opcedula`
(`aggregate_objetivo_opcedula()`) para el objetivo dia por dia.

Reglas:
- Filas: una por OpCedula vigente de ≥1 motriz; una por cada 'POR ASIGNAR
  <tipo>' (Gerencia 'Pendiente') que aparezca en la foto o en la asignacion
  diaria; y 'Pendiente' solo para lo atribuido dia por dia a una OpCedula
  retirada (no vigente de ningun motriz al corte).
- `Motrices Titulares` = la foto (ultima asignacion de cada motriz) y sus
  contadores de status (`Operando`, `Taller`, ...). `Remolques/Dollies
  Titulares` = foto de los arrastres (heredan la vigente de su motriz
  dominante).
- `Motrices Utilizadas` = unidades distintas con ≥1 viaje real (no comodato)
  atribuido dia por dia a esa OpCedula, aunque ya no sean titulares.
- KM/Diesel/Viajes/Rendimiento/Densidad: SUM dia por dia.
- `Objetivo KM Corte` = SUM dia por dia (cada dia asignado aporta el objetivo
  diario de la OpCedula de ese dia). `Complemento` = objetivo diario ×
  titulares × dias restantes (proyeccion). `Objetivo KM` = Corte +
  Complemento; Σ Por Operacion = Σ Por Equipo.
- `Tendencia KM` = KM Total (dia por dia) + Σ Potencial KM de los titulares
  (post-pass `post_calcular_tendencia`).
- `Promedio KM/dia/unidad` = KM Total / Σ Dias Activo de ESTA OpCedula (v0.6.9).
  Insumo del Potencial individual en el post-pass.
- `Dias unidad asignados/activos` y `% Operativo` siguen por titulares al
  corte (su version diaria esta pendiente de definir).
- Sin detalles (tests legacy): KM/Viajes y objetivo caen a los titulares.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from kpi_generator.domain.equipment import STATUS_CANONICOS, categoria_status, tipo_opcedula
from kpi_generator.domain.period import PeriodContext

POR_ASIGNAR = 'POR ASIGNAR'
PENDIENTE = 'Pendiente'

# Identidad de la fila 'Pendiente' (OpCedulas retiradas del catalogo).
_IDENTIDAD_PENDIENTE = {
    'Gerencia': PENDIENTE, 'Operacion': POR_ASIGNAR,
    'Circuito': POR_ASIGNAR, 'Tipo de Unidad': 'VARIOS',
}

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
    # Operativos (suma dia por dia)
    'KM Cargado', 'KM Vacio', 'KM Total', 'Diesel LTS', 'Rendimiento',
    'Viajes', 'Densidad Viaje',
    # Objetivos al cierre del mes: corte dia por dia + complemento futuro
    # (obj_diario × titulares × dias_restantes)
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
    # v0.7.0: foto de arrastres, al final por la misma razon.
    'Remolques Titulares', 'Dollies Titulares',
]


def _identidad_por_asignar(opcedula: str) -> Dict[str, str]:
    """Identidad de una fila 'POR ASIGNAR <tipo>'."""
    return {
        'Gerencia': PENDIENTE, 'Operacion': POR_ASIGNAR,
        'Circuito': POR_ASIGNAR, 'Tipo de Unidad': opcedula[len(POR_ASIGNAR):].strip(),
    }


def _sanear_opcedula_dia(df: Optional[pd.DataFrame], claves_reales: set) -> Optional[pd.DataFrame]:
    """Reetiqueta a 'Pendiente' la OpCedula del dia retirada del catalogo.

    Retirada = no es vigente de ningun motriz al corte ni 'POR ASIGNAR *'
    (ej. catalogo retirado a mitad de periodo, 'Sin Asignar').
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    opcedula = df['Operación cedula'].astype(str)
    conservar = opcedula.isin(claves_reales) | opcedula.str.startswith(POR_ASIGNAR)
    df['Operación cedula'] = opcedula.where(conservar, PENDIENTE)
    return df


def _grupos_por_clave(df: Optional[pd.DataFrame], columna: str):
    """({clave: sub-DataFrame}, sub-DataFrame vacio con el mismo schema).

    (None, None) si `df` es None: el caller cae al modo legacy.
    """
    if df is None:
        return None, None
    if df.empty:
        return {}, df
    return dict(tuple(df.groupby(columna))), df.iloc[0:0]


class OpcedulaAggregator:
    """Agrega `df_equipos` (salida de EquipmentAggregator) por OpCedula.

    Uso:
        op_agg = OpcedulaAggregator(df_equipos, obj_mapping, period,
                                    df_detalle_opcedula, df_objetivo_opcedula)
        df_opcedula = op_agg.aggregate()
    """

    def __init__(self, df_equipos: pd.DataFrame,
                 obj_mapping: Optional[Dict[str, Dict[str, float]]],
                 period: PeriodContext,
                 df_detalle_opcedula: Optional[pd.DataFrame] = None,
                 log_callback=print,
                 df_objetivo_opcedula: Optional[pd.DataFrame] = None):
        self.df_equipos = df_equipos
        self.obj_mapping = obj_mapping or {}
        self.period = period
        # Detalle motriz x OpCedula historica (dia-por-dia), salida de
        # `EquipmentAggregator.aggregate_detalle_opcedula()`. Si es None, KM/
        # Diesel/Viajes/Motrices Utilizadas se calculan desde `df_equipos`
        # (asignacion vigente), igual que antes de v0.6.0 — usado por tests
        # que no necesitan la distincion dia-por-dia.
        self.df_detalle_opcedula = df_detalle_opcedula
        # Objetivo de corte motriz x OpCedula del dia (v0.7.0), salida de
        # `EquipmentAggregator.aggregate_objetivo_opcedula()`. Si es None, el
        # objetivo de corte cae a objetivo diario × titulares × dias corrientes
        # (legacy, titulares estables todo el periodo).
        self.df_objetivo_opcedula = df_objetivo_opcedula
        self.log = log_callback

    def aggregate(self) -> pd.DataFrame:
        """Construye el DataFrame agregado por OpCedula."""
        if self.df_equipos.empty:
            return pd.DataFrame(columns=OPCEDULA_OUTPUT_COLS)

        motrices = self.df_equipos[self.df_equipos['Tipo Equipo'] == 'Motriz']
        arrastres = self.df_equipos[self.df_equipos['Tipo Equipo'] != 'Motriz']
        vigente_mot = motrices['Operacion Cedula'].astype(str)
        vigente_arr = arrastres['Operacion Cedula'].astype(str)

        # OpCedulas reales: vigentes de al menos un motriz al corte.
        claves_reales = set(vigente_mot[~vigente_mot.str.startswith(POR_ASIGNAR)])

        # Asignacion diaria saneada (retiradas -> 'Pendiente').
        detalle = _sanear_opcedula_dia(self.df_detalle_opcedula, claves_reales)
        objetivo = _sanear_opcedula_dia(self.df_objetivo_opcedula, claves_reales)

        # Una fila por cada 'POR ASIGNAR <tipo>' presente en la foto (motrices
        # o arrastres) o en la asignacion diaria (viajes/objetivo).
        claves_por_asignar = set(vigente_mot[vigente_mot.str.startswith(POR_ASIGNAR)])
        claves_por_asignar |= set(vigente_arr[vigente_arr.str.startswith(POR_ASIGNAR)])
        for df_dia in (detalle, objetivo):
            if df_dia is not None and not df_dia.empty:
                dia = df_dia['Operación cedula'].astype(str)
                claves_por_asignar |= set(dia[dia.str.startswith(POR_ASIGNAR)])

        # Arrastres cuya vigente no es clave de ninguna fila (motriz dominante
        # fuera del universo de motrices) se cuentan en 'Pendiente'.
        claves_filas = claves_reales | claves_por_asignar
        bucket_arr = vigente_arr.where(vigente_arr.isin(claves_filas), PENDIENTE)

        grupos_mot = dict(tuple(motrices.groupby(vigente_mot)))
        grupos_arr = dict(tuple(arrastres.groupby(bucket_arr)))
        grupos_dia, vacio_dia = _grupos_por_clave(detalle, 'Operación cedula')
        grupos_obj, vacio_obj = _grupos_por_clave(objetivo, 'Operación cedula')

        def fila(opcedula: str, identidad: Optional[Dict[str, str]] = None) -> dict:
            return self._fila_opcedula(
                opcedula, grupos_mot.get(opcedula, motrices.iloc[0:0]),
                grupo_dia=grupos_dia.get(opcedula, vacio_dia) if grupos_dia is not None else None,
                identidad=identidad,
                grupo_obj=grupos_obj.get(opcedula, vacio_obj) if grupos_obj is not None else None,
                grupo_arrastres=grupos_arr.get(opcedula, arrastres.iloc[0:0]),
            )

        registros = [fila(opcedula) for opcedula in sorted(claves_reales)]
        registros += [fila(opcedula, _identidad_por_asignar(opcedula))
                      for opcedula in sorted(claves_por_asignar)]

        # 'Pendiente': solo si algo se atribuyo dia por dia a una OpCedula
        # retirada (o quedo un arrastre sin fila).
        hay_pendiente = PENDIENTE in grupos_arr or any(
            grupos is not None and PENDIENTE in grupos for grupos in (grupos_dia, grupos_obj)
        )
        if hay_pendiente:
            registros.append(fila(PENDIENTE, _IDENTIDAD_PENDIENTE))

        df = pd.DataFrame(registros)
        for col in OPCEDULA_OUTPUT_COLS:
            if col not in df.columns:
                df[col] = 0
        df = df[OPCEDULA_OUTPUT_COLS]
        self.log(f'[OP] Por Operacion: {len(df)} operaciones '
                 f'({len(claves_reales)} vigentes, {len(claves_por_asignar)} POR ASIGNAR'
                 f'{", 1 Pendiente" if hay_pendiente else ""})')
        return df

    def _fila_opcedula(self, opcedula: str, grupo: pd.DataFrame,
                       grupo_dia: Optional[pd.DataFrame] = None,
                       identidad: Optional[Dict[str, str]] = None,
                       grupo_obj: Optional[pd.DataFrame] = None,
                       grupo_arrastres: Optional[pd.DataFrame] = None) -> dict:
        """Construye una fila de la OpCedula.

        `grupo` (motrices titulares: su asignacion vigente es esta OpCedula)
        alimenta identidad, conteos de status y dias unidad. `grupo_dia` y
        `grupo_obj` (detalle dia por dia, ya saneado) alimentan KM/Diesel/
        Viajes/Motrices Utilizadas y el objetivo de corte. Si son None (modo
        legacy sin detalle), caen de vuelta a los titulares.
        `grupo_arrastres` (arrastres con esta vigente) alimenta Remolques/
        Dollies Titulares.
        """
        n_titulares = len(grupo)

        if identidad is not None:
            id_gerencia, id_operacion = identidad['Gerencia'], identidad['Operacion']
            id_circuito, id_tipo_unidad = identidad['Circuito'], identidad['Tipo de Unidad']
        elif n_titulares > 0:
            primera = grupo.iloc[0]
            id_gerencia, id_operacion = primera['Gerencia'], primera['Operacion']
            id_circuito, id_tipo_unidad = primera['Circuito'], primera['Tipo de Unidad']
            # Un TORTHON RF sustituto (FEDEX/MERCADO LIBRE/DHL) no define el tipo
            # de la operación: la fila MERCADO LIBRE TORTHON es TORTHON (v0.7.1).
            if tipo_opcedula(id_operacion, id_tipo_unidad) == 'TORTHON':
                id_tipo_unidad = 'TORTHON'
        else:
            id_gerencia = id_operacion = id_circuito = id_tipo_unidad = ''

        # Counts por status vigente (al corte). `categoria_status` homologa
        # acentos y mayusculas igual que Por Equipo (ej. 'Puesto a Punto' ->
        # 'Puesto A Punto'; antes caia en 'Otros Status').
        categorias = (grupo['Estatus'].map(categoria_status).value_counts().to_dict()
                      if n_titulares > 0 else {})
        counts_status = {s: int(categorias.get(s, 0)) for s in STATUS_CANONICOS}
        counts_status['Otros Status'] = int(categorias.get('Otros Status', 0))

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

        # Objetivos al CIERRE del mes = corte + complemento futuro.
        #   Obj corte = Σ objetivo diario de cada unidad-dia asignada a esta
        #               OpCedula ESE dia (v0.7.0; cuadra con Por Equipo)
        #   Compl     = obj_diario × titulares × dias_restantes_mes
        #               (proyeccion: titulares estables hasta el cierre)
        obj_entry = self.obj_mapping.get(opcedula, {})
        obj_km_diario = float(obj_entry.get('Objetivo KM Diario', 0) or 0)
        obj_viajes_diario = float(obj_entry.get('Objetivo Viajes Diario', 0) or 0)
        if grupo_obj is not None:
            obj_km_corte = float(grupo_obj['Objetivo KM Corte'].sum()) if not grupo_obj.empty else 0.0
            obj_v_corte = float(grupo_obj['Objetivo Viajes Corte'].sum()) if not grupo_obj.empty else 0.0
        else:
            # Legacy (sin detalle de objetivo): titulares estables todo el periodo.
            obj_km_corte = obj_km_diario * n_titulares * self.period.dias_corrientes
            obj_v_corte = obj_viajes_diario * n_titulares * self.period.dias_corrientes
        obj_km_corte = round(obj_km_corte, 2)
        obj_v_corte = round(obj_v_corte, 2)
        compl_km = round(obj_km_diario * n_titulares * self.period.dias_restantes, 2)
        compl_v = round(obj_viajes_diario * n_titulares * self.period.dias_restantes, 2)
        obj_km_total = round(obj_km_corte + compl_km, 2)
        obj_v_total = round(obj_v_corte + compl_v, 2)
        # Cumplimiento al cierre: KM real (dia por dia) vs objetivo al cierre.
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

        # Foto de arrastres (heredan la vigente de su motriz dominante).
        tipos_arrastre = (grupo_arrastres['Tipo Equipo'] if grupo_arrastres is not None
                          else pd.Series(dtype=object))

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
            'Remolques Titulares': int((tipos_arrastre == 'Remolque').sum()),
            'Dollies Titulares': int((tipos_arrastre == 'Dolly').sum()),
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

    Si la OpCedula vigente es POR ASIGNAR, el equipo no proyecta (sin
    operacion que sostener): Potencial = 0 y Tendencia = Real. Desde v0.7.0
    su fila 'POR ASIGNAR <tipo>' si tiene promedio, asi que la regla es
    explicita. Una OpCedula sin datos ni objetivo tambien da Potencial 0.

    Agregado por OpCedula (v0.7.0, dia por dia): Tendencia = KM Total de la
    fila (atribuido dia por dia) + Σ Potencial de sus titulares.
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

        if str(opcedula).startswith(POR_ASIGNAR):
            potencial_km = potencial_v = 0.0
        else:
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

    # Agregados en df_opcedula: el real es el KM/Viajes de la fila (dia por
    # dia) y el potencial es el de sus titulares. Equipos cuya vigente no es
    # clave de ninguna fila se reagrupan bajo 'Pendiente' (si existe) para
    # que su potencial no se pierda silenciosamente.
    if df_opcedula.empty:
        return
    motrices = df_equipos[df_equipos['Tipo Equipo'] == 'Motriz']
    vigente = motrices['Operacion Cedula']
    bucket = vigente.where(vigente.isin(set(df_opcedula['Operacion Cedula'])), PENDIENTE)
    potencial = motrices.groupby(bucket)[['Potencial KM', 'Potencial Viajes']].sum()
    pot_km = df_opcedula['Operacion Cedula'].map(potencial['Potencial KM']).fillna(0.0)
    pot_v = df_opcedula['Operacion Cedula'].map(potencial['Potencial Viajes']).fillna(0.0)
    df_opcedula['Potencial KM'] = pot_km.round(2)
    df_opcedula['Potencial Viajes'] = pot_v.round(2)
    df_opcedula['Tendencia KM'] = (df_opcedula['KM Total'] + pot_km).round(2)
    df_opcedula['Tendencia Viajes'] = (df_opcedula['Viajes'] + pot_v).round(2)

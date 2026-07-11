"""Agregador por equipo: motrices + arrastres -> 1 fila por equipo unico.

Nucleo del nuevo schema v0.5.0 de la hoja `Por Equipo`. Toma:
- df_cedulas:    snapshot diario de asignaciones (BD o Excel, mismo schema)
- df_trips:      viajes con comodatos integrados
- obj_mapping:   {Operacion Cedula: {Objetivo KM Diario, Objetivo Viajes Diario}}
- period:        PeriodContext (mes en analisis + corte)

Produce un DataFrame con 1 fila por equipo unico (motriz o arrastre) y todas
las metricas v0.5.0. La logica de calculo esta en `EquipmentAggregator`; el
contrato del DataFrame de salida esta en `EQUIPO_OUTPUT_COLS`.

Reglas clave (ver `docs/v0.5.0-design.md`):
- Universo = union de Unidades en cedula + equipos en viajes no-comodato.
- Tipo Equipo se infiere de `Tipo de Unidad` de la cedula (motriz/remolque/dolly).
- Asignacion vigente motriz = ultima cedula del periodo (o POR ASIGNAR).
- Asignacion vigente arrastre = motriz dominante (mas viajes compartidos).
- Status BD canonicos -> 8 sub-status; resto -> `Dias Otros Status`.
- Sin Asignacion BD -> `Dias Sin Asignacion`.
- Arrastres: status se reconstruye desde viajes (Operando=viajo, Disponible=no).
- Objetivo Total = Sigma Objetivo KM Diario por dia asignado (sin importar status).
- Dias Activo = dias con >=1 viaje valido (no comodato), transversal.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from kpi_generator.domain.period import PeriodContext


def normalize_text(value: str) -> str:
    """Quita acentos y convierte Ñ/ñ -> N/n, preservando mayúsculas/minúsculas.

    NFKD descompone los caracteres acentuados en caracter base + diacritico
    combinante (incluida la Ñ, que se descompone en N + tilde); al descartar
    los diacriticos queda solo el caracter base.

    Valores no-string (NaN, None) se devuelven sin modificar: `.astype(str)`
    sobre una columna numerica con NaN puede preservar el NaN como float en
    vez de convertirlo a la cadena "nan", y `.map()` lo pasaria tal cual.
    """
    if not isinstance(value, str) or not value:
        return value
    return ''.join(
        c for c in unicodedata.normalize('NFKD', value)
        if not unicodedata.combining(c)
    )


# ---------- Mapeo de ClaveCategoria (viajes) -> Tipo de Unidad (cedula) ----------

CLAVE_CATEGORIA_A_TIPO_UNIDAD = {
    'CAMIONETA': 'CAMIONETA',
    'SENCILLO': 'TRACTOCAMION SENCILLO',
    'FULL': 'TRACTOCAMION FULL',
    'TORTHON': 'TORTHON',
    'THORTON': 'TORTHON',
    'PATIO': 'TRACTOCAMION PATIO',
    'DOBLE': 'TRACTOCAMION DOBLE',
}

# ---------- Catalogos de tipo de equipo ----------

REMOLQUE_TIPOS = {
    'EQUIPO REMOLQUE', 'REMOLQUE', 'CAJA', 'THERMO', 'CARROSERIA',
}
DOLLY_TIPOS = {
    'EQUIPO DOLLY', 'DOLLY',
}
# Todo lo demas se considera motriz (incluye DESCONOCIDO y futuros tipos).


def clasificar_tipo_equipo(tipo_unidad: str) -> str:
    """Mapea `Tipo de Unidad` BD a `Tipo Equipo` (`Motriz`/`Remolque`/`Dolly`)."""
    # NaN es truthy en Python (`not float('nan')` == False), asi que un valor
    # faltante de una fuente que no garantiza '' (ver io/excel.py) se colaba
    # hasta .strip() y reventaba con AttributeError (bug real 10/07/2026).
    if pd.isna(tipo_unidad) or not tipo_unidad:
        return 'Motriz'
    tu = tipo_unidad.strip().upper()
    if tu in REMOLQUE_TIPOS:
        return 'Remolque'
    if tu in DOLLY_TIPOS:
        return 'Dolly'
    return 'Motriz'


# ---------- Mapeo de estatus BD -> categorias canonicas ----------

# Los 8 status canonicos que cuentan dentro de `Dias Asignado` (motrices).
STATUS_CANONICOS = (
    'Operando', 'Disponible', 'Sin Operador', 'Taller',
    'Gestoria', 'Descanso', 'Rescate', 'Puesto A Punto',
)
SIN_ASIGNACION_BD = 'Sin Asignacion'  # valor literal en BD `estatus_2`

# Lookup case-insensitive: vocabulario Excel/Sheets trae acentos (ya cubiertos
# por normalize_text) y diferencias de mayusculas (ej. "Puesto a Punto" vs
# canonico "Puesto A Punto"); este lookup resuelve ambos sin alias manuales.
_STATUS_LOOKUP = {s.lower(): s for s in STATUS_CANONICOS}
_STATUS_LOOKUP[SIN_ASIGNACION_BD.lower()] = SIN_ASIGNACION_BD


def categoria_status(estatus: str) -> str:
    """Mapea un valor de status (BD, Excel o Sheets) a la categoria de conteo.

    Devuelve uno de:
    - `'Sin Asignacion'`
    - uno de los 8 STATUS_CANONICOS
    - `'Otros Status'` (resiliencia: Activo, Baja, Inhabilitado, etc., o cualquier
      string no contemplado en el futuro).

    Acentos (Gestoría -> Gestoria) y diferencias de mayusculas (Puesto a
    Punto -> Puesto A Punto) se normalizan antes del match.
    """
    # Mismo blindaje que clasificar_tipo_equipo: NaN es truthy, no lo atrapa
    # `if not estatus:` y revienta en .strip() (bug real 10/07/2026).
    if pd.isna(estatus) or not estatus:
        return 'Otros Status'
    e = normalize_text(estatus.strip())
    return _STATUS_LOOKUP.get(e.lower(), 'Otros Status')


# ---------- Contrato de salida ----------

# Columnas numericas del schema (se inicializan a 0 si faltan; el resto a '').
_NUMERIC_EQUIPO_COLS = frozenset({
    'Dias Asignado', 'Dias Sin Asignacion',
    'Dias Operando', 'Dias Disponible', 'Dias Sin Operador', 'Dias Taller',
    'Dias Gestoria', 'Dias Descanso', 'Dias Rescate', 'Dias Puesto A Punto',
    'Dias Otros Status', 'Dias Activo',
    'KM Cargado', 'KM Vacio', 'KM Total', 'Diesel LTS', 'Rendimiento',
    'Viajes', 'Densidad Viaje',
    'Objetivo KM Corte', 'Objetivo Viajes Corte',
    'Complemento KM Objetivo', 'Complemento Viajes Objetivo',
    'Objetivo KM Total', 'Objetivo Viajes Total',
    'Cump KM %', 'Cump Viajes %', '% Operativo',
    'Tendencia KM', 'Tendencia Viajes',
})

# Columnas del DataFrame que produce `EquipmentAggregator.aggregate()`.
# El orden aqui es el orden final en la hoja Excel.
EQUIPO_OUTPUT_COLS = [
    # Identidad
    'Equipo Motriz', 'Tipo Equipo',
    # Asignacion vigente
    'Gerencia', 'Operacion', 'Tipo de Unidad', 'Circuito', 'Operacion Cedula',
    'Estatus',
    # Dias eje 1 (asignacion)
    'Dias Asignado', 'Dias Sin Asignacion',
    # Dias eje 2 (status, suman Dias Asignado)
    'Dias Operando', 'Dias Disponible', 'Dias Sin Operador', 'Dias Taller',
    'Dias Gestoria', 'Dias Descanso', 'Dias Rescate', 'Dias Puesto A Punto',
    'Dias Otros Status',
    # Dias transversal
    'Dias Activo',
    # Operativos (suma desde Viajes, excluye comodatos)
    'KM Cargado', 'KM Vacio', 'KM Total', 'Diesel LTS', 'Rendimiento',
    'Viajes', 'Densidad Viaje',
    # Objetivos: corte + complemento futuro = total al cierre del mes
    'Objetivo KM Corte', 'Objetivo Viajes Corte',
    'Complemento KM Objetivo', 'Complemento Viajes Objetivo',
    'Objetivo KM Total', 'Objetivo Viajes Total',
    'Cump KM %', 'Cump Viajes %',
    # Eficiencia
    '% Operativo',
    # Tendencia (se llena en P4 al integrar con OpCedula)
    'Tendencia KM', 'Tendencia Viajes',
    # Ultimo viaje (excluye comodatos)
    'Numero de Viaje', 'Fecha Ult Viaje', 'Centro', 'Tipo De Operacion',
    'Ruta', 'Denominacion', 'Alias Origen', 'Alias Destino', 'ClaveCategoria',
]


# ---------- Asignacion vigente ----------

@dataclass(frozen=True)
class AsignacionVigente:
    """Foto de la asignacion del equipo al corte del periodo."""
    gerencia: str
    operacion: str
    tipo_unidad: str
    circuito: str
    operacion_cedula: str
    estatus: str

    @classmethod
    def pendiente(cls, tipo_unidad: str = '') -> "AsignacionVigente":
        """Asignacion para equipos egresados o nunca asignados."""
        return cls(
            gerencia='Pendiente',
            operacion='POR ASIGNAR',
            tipo_unidad=tipo_unidad,
            circuito='POR ASIGNAR',
            operacion_cedula=f'POR ASIGNAR {tipo_unidad}'.strip(),
            estatus='Sin Asignacion',
        )


def _calcular_opcedula(operacion: str, circuito: str, tipo_unidad: str,
                       special_circuits: Iterable[str]) -> str:
    """Misma regla que ChangeTracker/ComodatoManager.

    SPECIAL_CIRCUITS -> usa tipo_unidad; resto -> usa circuito.
    """
    op = (operacion or '').upper()
    ci = (circuito or '').upper()
    tu = (tipo_unidad or '').upper()
    if ci in {s.upper() for s in special_circuits}:
        return f'{op} {tu}'
    return f'{op} {ci}'


# ---------- Aggregator ----------

class EquipmentAggregator:
    """Agrega cedulas + viajes en 1 fila por equipo unico.

    Uso:
        agg = EquipmentAggregator(df_cedulas, df_trips, obj_mapping, period, special_circuits)
        df_equipo = agg.aggregate()
    """

    def __init__(self, df_cedulas: pd.DataFrame, df_trips: pd.DataFrame,
                 obj_mapping: Optional[Dict[str, Dict[str, float]]],
                 period: PeriodContext,
                 special_circuits: Iterable[str],
                 log_callback=print):
        self.df_cedulas = df_cedulas
        self.df_trips = df_trips
        self.obj_mapping = obj_mapping or {}
        self.period = period
        self.special_circuits = set(special_circuits)
        self.log = log_callback

        # Subconjunto de viajes validos (no comodatos) — usado para Dias Activo,
        # ultimo viaje, KM/Viajes/Diesel agregados.
        if df_trips.empty:
            self.df_trips_validos = df_trips.copy()
        elif 'ClaveCategoria' in df_trips.columns:
            self.df_trips_validos = df_trips[df_trips['ClaveCategoria'] != 'COM'].copy()
        else:
            self.df_trips_validos = df_trips.copy()
        if 'Fecha creación_date' not in self.df_trips_validos.columns and \
                'Fecha creación' in self.df_trips_validos.columns:
            self.df_trips_validos['Fecha creación_date'] = (
                pd.to_datetime(self.df_trips_validos['Fecha creación'], errors='coerce')
                .dt.date
            )

    # ---------- Entrada principal ----------

    def aggregate(self) -> pd.DataFrame:
        """Construye el DataFrame con 1 fila por equipo unico del periodo."""
        equipos = self._universo_equipos()
        motrices_mapping = self._tipos_por_equipo()
        motrices_dominantes = self._motrices_dominantes_por_arrastre()

        registros = []
        for equipo in sorted(equipos):
            tipo = motrices_mapping.get(equipo, 'Motriz')
            if tipo == 'Motriz':
                registros.append(self._fila_motriz(equipo))
            else:
                motriz_dom = motrices_dominantes.get(equipo)
                registros.append(self._fila_arrastre(equipo, tipo, motriz_dom))

        df = pd.DataFrame(registros)
        # Asegura el orden y la presencia de todas las columnas del contrato
        for col in EQUIPO_OUTPUT_COLS:
            if col not in df.columns:
                df[col] = 0 if col in _NUMERIC_EQUIPO_COLS else ''
        df = df[EQUIPO_OUTPUT_COLS]
        self.log(f'[EQ] Por Equipo: {len(df)} filas '
                 f'({(df["Tipo Equipo"] == "Motriz").sum()} motrices, '
                 f'{(df["Tipo Equipo"] != "Motriz").sum()} arrastres)')
        return df

    def aggregate_detalle_opcedula(self) -> pd.DataFrame:
        """Detalle motriz x OpCedula historica (dia-por-dia) para Por Operacion.

        A diferencia de `aggregate()` (1 fila por equipo, asignacion vigente al
        cierre), aqui un motriz genera 1 fila por cada `Operación cedula`
        historica distinta que tuvo durante el periodo. Reusa
        `_metricas_operativas` para que KM/Diesel/Viajes se deriven igual que
        en Por Equipo (mismas columnas, mismos fallbacks).
        """
        cols = ['Equipo Motriz', 'Operación cedula', 'KM Cargado', 'KM Vacio',
                'KM Total', 'Diesel LTS', 'Rendimiento', 'Viajes', 'Densidad Viaje']
        if self.df_trips_validos.empty or 'Operación cedula' not in self.df_trips_validos.columns:
            return pd.DataFrame(columns=cols)

        df = self.df_trips_validos.copy()
        df['Equipo Motriz'] = df['Equipo Motriz'].astype(str).str.strip().str.upper()

        registros = []
        for (equipo, opcedula), grupo in df.groupby(['Equipo Motriz', 'Operación cedula']):
            registros.append({
                'Equipo Motriz': equipo,
                'Operación cedula': opcedula,
                **self._metricas_operativas(grupo),
            })
        if not registros:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(registros)[cols]

    # ---------- Helpers de universo y tipo ----------

    def _universo_equipos(self) -> set[str]:
        """Equipos que aparecen en al menos un dia de cedula o un viaje valido."""
        equipos = set()
        if not self.df_cedulas.empty:
            equipos |= set(self.df_cedulas['Unidades'].astype(str).str.strip().str.upper())
        # Equipos motrices desde viajes validos
        if not self.df_trips_validos.empty:
            equipos |= set(self.df_trips_validos['Equipo Motriz'].dropna().astype(str)
                           .str.strip().str.upper())
            for col in ('Equipo Remolque 1', 'Equipo Remolque 2', 'Equipo Dolly'):
                if col in self.df_trips_validos.columns:
                    equipos |= set(self.df_trips_validos[col].dropna().astype(str)
                                   .str.strip().str.upper())
        equipos.discard('')
        equipos.discard('NAN')
        return equipos

    def _tipos_por_equipo(self) -> Dict[str, str]:
        """Mapea equipo -> tipo (Motriz/Remolque/Dolly).

        Prioridad:
        1. Si el equipo esta en cedula con un Tipo de Unidad conocido -> ese.
        2. Si no esta en cedula pero aparece en col `Equipo Remolque *` o `Equipo Dolly`
           de viajes -> Remolque o Dolly segun la columna.
        3. Default: Motriz.
        """
        mapping: Dict[str, str] = {}

        if not self.df_cedulas.empty:
            # Toma el ultimo Tipo de Unidad conocido (por orden de fecha) para cada equipo
            ced_sorted = self.df_cedulas.sort_values('Fecha Cedula_dt')
            for unit, grupo in ced_sorted.groupby(
                ced_sorted['Unidades'].astype(str).str.strip().str.upper()
            ):
                ultimo_tipo = grupo['Tipo de Unidad'].iloc[-1]
                mapping[unit] = clasificar_tipo_equipo(ultimo_tipo)

        if not self.df_trips_validos.empty:
            for col, tipo_default in (
                ('Equipo Remolque 1', 'Remolque'),
                ('Equipo Remolque 2', 'Remolque'),
                ('Equipo Dolly', 'Dolly'),
            ):
                if col in self.df_trips_validos.columns:
                    for eq in self.df_trips_validos[col].dropna().astype(str).str.strip().str.upper():
                        if eq and eq not in mapping:
                            mapping[eq] = tipo_default

        return mapping

    def _motrices_dominantes_por_arrastre(self) -> Dict[str, str]:
        """Para cada arrastre, identifica el motriz con el que mas viajes comparte.

        Recorre viajes validos: por cada (Equipo Remolque 1/2, Equipo Dolly) cuenta
        cuantas veces aparecio con cada Equipo Motriz. Devuelve el motriz mas frecuente.
        """
        if self.df_trips_validos.empty:
            return {}

        conteos: Dict[str, Dict[str, int]] = {}
        for col in ('Equipo Remolque 1', 'Equipo Remolque 2', 'Equipo Dolly'):
            if col not in self.df_trips_validos.columns:
                continue
            sub = self.df_trips_validos[['Equipo Motriz', col]].dropna()
            for motriz, arr in zip(sub['Equipo Motriz'].astype(str).str.strip().str.upper(),
                                    sub[col].astype(str).str.strip().str.upper()):
                if not arr or not motriz:
                    continue
                conteos.setdefault(arr, {}).setdefault(motriz, 0)
                conteos[arr][motriz] += 1

        return {arr: max(c.items(), key=lambda x: x[1])[0]
                for arr, c in conteos.items() if c}

    # ---------- Construccion de filas ----------

    def _fila_motriz(self, equipo: str) -> dict:
        """Fila para un equipo motriz."""
        ced = self._cedulas_del_equipo(equipo)
        viajes = self._viajes_del_equipo(equipo)

        asignacion = self._asignacion_vigente_motriz(ced)
        dias = self._contar_dias_motriz(ced)
        dias['Dias Activo'] = self._dias_activo(viajes)
        op_metrics = self._metricas_operativas(viajes)
        objetivos = self._calcular_objetivos(ced, asignacion)
        ultimo = self._ultimo_viaje(viajes)

        return self._consolidar_fila(
            equipo=equipo, tipo='Motriz',
            asignacion=asignacion, dias=dias,
            op_metrics=op_metrics, objetivos=objetivos, ultimo=ultimo,
        )

    def _fila_arrastre(self, equipo: str, tipo: str, motriz_dom: Optional[str]) -> dict:
        """Fila para un arrastre.

        Reglas v0.5.0:
        - Hereda asignacion del motriz dominante (sin co-viajes -> POR ASIGNAR).
        - Status reconstruido: Operando=dias con viaje, Disponible=dias asignado-Activo.
        - Resto de sub-status = 0.
        - Dias Asignado del arrastre = dias en que el motriz dominante estuvo asignado.
        """
        viajes = self._viajes_del_arrastre(equipo)

        if motriz_dom:
            ced_motriz_dom = self._cedulas_del_equipo(motriz_dom)
            asignacion = self._asignacion_vigente_motriz(ced_motriz_dom)
            dias_motriz_asignado = self._contar_dias_motriz(ced_motriz_dom)['Dias Asignado']
        else:
            asignacion = AsignacionVigente.pendiente()
            dias_motriz_asignado = 0

        dias_activo = self._dias_activo(viajes)
        dias_asignado = max(dias_motriz_asignado, dias_activo)  # nunca menos que sus viajes
        dias_sin_asignacion = self.period.dias_corrientes - dias_asignado
        dias = {
            'Dias Asignado': dias_asignado,
            'Dias Sin Asignacion': max(dias_sin_asignacion, 0),
            'Dias Operando': dias_activo,
            'Dias Disponible': max(dias_asignado - dias_activo, 0),
            'Dias Sin Operador': 0,
            'Dias Taller': 0,
            'Dias Gestoria': 0,
            'Dias Descanso': 0,
            'Dias Rescate': 0,
            'Dias Puesto A Punto': 0,
            'Dias Otros Status': 0,
            'Dias Activo': dias_activo,
        }
        # Para arrastres el estatus vigente = Operando si tuvo viaje en el ultimo dia,
        # Disponible si esta asignado pero sin viaje ese dia, o Sin Asignacion.
        asignacion = self._estatus_vigente_arrastre(asignacion, viajes, dias_asignado)
        op_metrics = self._metricas_operativas(viajes)
        # Los arrastres no tienen objetivo directo (los objetivos son por OpCedula motriz).
        objetivos = {'Objetivo KM Corte': 0.0, 'Objetivo Viajes Corte': 0.0,
                     'Complemento KM Objetivo': 0.0, 'Complemento Viajes Objetivo': 0.0,
                     'Objetivo KM Total': 0.0, 'Objetivo Viajes Total': 0.0,
                     'Cump KM %': None, 'Cump Viajes %': None}
        ultimo = self._ultimo_viaje(viajes)

        return self._consolidar_fila(
            equipo=equipo, tipo=tipo,
            asignacion=asignacion, dias=dias,
            op_metrics=op_metrics, objetivos=objetivos, ultimo=ultimo,
        )

    # ---------- Subrutinas ----------

    def _cedulas_del_equipo(self, equipo: str) -> pd.DataFrame:
        if self.df_cedulas.empty:
            return self.df_cedulas
        mask = self.df_cedulas['Unidades'].astype(str).str.strip().str.upper() == equipo
        ced = self.df_cedulas[mask]
        # Filtrar al rango corriente del periodo
        mask_periodo = (ced['Fecha Cedula_dt'] >= self.period.fecha_inicio_mes) & \
                       (ced['Fecha Cedula_dt'] <= self.period.fecha_corte)
        return ced[mask_periodo]

    def _viajes_del_equipo(self, equipo: str) -> pd.DataFrame:
        if self.df_trips_validos.empty:
            return self.df_trips_validos
        mask = self.df_trips_validos['Equipo Motriz'].astype(str).str.strip().str.upper() == equipo
        return self.df_trips_validos[mask]

    def _viajes_del_arrastre(self, equipo: str) -> pd.DataFrame:
        if self.df_trips_validos.empty:
            return self.df_trips_validos
        mask = pd.Series(False, index=self.df_trips_validos.index)
        for col in ('Equipo Remolque 1', 'Equipo Remolque 2', 'Equipo Dolly'):
            if col in self.df_trips_validos.columns:
                mask |= (self.df_trips_validos[col].astype(str).str.strip().str.upper() == equipo)
        return self.df_trips_validos[mask]

    def _asignacion_vigente_motriz(self, ced: pd.DataFrame) -> AsignacionVigente:
        """Foto de la asignacion en el ultimo dia de cedula del periodo.

        Si la cedula del ultimo dia es Sin Asignacion (egreso o nunca asignado)
        -> AsignacionVigente.pendiente().
        """
        if ced.empty:
            return AsignacionVigente.pendiente()
        ultima = ced.sort_values('Fecha Cedula_dt').iloc[-1]
        # Si el ultimo dia es Sin Asignacion, conservamos tipo_unidad pero marcamos POR ASIGNAR
        if categoria_status(ultima.get('Operando', '')) == 'Sin Asignacion':
            return AsignacionVigente.pendiente(ultima.get('Tipo de Unidad', ''))
        op = ultima.get('Operación', '')
        ci = ultima.get('Circuito', '')
        tu = ultima.get('Tipo de Unidad', '')
        return AsignacionVigente(
            gerencia=ultima.get('Gerencia', ''),
            operacion=op,
            tipo_unidad=tu,
            circuito=ci,
            operacion_cedula=_calcular_opcedula(op, ci, tu, self.special_circuits),
            estatus=ultima.get('Operando', ''),
        )

    def _estatus_vigente_arrastre(self, asignacion: AsignacionVigente,
                                   viajes: pd.DataFrame,
                                   dias_asignado: int) -> AsignacionVigente:
        """Sobrescribe el `estatus` del arrastre segun la regla v0.5.0."""
        # Buscar si tuvo viaje en la fecha de corte
        fecha_corte_d = self.period.fecha_corte.date()
        if not viajes.empty and 'Fecha creación_date' in viajes.columns:
            tuvo_viaje_corte = (viajes['Fecha creación_date'] == fecha_corte_d).any()
        else:
            tuvo_viaje_corte = False
        if tuvo_viaje_corte:
            estatus_nuevo = 'Operando'
        elif dias_asignado > 0:
            estatus_nuevo = 'Disponible'
        else:
            estatus_nuevo = 'Sin Asignacion'
        return AsignacionVigente(
            gerencia=asignacion.gerencia,
            operacion=asignacion.operacion,
            tipo_unidad=asignacion.tipo_unidad,
            circuito=asignacion.circuito,
            operacion_cedula=asignacion.operacion_cedula,
            estatus=estatus_nuevo,
        )

    def _contar_dias_motriz(self, ced: pd.DataFrame) -> Dict[str, int]:
        """Cuenta dias por categoria sobre el rango corriente del periodo.

        Si la cedula del equipo no cubre todo el rango (egreso/nunca asignado),
        los dias faltantes cuentan en `Dias Sin Asignacion`.
        """
        dias = {
            'Dias Asignado': 0,
            'Dias Sin Asignacion': 0,
            'Dias Operando': 0,
            'Dias Disponible': 0,
            'Dias Sin Operador': 0,
            'Dias Taller': 0,
            'Dias Gestoria': 0,
            'Dias Descanso': 0,
            'Dias Rescate': 0,
            'Dias Puesto A Punto': 0,
            'Dias Otros Status': 0,
        }

        rango = self.period.rango_corriente()
        if ced.empty:
            dias['Dias Sin Asignacion'] = len(rango)
            return dias

        # Diccionario fecha -> status para acceso rapido
        by_date = {row['Fecha Cedula_dt'].normalize(): row['Operando']
                   for _, row in ced.iterrows()}

        for fecha in rango:
            estatus = by_date.get(fecha.normalize())
            categoria = categoria_status(estatus or '')
            if categoria == 'Sin Asignacion' or estatus is None:
                dias['Dias Sin Asignacion'] += 1
            elif categoria == 'Otros Status':
                dias['Dias Asignado'] += 1
                dias['Dias Otros Status'] += 1
            else:
                dias['Dias Asignado'] += 1
                dias[f'Dias {categoria}'] += 1

        return dias

    def _dias_activo(self, viajes: pd.DataFrame) -> int:
        if viajes.empty or 'Fecha creación_date' not in viajes.columns:
            return 0
        fechas = viajes['Fecha creación_date'].dropna().unique()
        return len(fechas)

    def _metricas_operativas(self, viajes: pd.DataFrame) -> Dict[str, float]:
        """Suma KM/Diesel/Viajes excluyendo comodatos.

        Usa las columnas YA CALCULADAS por `process_trips_optimized`:
        - `KM_cargado` = KMLiqCargadoFinal si StatusViaje != 'A', sino Distancia.
          (KMLiqCargadoFinal es 0 hasta que el viaje se liquida; mientras tanto
          Distancia es el km cargado real reportado.)
        - `KM_vacio` = KMLiqVacioFinal.
        - `KM_total` = KM_cargado + KM_vacio.
        - `Viajes_count` = 1 si el viaje cuenta, 0 si StatusViaje='X' o caso CUERNAVACA
          con KM=0. Respeta la regla de negocio de `_calculate_trips_efficient`.

        Las columnas crudas (`KMLiqCargadoFinal`, `KMLiqVacioFinal`) NO se usan
        directamente — darian 0 en viajes sin liquidar.
        """
        if viajes.empty:
            return {'KM Cargado': 0.0, 'KM Vacio': 0.0, 'KM Total': 0.0,
                    'Diesel LTS': 0.0, 'Rendimiento': 0.0,
                    'Viajes': 0, 'Densidad Viaje': 0.0}
        km_cargado = viajes.get('KM_cargado', pd.Series(dtype=float)).fillna(0).sum()
        km_vacio = viajes.get('KM_vacio', pd.Series(dtype=float)).fillna(0).sum()
        km_total = viajes.get('KM_total', pd.Series(dtype=float)).fillna(0).sum()
        # Si por alguna razon KM_total no esta calculado, lo derivamos
        if km_total == 0 and (km_cargado > 0 or km_vacio > 0):
            km_total = km_cargado + km_vacio
        diesel = viajes.get('Diesel_LTS', pd.Series(dtype=float)).fillna(0).sum()
        # Viajes_count manda (respeta StatusViaje='X' y CUERNAVACA KM=0).
        # Fallback a len() solo si la columna no existe (tests sin pipeline completo).
        if 'Viajes_count' in viajes.columns:
            n_viajes = int(viajes['Viajes_count'].fillna(0).sum())
        else:
            n_viajes = len(viajes)
        rendimiento = km_total / diesel if diesel > 0 else 0.0
        densidad = km_total / n_viajes if n_viajes > 0 else 0.0
        return {
            'KM Cargado': round(km_cargado, 2),
            'KM Vacio': round(km_vacio, 2),
            'KM Total': round(km_total, 2),
            'Diesel LTS': round(diesel, 2),
            'Rendimiento': round(rendimiento, 2),
            'Viajes': int(n_viajes),
            'Densidad Viaje': round(densidad, 2),
        }

    def _calcular_objetivos(self, ced: pd.DataFrame,
                            asignacion: AsignacionVigente) -> Dict[str, float]:
        """Objetivo proyectado al CIERRE del mes (corte + futuro).

        Componentes:
        - Obj corte = Σ Objetivo KM Diario por dia asignado en rango_corriente.
          Sin importar status: un dia en Taller dentro de VEND CENTRO sigue
          sumando el objetivo de VEND CENTRO.
        - Obj futuro = Objetivo Diario de la OpCedula VIGENTE × dias_restantes_mes.
          Asume que el equipo permanece en su asignacion vigente hasta cierre.
        - Obj cierre = corte + futuro. Esta cantidad ES comparable con Tendencia KM.

        Si la asignacion vigente es POR ASIGNAR o sin objetivo registrado, el
        complemento futuro es 0.
        """
        if ced.empty:
            return {'Objetivo KM Corte': 0.0, 'Objetivo Viajes Corte': 0.0,
                    'Complemento KM Objetivo': 0.0, 'Complemento Viajes Objetivo': 0.0,
                    'Objetivo KM Total': 0.0, 'Objetivo Viajes Total': 0.0,
                    'Cump KM %': None, 'Cump Viajes %': None}

        # --- Obj corte: dias asignados en el rango ---
        rango = self.period.rango_corriente()
        by_date = {row['Fecha Cedula_dt'].normalize(): row for _, row in ced.iterrows()}

        obj_km_corte = 0.0
        obj_v_corte = 0.0
        for fecha in rango:
            row = by_date.get(fecha.normalize())
            if row is None:
                continue
            categoria = categoria_status(row.get('Operando', '') or '')
            if categoria == 'Sin Asignacion':
                continue
            op = row.get('Operación', '')
            ci = row.get('Circuito', '')
            tu = row.get('Tipo de Unidad', '')
            opcedula = _calcular_opcedula(op, ci, tu, self.special_circuits)
            obj_entry = self.obj_mapping.get(opcedula)
            if not obj_entry:
                continue
            obj_km_corte += float(obj_entry.get('Objetivo KM Diario', 0) or 0)
            obj_v_corte += float(obj_entry.get('Objetivo Viajes Diario', 0) or 0)

        # --- Obj futuro: asignacion vigente × dias restantes mes ---
        obj_entry_vig = self.obj_mapping.get(asignacion.operacion_cedula, {})
        obj_km_diario_vig = float(obj_entry_vig.get('Objetivo KM Diario', 0) or 0)
        obj_v_diario_vig = float(obj_entry_vig.get('Objetivo Viajes Diario', 0) or 0)
        compl_km = obj_km_diario_vig * self.period.dias_restantes
        compl_v = obj_v_diario_vig * self.period.dias_restantes

        obj_km_total = obj_km_corte + compl_km
        obj_v_total = obj_v_corte + compl_v

        return {
            'Objetivo KM Corte': round(obj_km_corte, 2),
            'Objetivo Viajes Corte': round(obj_v_corte, 2),
            'Complemento KM Objetivo': round(compl_km, 2),
            'Complemento Viajes Objetivo': round(compl_v, 2),
            'Objetivo KM Total': round(obj_km_total, 2),
            'Objetivo Viajes Total': round(obj_v_total, 2),
            'Cump KM %': None,  # se calcula al consolidar con KM Total
            'Cump Viajes %': None,
        }

    def _ultimo_viaje(self, viajes: pd.DataFrame) -> Dict[str, object]:
        """Datos del ultimo viaje valido (no comodato). Vacio si no hubo."""
        empty = {k: '' for k in ['Numero de Viaje', 'Fecha Ult Viaje', 'Centro',
                                  'Tipo De Operacion', 'Ruta', 'Denominacion',
                                  'Alias Origen', 'Alias Destino', 'ClaveCategoria']}
        if viajes.empty or 'Fecha creación' not in viajes.columns:
            return empty
        fechas = pd.to_datetime(viajes['Fecha creación'], errors='coerce')
        idx = fechas.idxmax()
        if pd.isna(idx):
            return empty
        row = viajes.loc[idx]
        return {
            'Numero de Viaje': row.get('Número de Viaje', ''),
            'Fecha Ult Viaje': fechas.loc[idx].strftime('%d/%m/%Y'),
            'Centro': row.get('Centro', ''),
            'Tipo De Operacion': row.get('Tipo De Operación', ''),
            'Ruta': row.get('Ruta', ''),
            'Denominacion': row.get('Denominación', ''),
            'Alias Origen': row.get('Alias Origen', ''),
            'Alias Destino': row.get('Alias Destino', ''),
            'ClaveCategoria': row.get('ClaveCategoria', ''),
        }

    def _consolidar_fila(self, *, equipo: str, tipo: str,
                          asignacion: AsignacionVigente, dias: Dict[str, int],
                          op_metrics: Dict[str, float],
                          objetivos: Dict[str, float],
                          ultimo: Dict[str, object]) -> dict:
        """Consolida los pedazos en un dict listo para el DataFrame final."""
        km_total = op_metrics['KM Total']
        n_viajes = op_metrics['Viajes']
        obj_km = objetivos['Objetivo KM Total']
        obj_v = objetivos['Objetivo Viajes Total']
        cump_km = round(km_total / obj_km * 100, 2) if obj_km > 0 else None
        cump_v = round(n_viajes / obj_v * 100, 2) if obj_v > 0 else None

        dias_corrientes = max(self.period.dias_corrientes, 1)
        pct_operativo = round(dias['Dias Activo'] / dias_corrientes * 100, 2)

        fila = {
            'Equipo Motriz': equipo,
            'Tipo Equipo': tipo,
            'Gerencia': asignacion.gerencia,
            'Operacion': asignacion.operacion,
            'Tipo de Unidad': asignacion.tipo_unidad,
            'Circuito': asignacion.circuito,
            'Operacion Cedula': asignacion.operacion_cedula,
            'Estatus': asignacion.estatus,
            **dias,
            **op_metrics,
            'Objetivo KM Corte': objetivos.get('Objetivo KM Corte', 0.0),
            'Objetivo Viajes Corte': objetivos.get('Objetivo Viajes Corte', 0.0),
            'Complemento KM Objetivo': objetivos.get('Complemento KM Objetivo', 0.0),
            'Complemento Viajes Objetivo': objetivos.get('Complemento Viajes Objetivo', 0.0),
            'Objetivo KM Total': obj_km,
            'Objetivo Viajes Total': obj_v,
            'Cump KM %': cump_km,
            'Cump Viajes %': cump_v,
            '% Operativo': pct_operativo,
            'Tendencia KM': 0.0,    # placeholder, lo llena post_calcular_tendencia
            'Tendencia Viajes': 0.0,
            **ultimo,
        }
        return fila

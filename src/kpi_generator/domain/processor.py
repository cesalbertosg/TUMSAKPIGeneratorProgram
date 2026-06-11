"""Motor principal de procesamiento KPI.

Carga viajes, combustible, cédulas y objetivos; calcula 32 métricas por
operación-cédula con detección de cambios de equipo; expone los DataFrames
listos para escribir a Excel y Google Sheets.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from kpi_generator.config import Config, LogLevel
from kpi_generator.domain.change_tracker import ChangeTracker
from kpi_generator.domain.comodato import ComodatoManager
from kpi_generator.domain.equipment import CLAVE_CATEGORIA_A_TIPO_UNIDAD, EquipmentAggregator, normalize_text
from kpi_generator.domain.opcedula import OpcedulaAggregator, post_calcular_tendencia
from kpi_generator.domain.period import PeriodContext
from kpi_generator.io import excel as excel_io
from kpi_generator.io import sheets as sheets_io
from kpi_generator.io.date_range import DateRangeError, derive_date_range

# --- Columnas Tier 1 deadweight de la hoja Viajes ---
# llaveremolque y EqAsignados son intermediarios solo usados internamente para
# calcular `cuenta llaverem` y verificación de asignación (no consumidos por Looker).
TRIP_DEADWEIGHT_COLS = ['llaveremolque', 'EqAsignados']

# Nombres canónicos de hojas Excel y tabs Sheets (v0.4.0).
SHEET_NAMES = {
    'resumen': 'Resumen',
    'por_equipo': 'Por Equipo',           # antes: 'KPIs per Equipment'
    'trip_data': 'Viajes',                # Excel ahora consistente con Sheets
    'cambios': 'Resumen de Cambios',
    'por_operacion': 'Por Operación',     # antes: 'KPIs OpCedula'
    'objetivos': 'Objetivos',
    'promedio': 'Promedio KM por Unidad', # antes: 'PromedioKMunitOps'
    'audit': 'Cedulas Rellenadas',
    'inconsistencias': 'Inconsistencias',
}

# Nombres de tabs en Google Sheets — mantenemos consistencia con Excel desde v0.4.0.
# IMPORTANTE: al promover este cambio, hay que borrar manualmente los tabs viejos
# (Equipos, OpCedula, PromedioKMunitOps) del spreadsheet — Looker debe actualizar fuentes.
SHEETS_TAB_NAMES = {
    'resumen': 'Resumen',
    'por_equipo': 'Por Equipo',
    'trip_data': 'Viajes',
    'cambios': 'Cambios',
    'por_operacion': 'Por Operación',
    'objetivos': 'Objetivos',
    'promedio': 'Promedio KM por Unidad',
    'inconsistencias': 'Inconsistencias',
}


class DataProcessor:
    """Motor optimizado de procesamiento de datos para análisis de KPIs de transporte."""

    def __init__(self, log_callback=print, log_level=LogLevel.INFO):
        self.log_func = log_callback
        self.log_level = log_level
        self._objective_cache = {}
        self._cedula_cache = {}
        self._stats = {'total_assigned': 0, 'periods_processed': 0}
        self._inconsistencias: List[dict] = []
        self.comodato_manager = ComodatoManager()
        self.change_tracker = ChangeTracker(log_callback)

    def log(self, message: str, level: LogLevel = LogLevel.INFO, code: str = None):
        """Sistema de logging simplificado con códigos."""
        if level.value <= self.log_level.value:
            prefix = f"[{code}]" if code else ""
            self.log_func(f"{prefix} {message}")

    def _registrar_inconsistencia(self, unidad, fecha, campo: str, valor_aplicado,
                                   motivo: str, valor_original=None) -> None:
        """Registra un fill/fallback/cruce aplicado durante la carga de cédulas.

        Acumulado en `self._inconsistencias`; `run_pipeline` lo vuelca a la
        hoja "Inconsistencias" del Excel y del Sheets de salida.
        """
        self._inconsistencias.append({
            'Unidad': unidad,
            'Fecha': fecha,
            'Campo': campo,
            'Valor Original': valor_original,
            'Valor Aplicado': valor_aplicado,
            'Motivo': motivo,
        })
    
    def load_daily_cedulas(self, cedulas_folder: str) -> Optional[pd.DataFrame]:
        """Delegado a `io.excel.load_daily_cedulas` (refactor v0.4.3)."""
        return excel_io.load_daily_cedulas(cedulas_folder, self.log)

    def _fill_missing_dates(self, df_cedulas: pd.DataFrame) -> pd.DataFrame:
        """Delegado a `io.excel.fill_missing_dates` (refactor v0.4.3).

        Se mantiene como metodo de instancia para compatibilidad con
        `load_cedula_from_sheets`, que tambien rellena fechas ausentes.
        """
        return excel_io.fill_missing_dates(df_cedulas)

    def load_cedula_from_sheets(self, sheet_id: str, tab_name: str = None) -> Optional[pd.DataFrame]:
        """Delegado a `io.sheets.load_cedula_from_sheet` (refactor v0.4.3)."""
        return sheets_io.load_cedula_from_sheet(sheet_id, self.log, tab_name)

    def load_data(self, trips_file: str, fuel_file: str, cedulas_folder: str,
                  objectives_file: str = None, cedulas_sheet_id: str = None,
                  cedulas_tab: str = None, cedulas_source: str = None) -> Optional[Dict]:
        """Cargar y validar archivos de entrada optimizado.

        `cedulas_source` controla la fuente de cédulas: "db" | "excel" | "sheets".
        Si es None, se usa Config.CEDULAS_SOURCE (default "excel").
        """
        try:
            self.log("Cargando archivos", code="LOAD")
            data = {}

            file_configs = [
                ('trips', trips_file, Config.COLUMNS["trips"]),
                ('fuel', fuel_file, Config.COLUMNS["fuel"])
            ]

            for key, file_path, required_cols in file_configs:
                df = pd.read_excel(file_path)
                missing = [col for col in required_cols if col not in df.columns]
                if missing:
                    self.log(f"Error {key} - Columnas faltantes: {missing}", LogLevel.ERROR, "ERR")
                    return None
                data[key] = df
                self.log(f"{key}: {len(df)} registros", LogLevel.DEBUG, "OK")

            source = (cedulas_source or Config.CEDULAS_SOURCE).lower()
            df_cedulas, df_cedulas_audit = self._load_cedulas_by_source(
                source, trips_file, cedulas_folder, cedulas_sheet_id, cedulas_tab
            )
            if df_cedulas is None:
                return None
            df_cedulas = self._apply_cedula_fallbacks(df_cedulas, data['trips'])
            data['cedulas'] = df_cedulas
            data['cedulas_audit'] = df_cedulas_audit  # vacío si source != "db"

            if objectives_file and Path(objectives_file).exists():
                df_obj = pd.read_excel(objectives_file)
                missing = [col for col in Config.COLUMNS["objectives"] if col not in df_obj.columns]
                if missing:
                    self.log(f"Error objetivos - Columnas faltantes: {missing}", LogLevel.ERROR, "ERR")
                    return None
                # Normaliza acentos/Ñ y mayúsculas para que 'Operación Cedula'
                # haga match con el campo calculado desde la cédula
                # (ver _get_operacion_cedula / _apply_cedula_fallbacks).
                df_obj['Operación Cedula'] = (
                    df_obj['Operación Cedula'].astype(str).str.strip().map(normalize_text).str.upper()
                )
                df_obj['Gerencia'] = (
                    df_obj['Gerencia'].astype(str).str.strip().map(normalize_text).str.upper()
                )
                data['objectives'] = df_obj
                self.log(f"Objetivos: {len(df_obj)} registros", LogLevel.DEBUG, "OK")
            else:
                data['objectives'] = None
                self.log("Sin objetivos", LogLevel.DEBUG, "SKIP")
            
            return data
            
        except Exception as e:
            self.log(f"Error carga archivos: {e}", LogLevel.ERROR, "ERR")
            return None

    def _load_cedulas_by_source(self, source: str, trips_file: str, cedulas_folder: str,
                                cedulas_sheet_id: str | None, cedulas_tab: str | None
                                ) -> tuple[Optional[pd.DataFrame], pd.DataFrame]:
        """Despacha la carga de cédulas a la fuente apropiada.

        Devuelve (df_cedulas, df_audit). df_audit está vacío salvo cuando source='db'.
        Si source='db' falla y FALLBACK_ON_DB_ERROR=true, intenta el path Excel.
        """
        if source == "sheets":
            self.log("Fuente cédulas: Google Sheets", code="SRC")
            sheet_id = cedulas_sheet_id or Config.CEDULA_SHEET_ID
            df = self.load_cedula_from_sheets(sheet_id, cedulas_tab)
            if df is None:
                return None, pd.DataFrame()

            # Acotar al rango real del zmov (primer a ultimo viaje): el respaldo
            # local nunca debe generar "Completa" para dias sin viajes todavia.
            # Si el sheet no llega hasta fecha_max, fill_missing_dates extiende
            # el ultimo snapshot conocido (la "foto" no cambia desde el corte).
            try:
                fecha_min, fecha_max = derive_date_range(trips_file)
                mask = (
                    (df['Fecha Cedula_dt'].dt.date >= fecha_min)
                    & (df['Fecha Cedula_dt'].dt.date <= fecha_max)
                )
                df = df[mask].copy()
                if df.empty:
                    self.log("Cédula Sheets sin datos en el rango de viajes", LogLevel.ERROR, "ERR")
                    return None, pd.DataFrame()
                df = excel_io.fill_missing_dates(df)
                self.log(f"Cédula Sheets acotada al rango de viajes: {fecha_min} a {fecha_max}", code="RNG")
            except DateRangeError as e:
                self.log(f"No se pudo acotar cédula Sheets al rango de viajes: {e}", LogLevel.ERROR, "WARN")

            if cedulas_folder:
                excel_io.save_cedula_as_completa(df, cedulas_folder, self.log)
                df_local = excel_io.load_local_cedulas_for_crossfill(cedulas_folder, self.log)
                if not df_local.empty:
                    df, crossfill_log = excel_io.crossfill_cedulas(df, df_local, self.log)
                    for unidad, fecha, campo in crossfill_log:
                        self._registrar_inconsistencia(
                            unidad, fecha, campo, valor_aplicado='(desde cédula local)',
                            motivo='Completado por cruce con cédula local guardada',
                        )
            else:
                self.log(
                    "Sin carpeta de cédulas seleccionada: no se genera respaldo local "
                    "'Completa' ni se completa Operador/No Operador/Estatus Operador/"
                    "Observaciones desde cédulas guardadas previamente",
                    LogLevel.ERROR, "WARN",
                )

            return df, pd.DataFrame()

        if source == "db":
            self.log("Fuente cédulas: PostgreSQL", code="SRC")
            if not Config.db_available():
                self.log(
                    "Falta dependencia 'psycopg2-binary'. Reinstala con: "
                    "pip install -e .[db]   o usa --cedulas-source excel.",
                    LogLevel.ERROR, "ERR",
                )
                return None, pd.DataFrame()
            try:
                from kpi_generator.io.cedulas_db import load_cedulas_from_db
                from kpi_generator.io.postgres import PostgresConnectionError

                fecha_min, fecha_max = derive_date_range(trips_file)
                self.log(f"Rango derivado de viajes: {fecha_min} a {fecha_max}", code="RNG")
                df, df_audit = load_cedulas_from_db(fecha_min, fecha_max, log_func=self.log)
                if df.empty:
                    self.log("BD devolvió 0 cédulas para el rango", LogLevel.ERROR, "ERR")
                    return None, pd.DataFrame()
                return df, df_audit
            except PostgresConnectionError as e:
                if Config.FALLBACK_ON_DB_ERROR and cedulas_folder:
                    self.log(f"BD inaccesible ({e}); fallback a Excel: {cedulas_folder}",
                             LogLevel.ERROR, "WARN")
                    df = self.load_daily_cedulas(cedulas_folder)
                    return df, pd.DataFrame()
                self.log(f"BD inaccesible y sin fallback: {e}", LogLevel.ERROR, "ERR")
                return None, pd.DataFrame()
            except Exception as e:
                self.log(f"Error cargando cédulas desde BD: {e}", LogLevel.ERROR, "ERR")
                return None, pd.DataFrame()

        # source == "excel" (default)
        self.log("Fuente cédulas: Excel local", code="SRC")
        df = self.load_daily_cedulas(cedulas_folder)
        return df, pd.DataFrame()

    def _apply_cedula_fallbacks(self, df_cedulas: pd.DataFrame, df_trips: pd.DataFrame) -> pd.DataFrame:
        """Normaliza texto y completa columnas categóricas faltantes de la cédula.

        1. Quita acentos/Ñ y espacios de Gerencia/Operación/Tipo de Unidad/
           Circuito/Operando + columnas `units_extra` presentes — necesario
           porque "Operación Cedula" (calculado a partir de estas columnas
           en `_get_operacion_cedula`) se usa para emparejar contra
           `Operación Cedula` del archivo de objetivos.
        2. Rellena Gerencia/Operación/Circuito faltantes con
           `Config.CEDULA_FIELD_DEFAULTS`.
        3. Rellena Tipo de Unidad faltante desde el histórico de viajes
           (última `ClaveCategoria` vía `CLAVE_CATEGORIA_A_TIPO_UNIDAD`) o,
           si la unidad no tiene viajes, desde el prefijo del número
           económico (`Config.CEDULA_TIPO_UNIDAD_POR_PREFIJO`).
        4. Si la cédula trae alguna columna de `Config.COLUMNS["units_extra"]`
           (Operador, No Operador, Estatus Operador, Observaciones), asegura
           las 4 y aplica ffill/bfill por Unidades; lo que siga vacío cae a
           "Sin Info". Si NINGUNA está presente (fuente db/excel clásico),
           se omite este paso por completo.

        Cada ajuste se registra vía `_registrar_inconsistencia`.
        """
        if df_cedulas.empty:
            return df_cedulas

        df = df_cedulas.copy()

        # Columnas categoricas/texto que esta funcion puede leer o escribir.
        # Forzar dtype 'object' evita que una columna 100% NaN quede como
        # float64 y pandas reviente (TypeError) al asignarle un string mas
        # adelante (defaults, "Sin Info", Tipo de Unidad inferido, etc.).
        text_cols = ['Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Operando'] \
            + Config.COLUMNS["units_extra"]
        for col in text_cols:
            if col in df.columns and df[col].dtype != object:
                df[col] = df[col].astype(object)

        # --- 1. Normalizar texto (acentos, Ñ, espacios) ---
        extra_cols = [c for c in Config.COLUMNS["units_extra"] if c in df.columns]
        for col in ['Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Operando'] + extra_cols:
            if col not in df.columns:
                continue
            normalized = df[col].astype(str).str.strip().map(normalize_text)
            normalized = normalized.replace({'nan': '', 'None': ''})
            if col == 'Operando':
                # Sin default propio: una cadena vacia se preserva tal cual
                # (categoria_status la trata como 'Otros Status').
                df[col] = normalized.astype(object)
            else:
                # `.astype(object)` evita que una columna 100% NaN quede en
                # float64 (ver comentario sobre text_cols arriba).
                df[col] = normalized.replace('', np.nan).astype(object)

        # --- 2. Defaults para Gerencia/Operación/Circuito ---
        for campo, default in Config.CEDULA_FIELD_DEFAULTS.items():
            if campo not in df.columns:
                df[campo] = pd.Series(np.nan, index=df.index, dtype=object)
            mask = df[campo].isna()
            for idx in df.index[mask]:
                self._registrar_inconsistencia(
                    df.at[idx, 'Unidades'], df.at[idx, 'Fecha Cedula_dt'], campo,
                    valor_aplicado=default, motivo='Faltante en cédula',
                )
            df.loc[mask, campo] = default

        # --- 3. Tipo de Unidad faltante ---
        if 'Tipo de Unidad' not in df.columns:
            df['Tipo de Unidad'] = pd.Series(np.nan, index=df.index, dtype=object)
        mask_tipo = df['Tipo de Unidad'].isna()
        if mask_tipo.any():
            unit_to_tipo: Dict[str, str] = {}
            if not df_trips.empty and 'ClaveCategoria' in df_trips.columns:
                trips_sorted = df_trips.sort_values('Fecha creación')
                ultimas_claves = trips_sorted.groupby('Equipo Motriz')['ClaveCategoria'].last()
                for unidad, clave in ultimas_claves.items():
                    tipo = CLAVE_CATEGORIA_A_TIPO_UNIDAD.get(str(clave).upper())
                    if tipo:
                        unit_to_tipo[str(unidad).strip().upper()] = tipo

            for idx in df.index[mask_tipo]:
                unidad_key = str(df.at[idx, 'Unidades']).strip().upper()
                fecha = df.at[idx, 'Fecha Cedula_dt']
                if unidad_key in unit_to_tipo:
                    tipo = unit_to_tipo[unidad_key]
                    motivo = 'Tipo de Unidad inferido de histórico de viajes'
                else:
                    prefijo_match = re.match(r'^([A-Z])\d', unidad_key)
                    prefijo = prefijo_match.group(1) if prefijo_match else None
                    tipo = Config.CEDULA_TIPO_UNIDAD_POR_PREFIJO.get(prefijo, 'DESCONOCIDO')
                    motivo = 'Tipo de Unidad inferido de prefijo de número económico'
                df.at[idx, 'Tipo de Unidad'] = tipo
                self._registrar_inconsistencia(
                    df.at[idx, 'Unidades'], fecha, 'Tipo de Unidad',
                    valor_aplicado=tipo, motivo=motivo,
                )

        # --- 4. units_extra: ffill/bfill por Unidades, resto -> "Sin Info" ---
        if extra_cols:
            for col in Config.COLUMNS["units_extra"]:
                if col not in df.columns:
                    df[col] = pd.Series(np.nan, index=df.index, dtype=object)

            df = df.sort_values(['Unidades', 'Fecha Cedula_dt'])
            for col in Config.COLUMNS["units_extra"]:
                before_na = df[col].isna()
                df[col] = df.groupby('Unidades')[col].transform(lambda s: s.ffill().bfill())

                filled_mask = before_na & df[col].notna()
                for idx in df.index[filled_mask]:
                    self._registrar_inconsistencia(
                        df.at[idx, 'Unidades'], df.at[idx, 'Fecha Cedula_dt'], col,
                        valor_aplicado=df.at[idx, col], motivo='Completado por ffill/bfill',
                    )

                remaining = df[col].isna()
                for idx in df.index[remaining]:
                    self._registrar_inconsistencia(
                        df.at[idx, 'Unidades'], df.at[idx, 'Fecha Cedula_dt'], col,
                        valor_aplicado='Sin Info', motivo='Sin información disponible',
                    )
                df.loc[remaining, col] = 'Sin Info'

            df = df.reset_index(drop=True)

        return df

    @lru_cache(maxsize=256)
    def _get_operacion_cedula(self, operacion: str, circuito: str, tipo_unidad: str) -> str:
        """Generar cédula de operación según reglas de negocio (cached)."""
        circuito_upper = circuito.upper()
        operacion_upper = operacion.upper()
        tipo_unidad_upper = tipo_unidad.upper()
        
        if circuito_upper in Config.SPECIAL_CIRCUITS:
            return f"{operacion_upper} {tipo_unidad_upper}"
        return f"{operacion_upper} {circuito_upper}"
    
    def create_unit_mapping(self, df_cedulas: pd.DataFrame, analysis_date: datetime) -> Dict:
        """Crear mapeo maestro de unidades vehiculares optimizado, incluyendo unidades sin cédula."""
        cedula_day = df_cedulas[df_cedulas['Fecha Cedula_dt'].dt.date == analysis_date.date()]
        
        if cedula_day.empty:
            previous_cedulas = df_cedulas[df_cedulas['Fecha Cedula_dt'] < analysis_date]
            if not previous_cedulas.empty:
                latest_date = previous_cedulas['Fecha Cedula_dt'].max()
                cedula_day = df_cedulas[df_cedulas['Fecha Cedula_dt'] == latest_date]
            else:
                earliest_date = df_cedulas['Fecha Cedula_dt'].min()
                cedula_day = df_cedulas[df_cedulas['Fecha Cedula_dt'] == earliest_date]
        
        mapping = {}
        for _, row in cedula_day.iterrows():
            unit_id = str(row['Unidades'])
            mapping[unit_id] = {
                'Gerencia': row['Gerencia'],
                'Operación': row['Operación'],
                'Tipo de Unidad': row['Tipo de Unidad'],
                'Circuito': row['Circuito'],
                'Estatus': row.get('Operando', 'Desconocido'),
                'Operación cedula': self._get_operacion_cedula(
                    row['Operación'], row['Circuito'], row['Tipo de Unidad']
                ),
                'Fecha Inicio': row.get('Fecha Cedula', ''),
                'Fecha Fin': row.get('Fecha Cedula', ''),
                'Días Operando': 1 if row.get('Operando', '') == 'Operando' else 0,
                'Días Disponible': 0,
                'Días Gestoría': 0,
                'Días Taller': 0,
                'En Cedula': True  # Marca que está en cédula
            }
        
        self.log(f"Mapeo: {len(mapping)} unidades en cédula", code="MAP")
        return mapping
    
    def add_phantom_units_from_trips(self, df_trips: pd.DataFrame, unit_mapping: Dict) -> Dict:
        """Agregar unidades sin cédula detectadas en viajes usando ClaveCategoria."""
        # Detectar unidades en viajes que no están en cédula
        units_in_trips = set(df_trips['Equipo Motriz'].dropna().astype(str).unique())
        units_in_cedula = set(unit_mapping.keys())
        phantom_units = units_in_trips - units_in_cedula
        
        if not phantom_units:
            self.log("Sin unidades fantasma detectadas", code="PHANTOM")
            return unit_mapping
        
        phantom_count = 0
        for unit_id in phantom_units:
            # Buscar información de esta unidad en viajes
            unit_trips = df_trips[df_trips['Equipo Motriz'].astype(str) == unit_id]
            if unit_trips.empty:
                continue
            
            # Obtener ClaveCategoria (usar el más común si hay varios)
            clave_categoria = unit_trips['ClaveCategoria'].mode()
            if len(clave_categoria) == 0:
                clave_categoria = 'SENCILLO'  # Default
            else:
                clave_categoria = str(clave_categoria.iloc[0]).upper()
            
            # Determinar Tipo de Unidad desde ClaveCategoria
            tipo_unidad = CLAVE_CATEGORIA_A_TIPO_UNIDAD.get(clave_categoria, f'TRACTOCAMION {clave_categoria}')
            
            # Crear operación cédula: POR ASIGNAR + Tipo
            # Como el circuito es "POR ASIGNAR" (circuito especial), usa el tipo de unidad
            operacion_cedula = f"POR ASIGNAR {tipo_unidad}"
            
            # Agregar al mapeo con valores predeterminados
            unit_mapping[unit_id] = {
                'Gerencia': 'PENDIENTE',
                'Operación': 'POR ASIGNAR',
                'Tipo de Unidad': tipo_unidad,
                'Circuito': 'POR ASIGNAR',
                'Estatus': 'SIN ASIGNACIÓN',
                'Operación cedula': operacion_cedula,
                'Fecha Inicio': '',
                'Fecha Fin': '',
                'Días Operando': 0,
                'Días Disponible': 0,
                'Días Gestoría': 0,
                'Días Taller': 0,
                'En Cedula': False,  # Marca que NO está en cédula
                'ClaveCategoria': clave_categoria  # Guardar para referencia
            }
            phantom_count += 1
        
        self.log(f"Unidades fantasma: {phantom_count} sin cédula (clasificadas por ClaveCategoria)", code="PHANTOM")
        return unit_mapping
    
    @lru_cache(maxsize=64)
    def _get_daily_objective(self, operation: str, obj_km: float, obj_viajes: float, days_in_month: int) -> Tuple[float, float]:
        """Calcular objetivos diarios (cached)."""
        return obj_km / days_in_month, obj_viajes / days_in_month
    
    def process_objectives(self, df_objectives: pd.DataFrame, unit_mapping: Dict, analysis_date: datetime) -> Dict:
        """Procesar objetivos optimizado."""
        if df_objectives is None:
            operations = set(info['Operación cedula'] for info in unit_mapping.values())
            obj_mapping = {op: {'Objetivo KM': 0, 'Objetivo Viajes': 0} for op in operations}
        else:
            obj_mapping = df_objectives.set_index('Operación Cedula')[['Objetivo KM', 'Objetivo Viajes']].to_dict('index')
            
            operations = set(info['Operación cedula'] for info in unit_mapping.values())
            for op in operations:
                if op not in obj_mapping:
                    obj_mapping[op] = {'Objetivo KM': 0, 'Objetivo Viajes': 0}
        
        days_in_month = calendar.monthrange(analysis_date.year, analysis_date.month)[1]
        
        for op in obj_mapping:
            daily_km, daily_viajes = self._get_daily_objective(
                op, obj_mapping[op]['Objetivo KM'], obj_mapping[op]['Objetivo Viajes'], days_in_month
            )
            obj_mapping[op]['Objetivo KM Diario'] = daily_km
            obj_mapping[op]['Objetivo Viajes Diario'] = daily_viajes
            obj_mapping[op]['Días en el mes'] = days_in_month
        
        self.log(f"Objetivos: {len(obj_mapping)} operaciones", code="OBJ")
        return obj_mapping
    
    def _calculate_trips_efficient(self, df: pd.DataFrame) -> pd.Series:
        """Aplicar lógica de conteo de viajes optimizada."""
        viajes = pd.Series(1, index=df.index)
        
        viajes[df['StatusViaje'] == 'X'] = 0
        
        cuernavaca_mask = df['Operación cedula'].str.contains('CUERNAVACA FULL', case=False, na=False)
        cuernavaca_zero_km = cuernavaca_mask & (df['KM_cargado'] <= 0)
        viajes[cuernavaca_zero_km] = 0
        
        return viajes
    
    def process_trips_optimized(self, df_trips: pd.DataFrame, df_cedulas: pd.DataFrame, 
                               df_fuel: pd.DataFrame, obj_mapping: Dict = None) -> pd.DataFrame:
        """Procesar viajes con optimizaciones y comodatos."""
        self.log("Procesando viajes", code="PROC")
        
        df = df_trips.copy()
        
        df['Fecha creación'] = pd.to_datetime(df['Fecha creación'], errors='coerce')
        df['Fecha creación_date'] = df['Fecha creación'].dt.date
        
        mask_valid = df['StatusViaje'] != 'A'
        df['KM_cargado'] = np.where(mask_valid, df['KMLiqCargadoFinal'], df['Distancia'])
        df['KM_vacio'] = df['KMLiqVacioFinal'].fillna(0)
        df['KM_total'] = df['KM_cargado'] + df['KM_vacio']
        
        fuel_summary = df_fuel[df_fuel['StatusVale'] == 'D'].groupby('Número de Viaje')['Cantidad Litros Real'].sum()
        df['Diesel_LTS'] = df['Número de Viaje'].map(fuel_summary).fillna(0)
        
        df['Rendimiento'] = np.where(df['Diesel_LTS'] > 0, df['KM_total'] / df['Diesel_LTS'], 0)
        
        # Crear mapeo base de unidades desde cédulas
        unit_mapping = self.create_unit_mapping(df_cedulas, df['Fecha creación'].max())
        
        # NUEVO: Agregar unidades fantasma (sin cédula) detectadas en viajes
        unit_mapping = self.add_phantom_units_from_trips(df, unit_mapping)
        
        df = self._assign_cedula_info_optimized(df, df_cedulas, unit_mapping)
        
        # Comodatos solo para unidades EN cédula
        comodatos = self.comodato_manager.create_comodatos(df, df_cedulas, unit_mapping, self.log)
        df = self.comodato_manager.integrate_comodatos(df, comodatos)
        
        df['Viajes_count'] = self._calculate_trips_efficient(df)
        
        if obj_mapping is not None:
            df = self._distribute_objectives_optimized(df, obj_mapping)
        
        self.log(f"Viajes procesados: {len(df)} registros", code="OK")
        return df
    
    def _assign_cedula_info_optimized(self, df_trips: pd.DataFrame, df_cedulas: pd.DataFrame, unit_mapping: Dict) -> pd.DataFrame:
        """Asignar información de cédula optimizado, manejando unidades fantasma."""
        cedula_lookup = df_cedulas.copy()
        cedula_lookup['Unidades'] = cedula_lookup['Unidades'].astype(str)
        cedula_lookup['Fecha Cedula_dt_date'] = cedula_lookup['Fecha Cedula_dt'].dt.date

        # Restringir a las columnas necesarias para el merge: la cédula desde
        # Sheets puede traer columnas-metadato extra (ej. "Denominación") que
        # colisionan con columnas de df_trips y rompen cols_to_keep más abajo
        # (pandas las renombra a _x/_y al hacer merge).
        cedula_lookup = cedula_lookup[Config.COLUMNS["units"] + ['Fecha Cedula_dt_date']]

        df_trips['Equipo Motriz'] = df_trips['Equipo Motriz'].astype(str)
        
        # Merge con cédulas
        merged = pd.merge(
            df_trips, 
            cedula_lookup,
            left_on=['Equipo Motriz', 'Fecha creación_date'],
            right_on=['Unidades', 'Fecha Cedula_dt_date'],
            how='left'
        )
        
        # Para unidades sin match en cédulas, usar unit_mapping (unidades fantasma)
        mask_sin_cedula = merged['Operación'].isna()

        if mask_sin_cedula.any():
            phantom_units = merged.loc[mask_sin_cedula, 'Equipo Motriz']
            for col_dest, info_key in [
                ('Gerencia', 'Gerencia'), ('Operación', 'Operación'),
                ('Tipo de Unidad', 'Tipo de Unidad'), ('Circuito', 'Circuito'), ('Operando', 'Estatus')
            ]:
                col_map = {uid: unit_mapping[uid][info_key] for uid in phantom_units.unique() if uid in unit_mapping}
                merged.loc[mask_sin_cedula, col_dest] = phantom_units.map(col_map).values
        
        # Calcular Operación cedula
        merged['Operación cedula'] = merged.apply(
            lambda row: self._get_operacion_cedula(
                str(row.get('Operación', '')), 
                str(row.get('Circuito', '')), 
                str(row.get('Tipo de Unidad', ''))
            ) if pd.notna(row.get('Operación')) else 'Sin Asignar',
            axis=1
        )
        
        # Limpiar merge artifacts
        cols_to_keep = [col for col in df_trips.columns] + ['Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Operando', 'Operación cedula']
        merged = merged[cols_to_keep]
        
        return merged
    
    def _distribute_objectives_optimized(self, df: pd.DataFrame, obj_mapping: Dict) -> pd.DataFrame:
        """Distribuir objetivos optimizado incluyendo comodatos."""
        self.log("Asignando objetivos", code="OBJ")
        
        # v0.5.0: Objetivo KM Total por fila = objetivo proyectado al CIERRE del mes.
        # Es simetrico a Tendencia KM Total y permite calcular cumplimiento de cierre
        # con SUM directo en Looker. Compuesto por:
        #   Objetivo KM Viaje    = obj_diario_OpCedula_dia / viajes_del_dia
        #   Complemento KM Obj   = obj_diario_OpCedula_vigente × dias_restantes / viajes_OpCedula_vigente
        #   Objetivo KM Total    = Viaje + Complemento (suma fila por fila)
        objective_cols = ['Objetivo KM Viaje', 'Objetivo Viajes Viaje', 'Complemento KM Objetivo',
                         'Complemento Viajes Objetivo', 'Objetivo KM Total', 'Objetivo Viajes Total']
        for col in objective_cols:
            df[col] = 0.0

        max_date = df['Fecha creación'].max()
        days_in_month = calendar.monthrange(max_date.year, max_date.month)[1]
        remaining_days = days_in_month - max_date.day

        for unidad, unit_trips in df.groupby('Equipo Motriz'):
            if unit_trips.empty:
                continue

            self._assign_daily_objectives_optimized(unit_trips, obj_mapping, df)

            if remaining_days > 0:
                self._assign_future_complement_optimized(unit_trips, obj_mapping, remaining_days, df)

            indices = unit_trips.index
            df.loc[indices, 'Objetivo KM Total'] = (
                df.loc[indices, 'Objetivo KM Viaje'] + df.loc[indices, 'Complemento KM Objetivo']
            )
            df.loc[indices, 'Objetivo Viajes Total'] = (
                df.loc[indices, 'Objetivo Viajes Viaje'] + df.loc[indices, 'Complemento Viajes Objetivo']
            )

        return df
    
    def _assign_daily_objectives_optimized(self, unit_trips: pd.DataFrame, obj_mapping: Dict, main_df: pd.DataFrame):
        """Asignar objetivos diarios optimizado incluyendo comodatos."""
        for date, day_trips in unit_trips.groupby('Fecha creación_date'):
            operation_cedula = day_trips.iloc[0]['Operación cedula']
            
            if operation_cedula not in obj_mapping or operation_cedula == 'Sin Asignar':
                continue
            
            obj_km_daily = obj_mapping[operation_cedula]['Objetivo KM Diario']
            obj_viajes_daily = obj_mapping[operation_cedula]['Objetivo Viajes Diario']
            
            trips_count = len(day_trips)
            km_per_trip = obj_km_daily / trips_count
            viajes_per_trip = obj_viajes_daily / trips_count
            
            main_df.loc[day_trips.index, 'Objetivo KM Viaje'] = km_per_trip
            main_df.loc[day_trips.index, 'Objetivo Viajes Viaje'] = viajes_per_trip
    
    def _assign_future_complement_optimized(self, unit_trips: pd.DataFrame, obj_mapping: Dict, 
                                          remaining_days: int, main_df: pd.DataFrame):
        """Calcular complemento futuro optimizado."""
        last_operation = unit_trips.iloc[-1]['Operación cedula']
        
        if last_operation not in obj_mapping or last_operation == 'Sin Asignar':
            return
        
        obj_km_daily = obj_mapping[last_operation]['Objetivo KM Diario']
        obj_viajes_daily = obj_mapping[last_operation]['Objetivo Viajes Diario']
        
        complement_km_total = obj_km_daily * remaining_days
        complement_viajes_total = obj_viajes_daily * remaining_days
        
        last_operation_trips = unit_trips[unit_trips['Operación cedula'] == last_operation]
        trips_count = len(last_operation_trips)
        
        if trips_count > 0:
            complement_km_per_trip = complement_km_total / trips_count
            complement_viajes_per_trip = complement_viajes_total / trips_count
            
            main_df.loc[last_operation_trips.index, 'Complemento KM Objetivo'] = complement_km_per_trip
            main_df.loc[last_operation_trips.index, 'Complemento Viajes Objetivo'] = complement_viajes_per_trip
    
    def _add_trip_extra_columns(self, df: pd.DataFrame, df_cedulas: pd.DataFrame) -> pd.DataFrame:
        """A: Agregar columnas calculadas a Trip Data que antes se computaban en Sheets/Looker."""
        df = df.copy()

        # Eq x dia x op — unidades distintas con viajes por fecha y OpCedula
        if 'Fecha creación_date' in df.columns and 'Operación cedula' in df.columns:
            df['Eq x dia x op'] = df.groupby(
                ['Fecha creación_date', 'Operación cedula'])['Equipo Motriz'].transform('nunique')
        else:
            df['Eq x dia x op'] = 1

        # Promedio KM x Unidad dia
        df['Promedio KM x Unidad dia'] = (
            df['KM_total'] / df['Eq x dia x op'].replace(0, np.nan)
        ).fillna(0).round(2)

        # CedulaActual — última asignación conocida de cada unidad
        last_cedula = (
            df_cedulas.sort_values('Fecha Cedula_dt')
            .groupby('Unidades').last().reset_index()
        )
        last_cedula['CedulaActual'] = last_cedula.apply(
            lambda r: self._get_operacion_cedula(r['Operación'], r['Circuito'], r['Tipo de Unidad']), axis=1
        )
        cedula_map = last_cedula.set_index('Unidades')['CedulaActual'].to_dict()
        df['CedulaActual'] = df['Equipo Motriz'].map(cedula_map).fillna(df.get('Operación cedula', ''))

        # Cuenta remolques — remolques únicos por OpCedula, prorrateado entre viajes
        # con remolque registrado para que SUM(Cuenta remolques) en Looker == # único.
        # Reemplaza el algoritmo viejo (1 o 2 por viaje) que inflaba la suma y duplicaba
        # cuando un mismo remolque aparecía en R1 y R2 del mismo viaje.
        df['Cuenta remolques'] = self._contar_remolques_unicos_prorrateado(df)

        # llaveremolque — clave compuesta de remolques para deduplicación
        r1 = df['Equipo Remolque 1'].fillna('').astype(str).str.strip()
        r2 = df['Equipo Remolque 2'].fillna('').astype(str).str.strip()
        df['llaveremolque'] = r1 + r2

        # cuenta llaverem — remolques únicos por fecha y OpCedula
        df['cuenta llaverem'] = df.groupby(
            ['Fecha creación_date', 'Operación cedula'])['llaveremolque'].transform('nunique')

        # EqAsignados — unidades en cédula para esa fecha y OpCedula (planificado vs real)
        df_ced = df_cedulas.copy()
        df_ced['Operación cedula_c'] = df_ced.apply(
            lambda r: self._get_operacion_cedula(r['Operación'], r['Circuito'], r['Tipo de Unidad']), axis=1
        )
        df_ced['Fecha_c'] = df_ced['Fecha Cedula_dt'].dt.date
        eq_asig = (
            df_ced.groupby(['Fecha_c', 'Operación cedula_c'])['Unidades']
            .nunique().reset_index()
            .rename(columns={'Unidades': 'EqAsignados', 'Fecha_c': 'Fecha_join', 'Operación cedula_c': 'Op_join'})
        )
        fecha_col = 'Fecha creación_date' if 'Fecha creación_date' in df.columns else None
        if fecha_col:
            df['_fecha_j'] = df[fecha_col]
            df['_op_j'] = df['Operación cedula']
            df = df.merge(eq_asig, left_on=['_fecha_j', '_op_j'],
                          right_on=['Fecha_join', 'Op_join'], how='left')
            df['EqAsignados'] = df['EqAsignados'].fillna(0).astype(int)
            df.drop(columns=['_fecha_j', '_op_j', 'Fecha_join', 'Op_join'], inplace=True, errors='ignore')
        else:
            df['EqAsignados'] = 0

        # Cuentaeqasig — unidades distintas con viajes en todo el día (global)
        df['Cuentaeqasig'] = df.groupby('Fecha creación_date')['Equipo Motriz'].transform('nunique')

        # ── Última foto de cédula ─────────────────────────────────────────────
        # Campos "Foto": atributos de la asignación ACTUAL de cada unidad
        # (último archivo de cédula), denormalizados a todas sus filas.
        # Permiten multifiltro por asignación vigente sobre datos históricos.
        last_cedula_date  = df_cedulas['Fecha Cedula_dt'].max()
        last_cedula_snap  = df_cedulas[df_cedulas['Fecha Cedula_dt'] == last_cedula_date].copy()
        last_cedula_snap['OpCedula Foto'] = last_cedula_snap.apply(
            lambda r: self._get_operacion_cedula(r['Operación'], r['Circuito'], r['Tipo de Unidad']), axis=1
        )
        # Deduplicar por unidad (por si hay duplicados en la misma fecha)
        last_snap_dedup = last_cedula_snap.drop_duplicates(subset='Unidades', keep='last')
        snap_idx = last_snap_dedup.set_index('Unidades')
        last_cedula_units = set(snap_idx.index.astype(str))

        foto_campos = {
            'Gerencia':       'Gerencia Foto',
            'Operación':      'Operación Foto',
            'Tipo de Unidad': 'Tipo Unidad Foto',
            'Circuito':       'Circuito Foto',
            'Operando':       'Operando Foto',
            'OpCedula Foto':  'OpCedula Foto',
        }
        for src, dst in foto_campos.items():
            col_map = snap_idx[src].to_dict() if src in snap_idx.columns else {}
            df[dst] = df['Equipo Motriz'].map(
                {str(k): v for k, v in col_map.items()}
            ).fillna('')

        # Eq en Cédula — binario: 1 solo en la última fila de la unidad
        # si esa unidad existe en la última foto.  SUM = equipos asignados hoy.
        last_date_per_unit = df.groupby('Equipo Motriz')['Fecha creación_date'].transform('max')
        is_last_date = df['Fecha creación_date'] == last_date_per_unit
        # Entre filas del mismo día, marcar solo una (la última por posición)
        last_row_idx = df[is_last_date].groupby('Equipo Motriz').tail(1).index
        df['Eq en Cédula'] = 0
        df.loc[
            df.index.isin(last_row_idx) &
            df['Equipo Motriz'].astype(str).isin(last_cedula_units),
            'Eq en Cédula'
        ] = 1

        self.log(
            f"Foto cédula: {len(last_cedula_units)} unidades. "
            f"Eq en Cédula=1: {df['Eq en Cédula'].sum()} filas",
            code="FOTO"
        )
        return df

    @staticmethod
    def _contar_remolques_unicos_prorrateado(df: pd.DataFrame) -> pd.Series:
        """Cuenta remolques únicos por Operación Cedula y prorratea entre los viajes
        con remolque registrado.

        Lógica:
          1. Aislar (Operación Cedula, Equipo Remolque 1) y (Operación Cedula, Equipo Remolque 2)
             como dos DataFrames con columna unificada `Remolque`.
          2. Concatenar y descartar filas con Remolque vacío.
          3. drop_duplicates por (Operación Cedula, Remolque) — el mismo remolque
             en R1 y R2 del mismo viaje cuenta una sola vez.
          4. groupby(Operación Cedula).size() → total único por OpCédula.
          5. Calcular viajes con remolque por OpCédula (denominador del prorrateo).
          6. Devolver Serie alineada al df original con:
               - 0  si el viaje no tiene remolque registrado (comodatos, viajes sin remolque)
               - total_unicos / viajes_con_remolque  si tiene al menos un remolque

        Garantía: SUM(Cuenta remolques) filtrado por Operación Cedula == número de
        remolques únicos usados en esa OpCédula durante todo el período.
        """
        op_col = 'Operación cedula'
        if op_col not in df.columns:
            return pd.Series(0.0, index=df.index, dtype='float64')

        def _norm(s: pd.Series) -> pd.Series:
            return s.fillna('').astype(str).str.strip()

        r1 = _norm(df['Equipo Remolque 1'])
        r2 = _norm(df['Equipo Remolque 2'])
        opc = _norm(df[op_col])

        # Máscara de viajes con AL MENOS un remolque registrado (denominador del prorrateo)
        tiene_remolque = (r1 != '') | (r2 != '')
        viajes_con_remolque_por_opc = (
            opc.where(tiene_remolque).dropna().value_counts()
        )

        # Construir tabla larga (OpCedula, Remolque) y deduplicar
        long_r1 = pd.DataFrame({'opc': opc, 'rem': r1})
        long_r2 = pd.DataFrame({'opc': opc, 'rem': r2})
        long = pd.concat([long_r1, long_r2], ignore_index=True)
        long = long[(long['rem'] != '') & (long['opc'] != '')]
        unicos_por_opc = (
            long.drop_duplicates(subset=['opc', 'rem'])
                .groupby('opc').size()
        )

        # Prorrateo: cada viaje-con-remolque recibe (unicos / n_viajes_con_remolque)
        prorrateo = (unicos_por_opc / viajes_con_remolque_por_opc).fillna(0)
        result = opc.map(prorrateo).fillna(0).astype('float64')
        # Filas sin remolque reciben 0 (no participan en la suma)
        result = result.where(tiene_remolque, 0.0)
        return result.round(6)

    def upload_to_sheets(self, df_resumen: pd.DataFrame, df_kpi: pd.DataFrame,
                         df_processed: pd.DataFrame, df_changes: pd.DataFrame,
                         df_opcedula: pd.DataFrame, df_objectives: pd.DataFrame,
                         df_promedio: pd.DataFrame,
                         df_inconsistencias: pd.DataFrame = None) -> bool:
        """Delegado a `io.sheets.sync_workbook_to_sheets` (refactor v0.4.3).

        Arma el dict {tab_name: df} respetando el orden canonico de `SHEETS_TAB_NAMES`
        y delega la subida a la capa de I/O.
        """
        dfs = {
            SHEETS_TAB_NAMES['resumen']: df_resumen,
            SHEETS_TAB_NAMES['por_equipo']: df_kpi,
            SHEETS_TAB_NAMES['trip_data']: df_processed,
            SHEETS_TAB_NAMES['cambios']: df_changes,
            SHEETS_TAB_NAMES['por_operacion']: df_opcedula,
            SHEETS_TAB_NAMES['objetivos']: df_objectives,
            SHEETS_TAB_NAMES['promedio']: df_promedio,
            SHEETS_TAB_NAMES['inconsistencias']: df_inconsistencias,
        }
        return sheets_io.sync_workbook_to_sheets(Config.SHEETS_ID, dfs, self.log)

    def _drop_deadweight(self, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        """Elimina columnas deadweight (intermediarios o constantes) sin fallar si no existen."""
        if df is None or df.empty:
            return df
        to_drop = [c for c in cols if c in df.columns]
        if to_drop:
            return df.drop(columns=to_drop)
        return df

    def _build_resumen_ejecutivo(self, df_opcedula: pd.DataFrame) -> pd.DataFrame:
        """Construye la hoja Resumen agregando df_opcedula por Gerencia + TOTAL.

        Schema v0.5.0: una fila por gerencia con conteos de unidades por status,
        Dias unidad activos/asignados, KM/Viajes/Diesel, cumplimientos ponderados
        y % Operativo. Ultima fila = TOTAL TUMSA.
        """
        if df_opcedula is None or df_opcedula.empty:
            self.log("Sin datos de OpCedula para Resumen", code="RESUMEN")
            return pd.DataFrame()

        df = df_opcedula.copy()

        # Mapa src_col -> out_col. Sumamos todas las columnas que se acumulan
        # de forma trivial; cumplimientos y rendimiento se recalculan al final.
        num_cols_sum = {
            'Motrices Titulares': 'Unidades Activas',
            'Operando': 'Operando',
            'Disponible': 'Disponible',
            'Sin Operador': 'Sin Operador',
            'Taller': 'Taller',
            'Gestoria': 'Gestoria',
            'Descanso': 'Descanso',
            'Rescate': 'Rescate',
            'Puesto A Punto': 'Puesto A Punto',
            'Otros Status': 'Otros Status',
            'Dias unidad asignados': 'Dias unidad asignados',
            'Dias unidad activos': 'Dias unidad activos',
            'KM Total': 'KM Total',
            'Viajes': 'Viajes',
            'Diesel LTS': 'Diesel LTS',
            'Objetivo KM': 'Objetivo KM',
            'Objetivo Viajes': 'Objetivo Viajes',
        }
        for col in num_cols_sum:
            if col not in df.columns:
                df[col] = 0

        grouped = df.groupby('Gerencia', dropna=False).agg(
            **{out: (src, 'sum') for src, out in num_cols_sum.items()}
        ).reset_index()

        # Metricas derivadas (recalculadas, no promediadas)
        grouped['Rendimiento'] = (
            grouped['KM Total'] / grouped['Diesel LTS']
        ).where(grouped['Diesel LTS'] > 0, 0).round(2)
        grouped['Cumplimiento KM %'] = (
            grouped['KM Total'] / grouped['Objetivo KM'] * 100
        ).where(grouped['Objetivo KM'] > 0, 0).round(1)
        grouped['Cumplimiento Viajes %'] = (
            grouped['Viajes'] / grouped['Objetivo Viajes'] * 100
        ).where(grouped['Objetivo Viajes'] > 0, 0).round(1)
        grouped['% Operativo'] = (
            grouped['Dias unidad activos'] / grouped['Dias unidad asignados'] * 100
        ).where(grouped['Dias unidad asignados'] > 0, 0).round(1)

        cols_out = [
            'Gerencia', 'Unidades Activas',
            'Operando', 'Disponible', 'Sin Operador', 'Taller', 'Gestoria',
            'Descanso', 'Rescate', 'Puesto A Punto', 'Otros Status',
            'Dias unidad asignados', 'Dias unidad activos', '% Operativo',
            'KM Total', 'Viajes', 'Diesel LTS', 'Rendimiento',
            'Objetivo KM', 'Objetivo Viajes',
            'Cumplimiento KM %', 'Cumplimiento Viajes %',
        ]
        grouped = grouped[cols_out]

        total = grouped.drop(columns='Gerencia').sum(numeric_only=True)
        total['Rendimiento'] = round(total['KM Total'] / total['Diesel LTS'], 2) \
            if total['Diesel LTS'] > 0 else 0
        total['Cumplimiento KM %'] = round(total['KM Total'] / total['Objetivo KM'] * 100, 1) \
            if total['Objetivo KM'] > 0 else 0
        total['Cumplimiento Viajes %'] = round(total['Viajes'] / total['Objetivo Viajes'] * 100, 1) \
            if total['Objetivo Viajes'] > 0 else 0
        total['% Operativo'] = round(total['Dias unidad activos'] / total['Dias unidad asignados'] * 100, 1) \
            if total['Dias unidad asignados'] > 0 else 0
        total_row = pd.DataFrame([{'Gerencia': 'TOTAL TUMSA', **total.to_dict()}])

        resumen = pd.concat([grouped, total_row], ignore_index=True)
        self.log(f"Resumen ejecutivo: {len(grouped)} gerencias + TOTAL", code="RESUMEN")
        return resumen

    def save_results(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame, df_changes: pd.DataFrame,
                     df_opcedula: pd.DataFrame, output_path: str, df_objectives: pd.DataFrame = None,
                     df_promedio: pd.DataFrame = None, df_cedulas_audit: pd.DataFrame = None,
                     df_inconsistencias: pd.DataFrame = None,
                     upload_sheets: bool = True) -> Optional[str]:
        """Genera el Excel KPI y opcionalmente sincroniza a Google Sheets (v0.4.3).

        Esta capa hace LOGICA de presentacion (drops de deadweight, naming canonico,
        construccion de Resumen ejecutivo). La escritura fisica del archivo y la
        subida a Sheets se delegan a `io.excel.write_workbook` y `upload_to_sheets`.

        Orden de hojas: Resumen → Por Equipo → Viajes → Resumen de Cambios →
        Por Operación → Objetivos → Promedio KM por Unidad → Cedulas Rellenadas →
        Inconsistencias.
        """
        # Drops de deadweight (Tier 1) sin tocar la logica de calculo
        df_processed = self._drop_deadweight(df_processed, TRIP_DEADWEIGHT_COLS)

        # Formato + naming canonico de la hoja Viajes (v0.4.0)
        df_processed_formatted = df_processed.copy()
        if 'Fecha creación' in df_processed_formatted.columns:
            df_processed_formatted['Fecha creación'] = df_processed_formatted['Fecha creación'].dt.strftime("%d/%m/%Y")
        # Internamente el pipeline usa 'Operación cedula' (c minuscula); aqui
        # publicamos 'Operación Cedula' para consistencia con Por Operación.
        if 'Operación cedula' in df_processed_formatted.columns:
            df_processed_formatted = df_processed_formatted.rename(columns={'Operación cedula': 'Operación Cedula'})
        if 'Operación cedula' in df_kpi.columns:
            df_kpi = df_kpi.rename(columns={'Operación cedula': 'Operación Cedula'})

        # Resumen ejecutivo a partir de Por Operación
        df_resumen = self._build_resumen_ejecutivo(df_opcedula)

        # Logs por hoja (mantienen la traza historica del pipeline)
        if df_changes is not None and not df_changes.empty:
            self.log(f"Hoja {SHEET_NAMES['cambios']}: {len(df_changes)} cambios", code="CHG")
        else:
            self.log(f"Hoja {SHEET_NAMES['cambios']}: Sin cambios operacionales", code="CHG")
        if df_opcedula is not None and not df_opcedula.empty:
            self.log(f"Hoja {SHEET_NAMES['por_operacion']}: {len(df_opcedula)} operaciones", code="OPCED")
        else:
            self.log(f"Hoja {SHEET_NAMES['por_operacion']}: Sin operaciones", code="OPCED")
        if df_cedulas_audit is not None and not df_cedulas_audit.empty:
            rellenadas = (df_cedulas_audit['Origen'] == 'forward_fill').sum()
            self.log(f"Hoja {SHEET_NAMES['audit']}: {rellenadas} días por forward-fill "
                     f"de {len(df_cedulas_audit)} totales", code="AUDIT")
        if df_inconsistencias is not None and not df_inconsistencias.empty:
            self.log(f"Hoja {SHEET_NAMES['inconsistencias']}: {len(df_inconsistencias)} "
                     f"ajustes registrados", code="INCONS")

        # Orquestar escritura: dict ordenado -> io.excel
        workbook_sheets = {
            SHEET_NAMES['resumen']: df_resumen,
            SHEET_NAMES['por_equipo']: df_kpi,
            SHEET_NAMES['trip_data']: df_processed_formatted,
            SHEET_NAMES['cambios']: df_changes,
            SHEET_NAMES['por_operacion']: df_opcedula,
            SHEET_NAMES['objetivos']: df_objectives,
            SHEET_NAMES['promedio']: df_promedio,
            SHEET_NAMES['audit']: df_cedulas_audit,
            SHEET_NAMES['inconsistencias']: df_inconsistencias,
        }
        full_path = excel_io.write_workbook(workbook_sheets, output_path, self.log)
        if full_path is None:
            return None

        # Subida automatica a Google Sheets (con Resumen incluido)
        if upload_sheets:
            self.upload_to_sheets(
                df_resumen, df_kpi, df_processed_formatted, df_changes, df_opcedula,
                df_objectives if df_objectives is not None else pd.DataFrame(),
                df_promedio if df_promedio is not None else pd.DataFrame(),
                df_inconsistencias if df_inconsistencias is not None else pd.DataFrame(),
            )

        return str(full_path)
    
    @staticmethod
    def _linear_project(daily_values: np.ndarray, km_actual: float,
                        remaining_days: int, days_elapsed: int) -> float:
        """Proyectar KM/Viajes restantes con tasa diaria constante (tendencia lineal).
        km_actual / dias_transcurridos × dias_restantes."""
        if days_elapsed == 0 or remaining_days <= 0 or km_actual == 0:
            return 0.0
        return (km_actual / days_elapsed) * remaining_days

    def generate_report(self, trips_file: str, fuel_file: str, cedulas_folder: str,
                        output_path: str, objectives_file: str = None,
                        cedulas_source: str = None,
                        upload_sheets: bool = True) -> Optional[str]:
        """Pipeline v0.5.0: aggregators puros sobre PeriodContext.

        Flujo:
            load_data -> PeriodContext.from_trips -> obj_mapping
            -> process_trips_optimized (integra comodatos, asigna OpCedula a viajes)
            -> EquipmentAggregator (Por Equipo: 1 fila por equipo unico)
            -> OpcedulaAggregator (Por Operacion: 1 fila por OpCedula vigente)
            -> post_calcular_tendencia (rellena Tendencia KM/Viajes)
            -> ChangeTracker (Resumen de Cambios)
            -> _build_promedio_km_sheet (Promedio KM por Unidad)
            -> save_results (Excel + Sheets opcional)

        `cedulas_source`: "db" | "excel" | "sheets" | None (usa Config.CEDULAS_SOURCE).
        `upload_sheets`: True (default) sincroniza a Google Sheets; False solo genera Excel.
        """
        try:
            self.log("=== INICIO PROCESO KPI ===", code="START")

            data = self.load_data(trips_file, fuel_file, cedulas_folder, objectives_file,
                                  cedulas_source=cedulas_source)
            if not data:
                return None

            # Contexto temporal: un solo mes derivado de las fechas de viajes
            period = PeriodContext.from_trips(data['trips'])
            self.log(f"Periodo: {period.anio}-{period.mes:02d}, "
                     f"dias_mes={period.dias_mes}, "
                     f"dias_corrientes={period.dias_corrientes}, "
                     f"dias_restantes={period.dias_restantes}",
                     code="PER")

            # analysis_date legacy (la siguen usando create_unit_mapping y process_objectives)
            analysis_date = period.fecha_corte.to_pydatetime()

            unit_mapping = self.create_unit_mapping(data['cedulas'], analysis_date)

            obj_mapping = None
            if data['objectives'] is not None:
                obj_mapping = self.process_objectives(
                    data['objectives'], unit_mapping, analysis_date
                )

            # Procesa viajes (integra comodatos + asigna OpCedula a cada fila)
            df_processed = self.process_trips_optimized(
                data['trips'], data['cedulas'], data['fuel'], obj_mapping
            )
            df_processed = self._add_trip_extra_columns(df_processed, data['cedulas'])

            # --- v0.5.0: aggregators puros ---
            equipment_agg = EquipmentAggregator(
                df_cedulas=data['cedulas'],
                df_trips=df_processed,
                obj_mapping=obj_mapping,
                period=period,
                special_circuits=Config.SPECIAL_CIRCUITS,
                log_callback=self.log_func,
            )
            df_kpi = equipment_agg.aggregate()

            opcedula_agg = OpcedulaAggregator(
                df_equipos=df_kpi, obj_mapping=obj_mapping, period=period,
                log_callback=self.log_func,
            )
            df_opcedula = opcedula_agg.aggregate()

            # Segundo pase: rellena Tendencia KM/Viajes (in-place en ambos)
            post_calcular_tendencia(df_kpi, df_opcedula, period)

            df_changes = self.change_tracker.track_operation_changes(
                data['cedulas'], obj_mapping
            )

            # Denormaliza KPIs de equipo y OpCedula a cada fila de Viajes (fuente Looker)
            df_processed = self._denormalize_kpis_to_trips_v050(
                df_processed, df_kpi, df_opcedula,
            )

            df_promedio = self._build_promedio_km_sheet_v050(df_opcedula, df_kpi)

            df_inconsistencias = pd.DataFrame(
                self._inconsistencias,
                columns=['Unidad', 'Fecha', 'Campo', 'Valor Original', 'Valor Aplicado', 'Motivo'],
            )

            result_path = self.save_results(
                df_kpi, df_processed, df_changes, df_opcedula, output_path,
                df_objectives=data['objectives'],
                df_promedio=df_promedio,
                df_cedulas_audit=data.get('cedulas_audit'),
                df_inconsistencias=df_inconsistencias,
                upload_sheets=upload_sheets,
            )

            if result_path:
                self.log("=== PROCESO COMPLETADO ===", code="END")
            else:
                self.log("=== PROCESO CON ERRORES ===", code="ERR")

            return result_path

        except Exception as e:
            self.log(f"Error crítico: {e}", LogLevel.ERROR, "CRIT")
            return None

    # ------------------------------------------------------------------
    # v0.5.0 helpers (reemplazo de _denormalize_kpis_to_trips y _build_promedio_km_sheet)
    # ------------------------------------------------------------------

    def _denormalize_kpis_to_trips_v050(self, df_trips: pd.DataFrame,
                                         df_kpi: pd.DataFrame,
                                         df_opcedula: pd.DataFrame) -> pd.DataFrame:
        """Anade columnas de equipo y OpCedula a cada fila de Viajes.

        Sin logica de periodos: cada equipo tiene 1 fila en df_kpi, asi que el
        join es directo por `Equipo Motriz`. Para OpCedula se hace por
        `Operación cedula` (nombre interno) <-> `Operacion Cedula` (nuevo schema).
        """
        df = df_trips.copy()

        # --- Equipo motriz ---
        # Solo atributos (porcentajes/promedios) y los aditivos prorrateados.
        # Las metricas SUMABLES por fila viven aqui:
        #   - KM_total, Viajes_count      (ya vienen de process_trips_optimized)
        #   - Objetivo KM Total, Viajes   (ya vienen prorrateados)
        #   - Complemento Tendencia KM/V  (prorrateado abajo)
        #   - Tendencia KM Total / Viajes Total (KM_total/Viajes + Complemento)
        # Las columnas denormalizadas viejas (`Tendencia KM Equipo`, `KM Total Equipo`,
        # etc.) NO se exponen porque inflan al sumar; usar las `... Total` en su lugar.
        if not df_kpi.empty:
            eq_cols = ['Equipo Motriz', '% Operativo', 'Tendencia KM', 'KM Total',
                       'Tendencia Viajes', 'Viajes', 'Operacion Cedula',
                       'Cump KM %', 'Cump Viajes %', 'Densidad Viaje']
            eq_subset = df_kpi[[c for c in eq_cols if c in df_kpi.columns]].copy()
            # Complemento del equipo (parte proyectada) = Tendencia - KM real.
            # Se prorratea entre las filas del equipo cuya OpCedula del dia coincide
            # con la OpCedula vigente del motriz (Bug 3 fix). Esto preserva la
            # atribucion correcta cuando un equipo cambio de OpCedula durante el
            # periodo y el complemento debe imputarse a su asignacion vigente.
            eq_subset['__compl_km_equipo'] = (
                eq_subset.get('Tendencia KM', 0).fillna(0)
                - eq_subset.get('KM Total', 0).fillna(0)
            )
            eq_subset['__compl_v_equipo'] = (
                eq_subset.get('Tendencia Viajes', 0).fillna(0)
                - eq_subset.get('Viajes', 0).fillna(0)
            )
            # Removemos las agregadas crudas; solo dejamos atributos, OpCedula
            # vigente y los __compl* internos. El resto se calcula por fila.
            eq_subset = eq_subset.drop(columns=['Tendencia KM', 'Tendencia Viajes',
                                                  'KM Total', 'Viajes'])
            eq_subset = eq_subset.rename(columns={
                'Cump KM %': 'Cump KM Equipo %',
                'Cump Viajes %': 'Cump Viajes Equipo %',
                'Operacion Cedula': '__opcedula_vigente',
            })
            df['Equipo Motriz'] = df['Equipo Motriz'].astype(str)
            eq_subset['Equipo Motriz'] = eq_subset['Equipo Motriz'].astype(str)
            df = df.merge(eq_subset, on='Equipo Motriz', how='left')

            # Mascara: filas cuya OpCedula del dia == OpCedula vigente del motriz.
            # Se imputa el complemento solo a estas. Si un equipo no tiene NINGUNA
            # fila con OpCedula vigente (caso raro: cambio reciente sin viajes en
            # la nueva), se cae al prorrateo simple entre todas sus filas.
            df['__op_dia'] = df.get('Operación cedula', pd.Series('', index=df.index)).astype(str)
            df['__match_vigente'] = (
                df['__op_dia'] == df['__opcedula_vigente'].astype(str)
            )
            n_match_por_equipo = df.groupby('Equipo Motriz')['__match_vigente'].sum()
            n_total_por_equipo = df.groupby('Equipo Motriz').size()
            df['__n_match'] = df['Equipo Motriz'].map(n_match_por_equipo)
            df['__n_total'] = df['Equipo Motriz'].map(n_total_por_equipo)

            # Denominador: si hay matches -> N filas matching; si no -> N filas totales
            denom = df['__n_match'].where(df['__n_match'] > 0, df['__n_total'])
            denom = denom.replace(0, np.nan)
            # Distribuir SOLO en filas matching (o en todas si no hay matching)
            distribuir_aqui = df['__match_vigente'] | (df['__n_match'] == 0)
            df['Complemento Tendencia KM'] = np.where(
                distribuir_aqui,
                (df['__compl_km_equipo'].fillna(0) / denom).fillna(0),
                0.0,
            ).round(4)
            df['Complemento Tendencia Viajes'] = np.where(
                distribuir_aqui,
                (df['__compl_v_equipo'].fillna(0) / denom).fillna(0),
                0.0,
            ).round(4)
            # Aditivos por fila: SUM(KM_total) + SUM(Complemento) == Tendencia KM total flota
            df['Tendencia KM Total'] = (
                df.get('KM_total', 0).fillna(0) + df['Complemento Tendencia KM']
            ).round(2)
            df['Tendencia Viajes Total'] = (
                df.get('Viajes_count', 0).fillna(0) + df['Complemento Tendencia Viajes']
            ).round(2)
            df = df.drop(columns=['__compl_km_equipo', '__compl_v_equipo',
                                   '__opcedula_vigente', '__op_dia', '__match_vigente',
                                   '__n_match', '__n_total'])

        # --- OpCedula: solo atributos (porcentajes / conteos / promedios) ---
        # Las agregadas crudas (`Tendencia KM`, `Objetivo KM`, `Tendencia Viajes`,
        # `Objetivo Viajes`) NO se exponen aqui — al estar denormalizadas inflan
        # con SUM. Para totales por OpCedula, agrupar por `Operacion Cedula` con
        # los campos sumables ya disponibles (`KM_total`, `Tendencia KM Total`, etc.).
        if not df_opcedula.empty and 'Operación cedula' in df.columns:
            op_cols = ['Operacion Cedula', 'Motrices Titulares',
                       'Cumplimiento KM %', 'Cumplimiento Viajes %',
                       'Promedio KM dia unidad']
            op_subset = df_opcedula[[c for c in op_cols if c in df_opcedula.columns]].copy()
            op_subset = op_subset.rename(columns={
                'Operacion Cedula': '__opcedula_join',
                'Cumplimiento KM %': 'Cumplimiento KM OpCed %',
                'Cumplimiento Viajes %': 'Cumplimiento Viajes OpCed %',
            })
            df['__opcedula_join'] = df['Operación cedula'].astype(str)
            op_subset['__opcedula_join'] = op_subset['__opcedula_join'].astype(str)
            df = df.merge(op_subset, on='__opcedula_join', how='left')
            df = df.drop(columns=['__opcedula_join'])

        # Numericas faltantes -> 0
        for col in df.columns:
            if df[col].dtype.kind in {'f', 'i'} and df[col].isna().any():
                df[col] = df[col].fillna(0)

        self.log(f"Viajes enriquecido: {len(df.columns)} cols, {len(df)} filas",
                 code="DENORM")
        return df

    def _build_promedio_km_sheet_v050(self, df_opcedula: pd.DataFrame,
                                       df_kpi: pd.DataFrame) -> pd.DataFrame:
        """Hoja `Promedio KM por Unidad`: 1 fila por OpCedula vigente.

        Columnas: Operacion Cedula, Gerencia, Motrices Titulares,
        Remolques Unicos (count arrastres con esta OpCedula vigente),
        Promedio Diario KM/U (KM Total / dias unidad asignados).
        """
        if df_opcedula.empty:
            return pd.DataFrame()

        # Contar arrastres por OpCedula vigente
        if not df_kpi.empty:
            arrastres = df_kpi[df_kpi['Tipo Equipo'] != 'Motriz']
            remolques_por_op = arrastres.groupby('Operacion Cedula').size().to_dict()
        else:
            remolques_por_op = {}

        rows = []
        for _, r in df_opcedula.iterrows():
            opcedula = r.get('Operacion Cedula', '')
            rows.append({
                'Operacion Cedula': opcedula,
                'Gerencia': r.get('Gerencia', ''),
                'Motrices': int(r.get('Motrices Titulares', 0)),
                'Remolques Unicos': int(remolques_por_op.get(opcedula, 0)),
                'Promedio Diario KM/U': r.get('Promedio KM dia unidad', 0),
            })
        return pd.DataFrame(rows)


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

import gspread
import numpy as np
import pandas as pd
from google.oauth2.service_account import Credentials

from kpi_generator.config import Config, LogLevel
from kpi_generator.domain.change_tracker import ChangeTracker
from kpi_generator.domain.comodato import ComodatoManager

# --- v0.4.0: estructura de hojas y deadweight ---
# Columnas Tier 1 (deadweight) que se eliminan justo antes de exportar Excel/Sheets.
# llaveremolque/EqAsignados son intermediarios solo usados internamente para calcular
# `cuenta llaverem` y verificación de asignación (no consumidos por Looker).
# `Días Gestoría` y los 4 `Dias *` de OpCedula resultan siempre constantes en 0.
TRIP_DEADWEIGHT_COLS = ['llaveremolque', 'EqAsignados']
KPI_EQUIPO_DEADWEIGHT_COLS = ['Días Gestoría']
KPI_OPCEDULA_DEADWEIGHT_COLS = ['Dias Operando', 'Dias Taller', 'Dias Gestoria', 'Dias Sin Op']

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
}


class DataProcessor:
    """Motor optimizado de procesamiento de datos para análisis de KPIs de transporte."""

    def __init__(self, log_callback=print, log_level=LogLevel.INFO):
        self.log_func = log_callback
        self.log_level = log_level
        self._objective_cache = {}
        self._cedula_cache = {}
        self._stats = {'total_assigned': 0, 'periods_processed': 0}
        self.comodato_manager = ComodatoManager()
        self.change_tracker = ChangeTracker(log_callback)
    
    def log(self, message: str, level: LogLevel = LogLevel.INFO, code: str = None):
        """Sistema de logging simplificado con códigos."""
        if level.value <= self.log_level.value:
            prefix = f"[{code}]" if code else ""
            self.log_func(f"{prefix} {message}")
    
    @lru_cache(maxsize=128)
    def _parse_cedula_filename(self, filename: str) -> Optional[datetime]:
        """Extraer fecha del nombre de archivo de cédula (cached)."""
        patterns = [
            r'cedula\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})\.xlsx?',
            r'c[eé]dula\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})\.xlsx?',
            r'cedula\s*(\d{2})(\d{2})(\d{4})\.xlsx?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename.lower())
            if match:
                try:
                    day, month, year = map(int, match.groups())
                    return datetime(year, month, day)
                except ValueError:
                    continue
        return None
    
    def load_daily_cedulas(self, cedulas_folder: str) -> Optional[pd.DataFrame]:
        """Cargar y consolidar cédulas diarias optimizado."""
        try:
            self.log("Cargando cédulas", code="LOAD")
            folder_path = Path(cedulas_folder)
            
            if not folder_path.exists() or not folder_path.is_dir():
                self.log("Carpeta cédulas inválida", LogLevel.ERROR, "ERR")
                return None
            
            cedula_files = [
                (file_path, self._parse_cedula_filename(file_path.name))
                for file_path in folder_path.glob("*.xlsx")
            ]
            
            valid_files = [(f, d) for f, d in cedula_files if d is not None]
            invalid_files = [f.name for f, d in cedula_files if d is None]
            
            if invalid_files:
                self.log(f"Archivos formato inválido: {len(invalid_files)}", LogLevel.ERROR, "ERR")
                return None
            
            if not valid_files:
                self.log("Sin archivos válidos", LogLevel.ERROR, "ERR")
                return None
            
            valid_files.sort(key=lambda x: x[1])
            
            consolidated_cedulas = []
            required_cols = Config.COLUMNS["units"]
            
            for file_path, fecha in valid_files:
                try:
                    df = pd.read_excel(file_path)
                    
                    if not all(col in df.columns for col in required_cols):
                        missing = [col for col in required_cols if col not in df.columns]
                        self.log(f"Columnas faltantes en {file_path.name}: {missing}", LogLevel.ERROR, "ERR")
                        return None
                    
                    df['Fecha Cedula'] = fecha.strftime("%d/%m/%Y")
                    df['Fecha Cedula_dt'] = fecha
                    consolidated_cedulas.append(df)
                    
                except Exception as e:
                    self.log(f"Error procesando {file_path.name}: {e}", LogLevel.ERROR, "ERR")
                    return None
            
            df_cedulas = pd.concat(consolidated_cedulas, ignore_index=True)
            df_cedulas = self._fill_missing_dates(df_cedulas)
            
            self.log(f"Cédulas: {len(df_cedulas)} registros", code="OK")
            return df_cedulas
            
        except Exception as e:
            self.log(f"Error carga cédulas: {e}", LogLevel.ERROR, "ERR")
            return None
    
    def _fill_missing_dates(self, df_cedulas: pd.DataFrame) -> pd.DataFrame:
        """Rellenar fechas faltantes optimizado."""
        date_range = pd.date_range(
            start=df_cedulas['Fecha Cedula_dt'].min(),
            end=df_cedulas['Fecha Cedula_dt'].max(),
            freq='D'
        )
        existing_sorted = sorted(set(df_cedulas['Fecha Cedula_dt']))
        existing_set = set(existing_sorted)
        missing_dates = sorted(d for d in date_range if d not in existing_set)

        if not missing_dates:
            return df_cedulas

        self.log(f"Rellenando {len(missing_dates)} fechas", LogLevel.DEBUG, "FILL")

        # Snapshot por fecha existente — solo se lee una vez por fecha
        snapshots = {d: df_cedulas[df_cedulas['Fecha Cedula_dt'] == d] for d in existing_sorted}

        fill_frames = [df_cedulas]
        ptr = 0
        for missing_date in missing_dates:
            # Avanzar puntero hasta la fecha anterior más cercana (O(n) total, no O(n) por iteración)
            while ptr < len(existing_sorted) and existing_sorted[ptr] < missing_date:
                ptr += 1
            if ptr == 0:
                continue
            closest = existing_sorted[ptr - 1]
            records = snapshots[closest].copy()
            records['Fecha Cedula'] = missing_date.strftime("%d/%m/%Y")
            records['Fecha Cedula_dt'] = missing_date
            fill_frames.append(records)

        return pd.concat(fill_frames, ignore_index=True)

    def load_cedula_from_sheets(self, sheet_id: str, tab_name: str = None) -> Optional[pd.DataFrame]:
        """Carga la cédula mensual desde Google Sheets (formato horizontal) y la convierte
        al formato vertical que espera el resto del pipeline (una fila por unidad+día)."""
        DATE_COL_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
        UNIT_ID_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]+$')  # C070, T317, FL7, etc.
        SUBTOTAL_RE = re.compile(r'^\d+\s+al\s+\d+|en\s+adelante', re.IGNORECASE)
        SKIP_COL_NAMES = {'Taller', 'Gestoría', 'Sin operador', 'Sin Operador', ''}

        try:
            self.log("Conectando a Google Sheets para cédula", code="LOAD")
            creds = Credentials.from_service_account_file(
                Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES
            )
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(sheet_id)

            if tab_name is None:
                tab_name = sh.worksheets()[0].title
                self.log(f"Tab seleccionado: {tab_name}", LogLevel.DEBUG, "LOAD")

            ws = sh.worksheet(tab_name)
            all_rows = ws.get_all_values()

            if not all_rows:
                self.log("Sheet vacía", LogLevel.ERROR, "ERR")
                return None

            # Localizar la fila de encabezado principal (contiene 'Unidad' y 'Gerencia')
            header_idx = next(
                (i for i, row in enumerate(all_rows) if 'Unidad' in row and 'Gerencia' in row),
                None
            )
            if header_idx is None:
                self.log("Encabezado de cédula no encontrado en el sheet", LogLevel.ERROR, "ERR")
                return None

            header = all_rows[header_idx]
            data_rows = all_rows[header_idx + 1:]

            # Columnas de fecha (DD/MM/YYYY), excluir subtotales y conteos
            date_col_indices = [i for i, h in enumerate(header) if DATE_COL_RE.match(h)]
            if not date_col_indices:
                self.log("Sin columnas de fecha en el sheet", LogLevel.ERROR, "ERR")
                return None

            unit_col_idx = header.index('Unidad')

            # Filas de unidades activas: primera columna coincide con patrón de ID de equipo
            unit_rows = [
                row for row in data_rows
                if len(row) > unit_col_idx and UNIT_ID_RE.match(row[unit_col_idx].strip())
            ]

            if not unit_rows:
                self.log("Sin unidades válidas en el sheet", LogLevel.ERROR, "ERR")
                return None

            # Detectar dinámicamente todas las columnas de metadatos (excluye fechas y subtotales)
            meta_col_indices = [
                i for i, h in enumerate(header)
                if not DATE_COL_RE.match(h.strip())
                and not SUBTOTAL_RE.match(h.strip())
                and h.strip() not in SKIP_COL_NAMES
            ]

            # Construir registros: un registro por (unidad, fecha)
            records = []
            for row in unit_rows:
                padded = row + [''] * max(0, len(header) - len(row))
                meta = {header[i]: padded[i].strip() for i in meta_col_indices}
                for col_idx in date_col_indices:
                    records.append({
                        **meta,
                        'Fecha Cedula': header[col_idx],
                        'Operando': padded[col_idx].strip() if col_idx < len(padded) else '',
                    })

            df = pd.DataFrame(records)

            # Renombrar 'Unidad' → 'Unidades' para compatibilidad con el pipeline
            df = df.rename(columns={'Unidad': 'Unidades'})

            # Normalizar IDs y parsear fechas
            df['Unidades'] = df['Unidades'].str.strip().str.upper()
            df['Fecha Cedula_dt'] = pd.to_datetime(df['Fecha Cedula'], dayfirst=True)
            df = df.sort_values(['Unidades', 'Fecha Cedula_dt'])

            # Forward-fill: celda vacía hereda el estatus del día anterior de la misma unidad
            df['Operando'] = (
                df.groupby('Unidades')['Operando']
                .transform(lambda s: s.replace('', None).ffill())
                .fillna('Desconocido')
            )

            # Rellenar fechas completamente ausentes (igual que con archivos locales)
            df = self._fill_missing_dates(df)

            self.log(
                f"Cédula Sheets: {df['Unidades'].nunique()} unidades, "
                f"{df['Fecha Cedula_dt'].nunique()} días",
                code="OK"
            )
            return df

        except Exception as e:
            self.log(f"Error carga cédula desde Sheets: {e}", LogLevel.ERROR, "ERR")
            return None

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
            data['cedulas'] = df_cedulas
            data['cedulas_audit'] = df_cedulas_audit  # vacío si source != "db"
            
            if objectives_file and Path(objectives_file).exists():
                df_obj = pd.read_excel(objectives_file)
                missing = [col for col in Config.COLUMNS["objectives"] if col not in df_obj.columns]
                if missing:
                    self.log(f"Error objetivos - Columnas faltantes: {missing}", LogLevel.ERROR, "ERR")
                    return None
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
            return df, pd.DataFrame()

        if source == "db":
            self.log("Fuente cédulas: PostgreSQL", code="SRC")
            try:
                from kpi_generator.io.cedulas_db import load_cedulas_from_db
                from kpi_generator.io.date_range import derive_date_range
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
        
        # Mapeo de ClaveCategoria a Tipo de Unidad
        clave_to_tipo = {
            'CAMIONETA': 'CAMIONETA',
            'SENCILLO': 'TRACTOCAMION SENCILLO',
            'FULL': 'TRACTOCAMION FULL',
            'TORTHON': 'TORTHON',
            'THORTON': 'TORTHON',
            'PATIO': 'TRACTOCAMION PATIO',
            'DOBLE': 'TRACTOCAMION DOBLE'
        }
        
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
            tipo_unidad = clave_to_tipo.get(clave_categoria, f'TRACTOCAMION {clave_categoria}')
            
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
    
    def create_periods(self, df_cedulas: pd.DataFrame, unit: str = None) -> List[Dict]:
        """Crear períodos unificados optimizado."""
        units_to_process = [unit] if unit else df_cedulas['Unidades'].unique()
        all_periods = []
        
        for current_unit in units_to_process:
            unit_data = df_cedulas[df_cedulas['Unidades'] == current_unit].sort_values('Fecha Cedula_dt')
            
            if unit_data.empty:
                continue
            
            periods = []
            prev_key = None
            prev_row = None
            period_start = None
            
            for _, row in unit_data.iterrows():
                current_operation = self._get_operacion_cedula(
                    row['Operación'], row['Circuito'], row['Tipo de Unidad']
                )
                current_status = row['Operando']
                current_date = row['Fecha Cedula_dt']
                current_key = (current_operation, current_status)
                
                if prev_key != current_key:
                    if prev_key is not None and prev_row is not None:
                        end_date = current_date - pd.Timedelta(days=1)
                        periods.append({
                            'Unidades': str(current_unit),
                            'Gerencia': prev_row['Gerencia'],
                            'Operación': prev_row['Operación'],
                            'Tipo de Unidad': prev_row['Tipo de Unidad'],
                            'Circuito': prev_row['Circuito'],
                            'Estatus': prev_key[1],
                            'Operación cedula': prev_key[0],
                            'Fecha Inicio': period_start.strftime("%d/%m/%Y"),
                            'Fecha Fin': end_date.strftime("%d/%m/%Y"),
                            'Días Periodo': (current_date - period_start).days,
                            'operation': prev_key[0],
                            'status': prev_key[1],
                            'start_date': period_start.date(),
                            'end_date': end_date.date(),
                            'days': (current_date - period_start).days
                        })
                    
                    period_start = current_date
                    prev_key = current_key
                    prev_row = row
            
            if prev_key is not None and prev_row is not None:
                end_date = unit_data['Fecha Cedula_dt'].max()
                periods.append({
                    'Unidades': str(current_unit),
                    'Gerencia': prev_row['Gerencia'],
                    'Operación': prev_row['Operación'],
                    'Tipo de Unidad': prev_row['Tipo de Unidad'],
                    'Circuito': prev_row['Circuito'],
                    'Estatus': prev_key[1],
                    'Operación cedula': prev_key[0],
                    'Fecha Inicio': period_start.strftime("%d/%m/%Y"),
                    'Fecha Fin': end_date.strftime("%d/%m/%Y"),
                    'Días Periodo': (end_date - period_start).days + 1,
                    'operation': prev_key[0],
                    'status': prev_key[1],
                    'start_date': period_start.date(),
                    'end_date': end_date.date(),
                    'days': (end_date - period_start).days + 1
                })
            
            all_periods.extend(periods)
        
        if not unit:
            self.log(f"Períodos: {len(all_periods)}", code="PERIOD")
        return all_periods
    
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
            df.loc[indices, 'Objetivo KM Total'] = df.loc[indices, 'Objetivo KM Viaje'] + df.loc[indices, 'Complemento KM Objetivo']
            df.loc[indices, 'Objetivo Viajes Total'] = df.loc[indices, 'Objetivo Viajes Viaje'] + df.loc[indices, 'Complemento Viajes Objetivo']
        
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
    
    def create_kpi_summary_optimized(self, df_processed: pd.DataFrame, df_cedulas: pd.DataFrame, 
                                   unit_mapping: Dict, obj_mapping: Dict = None, analysis_date: datetime = None) -> pd.DataFrame:
        """Generar resumen KPI optimizado, incluyendo unidades fantasma."""
        self.log("Generando KPIs", code="KPI")
        
        # 1. KPIs para unidades en cédula (con períodos)
        df_kpi_base = self.create_periods(df_cedulas)
        
        if df_kpi_base:
            df_kpi = pd.DataFrame(df_kpi_base)
        else:
            df_kpi = pd.DataFrame()
        
        latest_date = df_processed['Fecha creación'].max()
        fecha_str = latest_date.strftime("%d/%m/%Y") if pd.notna(latest_date) else datetime.now().strftime("%d/%m/%Y")
        
        if not df_kpi.empty:
            df_kpi['Fecha Ultima modif'] = fecha_str
            df_kpi['Tipo de equipo'] = 'EQUIPO MOTRIZ'
            df_kpi['Denominación del equipo'] = df_kpi.apply(self._get_denominacion_equipo, axis=1)
            
            df_kpi = self._add_metrics_optimized(df_kpi, df_processed, df_cedulas)
            
            if obj_mapping is not None:
                df_kpi = self._calculate_compliance_optimized(df_kpi, obj_mapping)
        
        # 2. Agregar KPIs para unidades fantasma (sin cédula)
        phantom_units = [u for u, info in unit_mapping.items() if not info.get('En Cedula', True)]
        
        if phantom_units:
            self.log(f"Agregando {len(phantom_units)} unidades fantasma a KPIs", code="PHANTOM")
            df_phantom = self._create_phantom_kpis(phantom_units, unit_mapping, df_processed, fecha_str)
            
            if not df_phantom.empty:
                # Combinar con KPIs normales
                if df_kpi.empty:
                    df_kpi = df_phantom
                else:
                    df_kpi = pd.concat([df_kpi, df_phantom], ignore_index=True)
        
        return df_kpi
    
    def _create_phantom_kpis(self, phantom_units: List[str], unit_mapping: Dict, 
                           df_processed: pd.DataFrame, fecha_str: str) -> pd.DataFrame:
        """Crear registros KPI para unidades fantasma (sin cédula)."""
        phantom_records = []
        
        for unit_id in phantom_units:
            info = unit_mapping[unit_id]
            
            # Obtener viajes de esta unidad fantasma
            unit_trips = df_processed[df_processed['Equipo Motriz'].astype(str) == unit_id]
            
            if unit_trips.empty:
                continue
            
            # Calcular métricas
            km_cargado = float(unit_trips['KM_cargado'].sum())
            km_vacio = float(unit_trips['KM_vacio'].sum())
            km_total = float(unit_trips['KM_total'].sum())
            diesel = float(unit_trips['Diesel_LTS'].sum())
            viajes = int(unit_trips['Viajes_count'].sum())
            rendimiento = float(km_total / diesel) if diesel > 0 else 0.0
            
            # Fechas del período
            fecha_inicio = unit_trips['Fecha creación'].min()
            fecha_fin = unit_trips['Fecha creación'].max()
            dias_periodo = (fecha_fin - fecha_inicio).days + 1
            
            # Último viaje
            last_trip = unit_trips.loc[unit_trips['Fecha creación'].idxmax()]
            
            phantom_records.append({
                'Fecha Ultima modif': fecha_str,
                'Denominación del equipo': self._get_denominacion_from_tipo(info['Tipo de Unidad']),
                'Tipo de equipo': 'EQUIPO MOTRIZ',
                'Operación cedula': info['Operación cedula'],
                'Unidades': unit_id,
                'Gerencia': info['Gerencia'],
                'Operación': info['Operación'],
                'Tipo de Unidad': info['Tipo de Unidad'],
                'Circuito': info['Circuito'],
                'Estatus': info['Estatus'],
                'Fecha Inicio': fecha_inicio.strftime("%d/%m/%Y"),
                'Fecha Fin': fecha_fin.strftime("%d/%m/%Y"),
                'Días Periodo': dias_periodo,
                'Días Operando': 0,
                'Días Disponible': 0,
                'Días Gestoría': 0,
                'Días Taller': 0,
                'KMLiqCargadoFinal': km_cargado,
                'KMLiqVacioFinal': km_vacio,
                'KM Total': km_total,
                'Diesel LTS': diesel,
                'Viajes': viajes,
                'Rendimiento': rendimiento,
                '% Operativo': 0,
                'KM/h': round(km_total / (dias_periodo * 24), 4) if dias_periodo > 0 else 0,
                'Densidad Viaje': round(km_total / viajes, 2) if viajes > 0 else 0,
                'Tendencia KM': 0,
                'Obj KM Diario': 0,
                'Obj Viajes Diario': 0,
                'Objetivo KM Total': 0,
                'Objetivo Viajes Total': 0,
                'Cump. KM periodo': 0,
                'Cump. Viaje periodo': 0,
                'Número de Viaje': str(last_trip['Número de Viaje']),
                'Fecha Ult Viaje': last_trip['Fecha creación'].strftime("%d/%m/%Y"),
                'Centro': str(last_trip.get('Centro', '')),
                'Tipo De Operación': str(last_trip.get('Tipo De Operación', '')),
                'Ruta': str(last_trip.get('Ruta', '')),
                'Denominación': str(last_trip.get('Denominación', '')),
                'Alias Origen': str(last_trip.get('Alias Origen', '')),
                'Alias Destino': str(last_trip.get('Alias Destino', '')),
                'ClaveCategoria': str(last_trip.get('ClaveCategoria', ''))
            })
        
        return pd.DataFrame(phantom_records)
    
    def _get_denominacion_from_tipo(self, tipo_unidad: str) -> str:
        """Obtener denominación desde tipo de unidad."""
        tipo_upper = tipo_unidad.upper()
        if 'CAMIONETA' in tipo_upper:
            return 'CAMIONETA'
        elif 'TORTHON' in tipo_upper or 'THORTON' in tipo_upper:
            return 'THORTON'
        else:
            return 'TRACTOCAMION'
    
    def _get_denominacion_equipo(self, row) -> str:
        """Determinar denominación estándar del equipo."""
        tipo_equipo = str(row.get('Tipo de equipo', '')).upper()
        tipo_unidad = str(row.get('Tipo de Unidad', '')).upper()
        
        if tipo_equipo == 'EQUIPO REMOLQUE':
            return 'ARRASTRE'
        elif tipo_equipo == 'EQUIPO DOLLY':
            return 'DOLLY'
        elif tipo_equipo == 'EQUIPO MOTRIZ':
            if 'CAMIONETA' in tipo_unidad:
                return 'CAMIONETA'
            elif 'TORTHON' in tipo_unidad or 'THORTON' in tipo_unidad:
                return 'THORTON'
            else:
                return 'TRACTOCAMION'
        else:
            return str(row.get('Unidades', ''))
    
    def _add_metrics_optimized(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame, df_cedulas: pd.DataFrame) -> pd.DataFrame:
        """Agregar métricas optimizado usando groupby eficiente."""
        numeric_cols = ['KMLiqCargadoFinal', 'KMLiqVacioFinal', 'KM Total', 'Diesel LTS', 'Viajes', 'Rendimiento',
                       'Días Operando', 'Días Disponible', 'Días Gestoría', 'Días Taller', 'Tendencia KM']
        string_cols = ['Número de Viaje', 'Fecha Ult Viaje', 'Centro', 'Tipo De Operación', 'Ruta',
                      'Denominación', 'Alias Origen', 'Alias Destino', 'ClaveCategoria']

        for col in numeric_cols:
            df_kpi[col] = 0.0
        for col in string_cols:
            df_kpi[col] = ''

        # Pre-calcular días restantes del mes por día de semana (para tendencia por unidad)
        global_max_date = df_processed['Fecha creación'].max()
        days_in_month_global = calendar.monthrange(global_max_date.year, global_max_date.month)[1]
        remaining_by_weekday = Counter()
        for _d in range(global_max_date.day + 1, days_in_month_global + 1):
            _fd = datetime(global_max_date.year, global_max_date.month, _d)
            remaining_by_weekday[_fd.weekday()] += 1

        # Pre-indexar por unidad: evita re-escanear DataFrames completos en cada período
        _ced = df_cedulas.copy()
        _ced['Unidades'] = _ced['Unidades'].astype(str)
        cedulas_by_unit = {u: g.reset_index(drop=True) for u, g in _ced.groupby('Unidades')}

        _proc = df_processed.copy()
        _proc['Equipo Motriz'] = _proc['Equipo Motriz'].astype(str)
        trips_by_unit = {u: g.reset_index(drop=True) for u, g in _proc.groupby('Equipo Motriz')}

        for idx, period in df_kpi.iterrows():
            unit = period['Unidades']
            fecha_inicio = pd.to_datetime(period['Fecha Inicio'], format='%d/%m/%Y')
            fecha_fin = pd.to_datetime(period['Fecha Fin'], format='%d/%m/%Y')
            d0, d1 = fecha_inicio.date(), fecha_fin.date()

            _ced_unit = cedulas_by_unit.get(unit, pd.DataFrame())
            unit_cedula_data = (
                _ced_unit[(_ced_unit['Fecha Cedula_dt'].dt.date >= d0) & (_ced_unit['Fecha Cedula_dt'].dt.date <= d1)]
                if not _ced_unit.empty else pd.DataFrame()
            )

            status_counts = unit_cedula_data['Operando'].value_counts()
            df_kpi.at[idx, 'Días Operando'] = status_counts.get('Operando', 0)
            df_kpi.at[idx, 'Días Disponible'] = status_counts.get('Disponible', 0)
            df_kpi.at[idx, 'Días Gestoría'] = status_counts.get('Gestoría', 0)
            df_kpi.at[idx, 'Días Taller'] = status_counts.get('Taller', 0)

            _trips_unit = trips_by_unit.get(unit, pd.DataFrame())
            unit_trips = (
                _trips_unit[(_trips_unit['Fecha creación_date'] >= d0) & (_trips_unit['Fecha creación_date'] <= d1)]
                if not _trips_unit.empty else pd.DataFrame()
            )
            
            if not unit_trips.empty:
                df_kpi.at[idx, 'KMLiqCargadoFinal'] = float(unit_trips['KM_cargado'].sum())
                df_kpi.at[idx, 'KMLiqVacioFinal'] = float(unit_trips['KM_vacio'].sum())
                df_kpi.at[idx, 'KM Total'] = float(unit_trips['KM_total'].sum())
                df_kpi.at[idx, 'Diesel LTS'] = float(unit_trips['Diesel_LTS'].sum())
                df_kpi.at[idx, 'Viajes'] = int(unit_trips['Viajes_count'].sum())
                
                km_total = df_kpi.at[idx, 'KM Total']
                diesel_total = df_kpi.at[idx, 'Diesel LTS']
                df_kpi.at[idx, 'Rendimiento'] = float(km_total / diesel_total) if diesel_total > 0 else 0.0
                
                last_trip = unit_trips.loc[unit_trips['Fecha creación'].idxmax()]
                df_kpi.at[idx, 'Número de Viaje'] = str(last_trip['Número de Viaje'])
                df_kpi.at[idx, 'Fecha Ult Viaje'] = last_trip['Fecha creación'].strftime("%d/%m/%Y")

                string_fields = ['Centro', 'Tipo De Operación', 'Ruta', 'Denominación',
                               'Alias Origen', 'Alias Destino', 'ClaveCategoria']
                for field in string_fields:
                    df_kpi.at[idx, field] = str(last_trip.get(field, ''))

                # Tendencia KM: regresión lineal OLS sobre días con viajes
                km_actual = float(df_kpi.at[idx, 'KM Total'])
                daily_km = unit_trips.groupby('Fecha creación_date')['KM_total'].sum()
                remaining_days_total = sum(remaining_by_weekday.values())
                future_km = self._linear_project(
                    daily_km.values, km_actual, remaining_days_total, global_max_date.day
                )
                df_kpi.at[idx, 'Tendencia KM'] = round(km_actual + future_km, 2)

        return df_kpi
    
    def _calculate_compliance_optimized(self, df_kpi: pd.DataFrame, obj_mapping: Dict) -> pd.DataFrame:
        """Calcular cumplimiento optimizado vectorizado."""
        df_kpi['Obj KM Diario'] = 0.0
        df_kpi['Obj Viajes Diario'] = 0.0
        df_kpi['Objetivo KM Total'] = 0.0
        df_kpi['Objetivo Viajes Total'] = 0.0
        df_kpi['Cump. KM periodo'] = 0.0
        df_kpi['Cump. Viaje periodo'] = 0.0

        for operacion in df_kpi['Operación cedula'].unique():
            if operacion in obj_mapping:
                mask = df_kpi['Operación cedula'] == operacion
                obj_km_diario = obj_mapping[operacion]['Objetivo KM Diario']
                obj_viajes_diario = obj_mapping[operacion]['Objetivo Viajes Diario']

                dias_periodo = df_kpi.loc[mask, 'Días Periodo']
                objetivo_km_total = obj_km_diario * dias_periodo
                objetivo_viajes_total = obj_viajes_diario * dias_periodo

                df_kpi.loc[mask, 'Obj KM Diario'] = obj_km_diario
                df_kpi.loc[mask, 'Obj Viajes Diario'] = obj_viajes_diario
                df_kpi.loc[mask, 'Objetivo KM Total'] = objetivo_km_total
                df_kpi.loc[mask, 'Objetivo Viajes Total'] = objetivo_viajes_total

                # Cumplimiento usa Tendencia KM (proyección) vs Objetivo Total
                tendencia_km = df_kpi.loc[mask, 'Tendencia KM']
                viajes_actual = df_kpi.loc[mask, 'Viajes']

                df_kpi.loc[mask, 'Cump. KM periodo'] = np.where(
                    objetivo_km_total > 0,
                    round((tendencia_km / objetivo_km_total) * 100, 2),
                    0
                )
                df_kpi.loc[mask, 'Cump. Viaje periodo'] = np.where(
                    objetivo_viajes_total > 0,
                    round((viajes_actual / objetivo_viajes_total) * 100, 2),
                    0
                )

        # Métricas derivadas (vectorizadas)
        dias_p = df_kpi['Días Periodo'].replace(0, np.nan)
        df_kpi['% Operativo'] = (df_kpi['Días Operando'] / dias_p * 100).fillna(0).round(2)
        df_kpi['KM/h'] = (df_kpi['KM Total'] / (dias_p * 24)).fillna(0).round(4)
        viajes_s = df_kpi['Viajes'].replace(0, np.nan)
        df_kpi['Densidad Viaje'] = (df_kpi['KM Total'] / viajes_s).fillna(0).round(2)

        return df_kpi
    
    def add_trailer_equipment_optimized(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame) -> pd.DataFrame:
        """Integrar equipos de arrastre optimizado."""
        self.log("Integrando arrastre", LogLevel.DEBUG, "TRAIL")
        
        trailer_cols = ['Equipo Remolque 1', 'Equipo Dolly', 'Equipo Remolque 2']
        existing_units = set(df_kpi['Unidades'].astype(str))
        
        trailers = []
        for col in trailer_cols:
            unique_trailers = df_processed[col].dropna().unique()
            tipo_equipo = 'EQUIPO REMOLQUE' if 'Remolque' in col else 'EQUIPO DOLLY'
            denominacion = 'ARRASTRE' if 'Remolque' in col else 'DOLLY'
            
            for trailer in unique_trailers:
                trailer_str = str(trailer)
                if trailer_str not in existing_units:
                    trailer_trips = df_processed[df_processed[col] == trailer]
                    if not trailer_trips.empty:
                        source_trip = trailer_trips.iloc[-1]
                        
                        trailer_record = self._create_trailer_record(
                            trailer_str, tipo_equipo, denominacion, source_trip, len(trailer_trips)
                        )
                        trailers.append(trailer_record)
                        existing_units.add(trailer_str)
        
        if trailers:
            df_trailers = pd.DataFrame(trailers)
            df_trailers = df_trailers.reindex(columns=df_kpi.columns, fill_value=0)
            df_kpi = pd.concat([df_kpi, df_trailers], ignore_index=True)
            self.log(f"Arrastre: {len(trailers)} integrados", LogLevel.DEBUG, "OK")
        
        return df_kpi
    
    def _create_trailer_record(self, trailer_str: str, tipo_equipo: str, denominacion: str, 
                             source_trip: pd.Series, viajes_count: int) -> Dict:
        """Crear registro de remolque optimizado."""
        fecha_ultima_modif = source_trip['Fecha creación']
        fecha_str = fecha_ultima_modif.strftime("%d/%m/%Y") if pd.notna(fecha_ultima_modif) else datetime.now().strftime("%d/%m/%Y")
        
        return {
            'Fecha Ultima modif': fecha_str,
            'Denominación del equipo': denominacion,
            'Tipo de equipo': tipo_equipo,
            'Unidades': trailer_str,
            'Gerencia': source_trip.get('Gerencia', ''),
            'Operación': source_trip.get('Operación', ''),
            'Tipo de Unidad': tipo_equipo.replace('EQUIPO ', ''),
            'Circuito': source_trip.get('Circuito', ''),
            'Operación cedula': source_trip.get('Operación cedula', ''),
            'Estatus': 'Activo',
            'Viajes': viajes_count,
            'Número de Viaje': str(source_trip.get('Número de Viaje', '')),
            'Fecha Ult Viaje': fecha_str,
            'Centro': source_trip.get('Centro', ''),
            'Tipo De Operación': source_trip.get('Tipo De Operación', ''),
            'Ruta': source_trip.get('Ruta', ''),
            'Denominación': source_trip.get('Denominación', ''),
            'Alias Origen': source_trip.get('Alias Origen', ''),
            'Alias Destino': source_trip.get('Alias Destino', ''),
            'ClaveCategoria': source_trip.get('ClaveCategoria', '')
        }
    
    def create_opcedula_summary(self, df_processed: pd.DataFrame, df_cedulas: pd.DataFrame, obj_mapping: Dict = None) -> pd.DataFrame:
        """Generar resumen KPIs por Operación Cédula con tendencias y objetivos ponderados por período."""
        self.log("Generando KPIs OpCedula", code="OPCED")

        last_date = df_cedulas['Fecha Cedula_dt'].max()
        df_last_cedula = df_cedulas[df_cedulas['Fecha Cedula_dt'] == last_date].copy()

        df_last_cedula['Operación cedula'] = df_last_cedula.apply(
            lambda row: self._get_operacion_cedula(row['Operación'], row['Circuito'], row['Tipo de Unidad']),
            axis=1
        )

        # Períodos por unidad — base para objetivos ponderados (B)
        all_periods = self.create_periods(df_cedulas)

        opcedulas = df_processed['Operación cedula'].dropna().unique()
        opcedula_summary = []

        max_date = df_processed['Fecha creación'].max()
        days_in_month = calendar.monthrange(max_date.year, max_date.month)[1]
        days_elapsed = max_date.day
        
        for opcedula in opcedulas:
            if opcedula == 'Sin Asignar':
                continue
                
            opcedula_data = df_processed[df_processed['Operación cedula'] == opcedula]
            if opcedula_data.empty:
                continue
            
            titulares = df_last_cedula[df_last_cedula['Operación cedula'] == opcedula]['Unidades'].astype(str).unique()
            motrices_titulares = len(titulares)
            
            status_counts = {'Operando': 0, 'Taller': 0, 'Gestoria': 0, 'Sin Op': 0}
            for titular in titulares:
                titular_status = df_last_cedula[df_last_cedula['Unidades'].astype(str) == titular]['Operando'].iloc[0] if titular in df_last_cedula['Unidades'].astype(str).values else ''
                if titular_status.upper() in ['OPERANDO', 'DISPONIBLE', 'DESCANSO']:
                    status_counts['Operando'] += 1
                elif titular_status.upper() == 'TALLER':
                    status_counts['Taller'] += 1
                elif titular_status.upper() in ['GESTORIA', 'GESTORÍA']:
                    status_counts['Gestoria'] += 1
                else:
                    status_counts['Sin Op'] += 1
            
            motrices_utilizadas = opcedula_data['Equipo Motriz'].nunique()
            
            if 'Operación cedula' in df_cedulas.columns:
                ced_st = df_cedulas[df_cedulas['Operación cedula'] == opcedula]['Operando'].str.upper()
                m_op = ced_st.isin(['OPERANDO', 'DISPONIBLE', 'DESCANSO'])
                m_ta = ced_st == 'TALLER'
                m_ge = ced_st.isin(['GESTORIA', 'GESTORÍA'])
                dias_operando = int(m_op.sum())
                dias_taller   = int(m_ta.sum())
                dias_gestoria = int(m_ge.sum())
                dias_sinop    = int((~m_op & ~m_ta & ~m_ge).sum())
            else:
                dias_operando = dias_taller = dias_gestoria = dias_sinop = 0
            
            remolques = 0
            dollys = 0
            if 'Equipo Remolque 1' in opcedula_data.columns:
                remolques += opcedula_data['Equipo Remolque 1'].dropna().nunique()
            if 'Equipo Remolque 2' in opcedula_data.columns:
                remolques += opcedula_data['Equipo Remolque 2'].dropna().nunique()
            if 'Equipo Dolly' in opcedula_data.columns:
                dollys = opcedula_data['Equipo Dolly'].dropna().nunique()
            
            km_cargado = opcedula_data['KM_cargado'].sum()
            km_vacio = opcedula_data['KM_vacio'].sum()
            km_total = opcedula_data['KM_total'].sum()
            km_u_titular = km_total / motrices_titulares if motrices_titulares > 0 else 0
            km_u_real = km_total / motrices_utilizadas if motrices_utilizadas > 0 else 0
            
            remaining_days_opc = days_in_month - days_elapsed
            fechas_opc = pd.to_datetime(opcedula_data['Fecha creación']).dt.date

            daily_km_opc = opcedula_data.groupby(fechas_opc)['KM_total'].sum()
            future_km = self._linear_project(
                daily_km_opc.values, float(km_total), remaining_days_opc, days_elapsed
            )
            tendencia_km = km_total + future_km
            tendencia_km_u = tendencia_km / motrices_titulares if motrices_titulares > 0 else 0

            viajes = opcedula_data['Viajes_count'].sum()
            viajes_u = viajes / motrices_titulares if motrices_titulares > 0 else 0

            daily_v_opc = opcedula_data.groupby(fechas_opc)['Viajes_count'].sum()
            future_viajes = self._linear_project(
                daily_v_opc.values, float(viajes), remaining_days_opc, days_elapsed
            )
            tendencia_viajes = viajes + future_viajes
            tendencia_viajes_u = tendencia_viajes / motrices_titulares if motrices_titulares > 0 else 0
            
            diesel = opcedula_data['Diesel_LTS'].sum()
            
            objetivo_km = 0
            objetivo_viajes = 0
            objetivo_km_u = 0
            objetivo_viajes_u = 0
            
            if obj_mapping and opcedula in obj_mapping:
                obj_km_diario = obj_mapping[opcedula]['Objetivo KM Diario']
                obj_viajes_diario = obj_mapping[opcedula]['Objetivo Viajes Diario']
                # B: Objetivo ponderado por días reales en cada período de cada unidad
                periodos_opcedula = [p for p in all_periods if p['operation'] == opcedula]
                total_dias_ponderados = sum(p['days'] for p in periodos_opcedula)
                objetivo_km = obj_km_diario * total_dias_ponderados if total_dias_ponderados > 0 else obj_km_diario * days_in_month * motrices_titulares
                objetivo_viajes = obj_viajes_diario * total_dias_ponderados if total_dias_ponderados > 0 else obj_viajes_diario * days_in_month * motrices_titulares
                objetivo_km_u = (objetivo_km / motrices_titulares) if motrices_titulares > 0 else 0
                objetivo_viajes_u = (objetivo_viajes / motrices_titulares) if motrices_titulares > 0 else 0
            
            cumplimiento_km = (tendencia_km / objetivo_km * 100) if objetivo_km > 0 else 0
            cumplimiento_viajes = (tendencia_viajes / objetivo_viajes * 100) if objetivo_viajes > 0 else 0
            
            rendimiento = km_total / diesel if diesel > 0 else 0
            
            gerencia = opcedula_data['Gerencia'].iloc[0] if not opcedula_data['Gerencia'].empty else ''
            
            opcedula_summary.append({
                'Gerencia': gerencia,
                'Operación Cedula': opcedula,
                'Motrices Titulares': motrices_titulares,
                'Operando': status_counts['Operando'],
                'Taller': status_counts['Taller'],
                'Gestoria': status_counts['Gestoria'],
                'Sin Op': status_counts['Sin Op'],
                'Motrices Utilizadas': motrices_utilizadas,
                'Dias Operando': dias_operando,
                'Dias Taller': dias_taller,
                'Dias Gestoria': dias_gestoria,
                'Dias Sin Op': dias_sinop,
                'Remolques': remolques,
                'Dollys': dollys,
                'KM Cargado': round(km_cargado, 2),
                'KM Vacio': round(km_vacio, 2),
                'KM Total': round(km_total, 2),
                'KM/U Titular': round(km_u_titular, 2),
                'KM/U Real': round(km_u_real, 2),
                'Tendencia KM': round(tendencia_km, 2),
                'Tendencia KM/U': round(tendencia_km_u, 2),
                'Viajes': int(viajes),
                'V/U': round(viajes_u, 2),
                'Tendencia Viajes': round(tendencia_viajes, 2),
                'Tendencia V/U': round(tendencia_viajes_u, 2),
                'Diesel': round(diesel, 2),
                'Objetivo KM': round(objetivo_km, 2),
                'Objetivo Viajes': int(objetivo_viajes),
                'Objetivo KM/U': round(objetivo_km_u, 2),
                'Objetivo V/U': round(objetivo_viajes_u, 2),
                'Cumplimiento KM %': round(cumplimiento_km, 2),
                'Cumplimiento Viajes %': round(cumplimiento_viajes, 2),
                'Rendimiento': round(rendimiento, 2)
            })
        
        if opcedula_summary:
            self.log(f"OpCedula: {len(opcedula_summary)} operaciones", code="OK")
            return pd.DataFrame(opcedula_summary)
        else:
            self.log("Sin operaciones OpCedula", code="SKIP")
            return pd.DataFrame()
    
    def finalize_output(self, df_kpi: pd.DataFrame) -> pd.DataFrame:
        """Finalizar estructura de salida optimizada."""
        for col in Config.OUTPUT_COLUMNS:
            if col not in df_kpi.columns:
                default_val = 0 if col in [
                    'Días Periodo', 'Días Operando', 'Días Disponible', 'Días Gestoría', 'Días Taller',
                    '% Operativo', 'KMLiqCargadoFinal', 'KMLiqVacioFinal', 'KM Total', 'Diesel LTS',
                    'Viajes', 'Rendimiento', 'KM/h', 'Densidad Viaje', 'Tendencia KM',
                    'Obj KM Diario', 'Obj Viajes Diario', 'Objetivo KM Total', 'Objetivo Viajes Total',
                    'Cump. KM periodo', 'Cump. Viaje periodo'] else ''
                df_kpi[col] = default_val
        
        existing_cols = [col for col in Config.OUTPUT_COLUMNS if col in df_kpi.columns]
        df_output = df_kpi[existing_cols].copy()
        
        numeric_cols = ['Días Periodo', 'Días Operando', 'Días Disponible', 'Días Gestoría', 'Días Taller',
                       '% Operativo', 'KMLiqCargadoFinal', 'KMLiqVacioFinal', 'KM Total', 'Diesel LTS',
                       'Viajes', 'Rendimiento', 'KM/h', 'Densidad Viaje', 'Tendencia KM',
                       'Obj KM Diario', 'Obj Viajes Diario', 'Objetivo KM Total', 'Objetivo Viajes Total',
                       'Cump. KM periodo', 'Cump. Viaje periodo']
        
        for col in numeric_cols:
            if col in df_output.columns:
                df_output[col] = pd.to_numeric(df_output[col], errors='coerce').fillna(0)
        
        self.log(f"Estructura final: {len(df_output)} registros", code="FINAL")
        return df_output

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

    def _add_tendencia_complement_to_trips(self, df: pd.DataFrame) -> pd.DataFrame:
        """Hacer Tendencia KM y Tendencia Viajes aditivas por fila, análogo a Objetivo KM/Viajes Total.

        Lógica (homóloga para KM y Viajes):
          - Días pasados: el valor real ya está en KM_total / Viajes_count.
          - Días restantes: se proyectan con el promedio por día de semana de la unidad
            y se distribuyen entre todas sus filas existentes como complemento.
          - Tendencia KM Total    = KM_total    + Complemento Tendencia KM
          - Tendencia Viajes Total = Viajes_count + Complemento Tendencia Viajes

        SUM() de cualquiera de estos campos es correcto a cualquier granularidad
        (unidad, fecha, OpCedula, gerencia, total flota).
        """
        df = df.copy()
        df['Complemento Tendencia KM']     = 0.0
        df['Tendencia KM Total']           = df['KM_total'].fillna(0).astype(float)
        df['Complemento Tendencia Viajes'] = 0.0
        df['Tendencia Viajes Total']       = df['Viajes_count'].fillna(0).astype(float)

        global_max_date = df['Fecha creación'].max()
        days_in_month   = calendar.monthrange(global_max_date.year, global_max_date.month)[1]

        remaining_by_weekday = Counter()
        for _d in range(global_max_date.day + 1, days_in_month + 1):
            _fd = datetime(global_max_date.year, global_max_date.month, _d)
            remaining_by_weekday[_fd.weekday()] += 1

        remaining_days_total = sum(remaining_by_weekday.values())
        days_elapsed = global_max_date.day

        for unit, unit_trips in df.groupby('Equipo Motriz'):
            if unit_trips.empty:
                continue

            n_rows = len(unit_trips)

            # ── KM ──────────────────────────────────────────────────────────
            daily_km = unit_trips.groupby('Fecha creación_date')['KM_total'].sum()
            km_actual_unit = float(unit_trips['KM_total'].sum())
            future_km = self._linear_project(
                daily_km.values, km_actual_unit, remaining_days_total, days_elapsed
            )

            if future_km > 0:
                comp_km = future_km / n_rows
                df.loc[unit_trips.index, 'Complemento Tendencia KM'] = round(comp_km, 4)
                df.loc[unit_trips.index, 'Tendencia KM Total'] = (
                    unit_trips['KM_total'].fillna(0) + comp_km
                ).round(4)

            # ── Viajes ──────────────────────────────────────────────────────
            daily_v = unit_trips.groupby('Fecha creación_date')['Viajes_count'].sum()
            viajes_actual_unit = float(unit_trips['Viajes_count'].sum())
            future_v = self._linear_project(
                daily_v.values, viajes_actual_unit, remaining_days_total, days_elapsed
            )

            if future_v > 0:
                comp_v = future_v / n_rows
                df.loc[unit_trips.index, 'Complemento Tendencia Viajes'] = round(comp_v, 4)
                df.loc[unit_trips.index, 'Tendencia Viajes Total'] = (
                    unit_trips['Viajes_count'].fillna(0) + comp_v
                ).round(4)

        self.log("Tendencia KM y Viajes distribuidas por fila (aditivas)", code="TEND")
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

    def _denormalize_kpis_to_trips(self, df_trips: pd.DataFrame,
                                    df_final: pd.DataFrame,
                                    df_opcedula: pd.DataFrame) -> pd.DataFrame:
        """Denormalizar KPIs de período y OpCedula a cada fila de Viajes.

        Permite usar la hoja Viajes como fuente única en Looker con multi-filtro:
        - Nivel unidad-período: % Operativo, Tendencia KM, KM/h, Densidad Viaje, Cump.
        - Nivel OpCedula: Tendencia, Motrices, Objetivo, Cumplimiento.
        Los valores de período se repiten en cada fila de la unidad; en Looker
        usar MAX() para estas métricas en gráficas agrupadas.
        """
        df = df_trips.copy()

        # ── 1. Atributos nivel unidad-período (desde Equipos) ───────────────
        unit_kpi_cols = ['% Operativo', 'Tendencia KM', 'KM/h', 'Densidad Viaje',
                         'Cump. KM periodo', 'Cump. Viaje periodo']

        df_eq = df_final[df_final.get('Tipo de equipo', pd.Series()) == 'EQUIPO MOTRIZ'].copy() \
            if 'Tipo de equipo' in df_final.columns \
            else df_final.copy()

        df_eq['_fi'] = pd.to_datetime(df_eq['Fecha Inicio'], format='%d/%m/%Y', errors='coerce').dt.date
        df_eq['_ff'] = pd.to_datetime(df_eq['Fecha Fin'],    format='%d/%m/%Y', errors='coerce').dt.date

        for col in unit_kpi_cols:
            df[col] = np.nan

        for unit in df_eq['Unidades'].unique():
            u_str = str(unit)
            u_rows = df_eq[df_eq['Unidades'] == unit]
            u_mask = df['Equipo Motriz'].astype(str) == u_str
            if not u_mask.any():
                continue
            for _, period in u_rows.iterrows():
                if pd.isna(period['_fi']) or pd.isna(period['_ff']):
                    continue
                d_mask = (
                    (df['Fecha creación_date'] >= period['_fi']) &
                    (df['Fecha creación_date'] <= period['_ff'])
                )
                full_mask = u_mask & d_mask
                if full_mask.any():
                    for col in unit_kpi_cols:
                        if col in period.index and pd.notna(period[col]):
                            df.loc[full_mask, col] = period[col]

        for col in unit_kpi_cols:
            df[col] = df[col].fillna(0)

        # ── 2. Atributos nivel OpCedula ──────────────────────────────────────
        if not df_opcedula.empty and 'Operación Cedula' in df_opcedula.columns:
            opc_rename = {
                'Motrices Titulares':   'Motrices Titulares',
                'Motrices Utilizadas':  'Motrices Utilizadas',
                'KM/U Titular':         'KM/U Titular',
                'KM/U Real':            'KM/U Real',
                'Tendencia KM':         'Tendencia KM OpCed',
                'Tendencia KM/U':       'Tendencia KM/U OpCed',
                'Tendencia Viajes':     'Tendencia Viajes OpCed',
                'V/U':                  'V/U',
                'Objetivo KM':          'Objetivo KM OpCed',
                'Objetivo Viajes':      'Objetivo Viajes OpCed',
                'Objetivo KM/U':        'Objetivo KM/U',
                'Objetivo V/U':         'Objetivo V/U',
                'Cumplimiento KM %':    'Cumplimiento KM % OpCed',
                'Cumplimiento Viajes %':'Cumplimiento Viajes % OpCed',
                'Rendimiento':          'Rendimiento OpCed',
            }
            cols_to_take = ['Operación Cedula'] + [c for c in opc_rename if c in df_opcedula.columns]
            df_opc = df_opcedula[cols_to_take].copy().rename(columns=opc_rename)

            df = df.merge(df_opc, left_on='Operación cedula',
                          right_on='Operación Cedula', how='left')
            df.drop(columns=['Operación Cedula'], inplace=True, errors='ignore')

            new_opc_cols = [opc_rename[c] for c in opc_rename if opc_rename[c] in df.columns]
            for col in new_opc_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        self.log(f"Viajes enriquecido: {len(df.columns)} cols, {len(df)} filas", code="DENORM")
        return df

    def _build_promedio_km_sheet(self, df_opcedula: pd.DataFrame, days_elapsed: int) -> pd.DataFrame:
        """A: Construir tabla PromedioKMunitOps desde el resumen OpCedula."""
        if df_opcedula.empty:
            return pd.DataFrame()
        rows = []
        for _, r in df_opcedula.iterrows():
            motrices = r.get('Motrices Titulares', 0)
            km_total = r.get('KM Total', 0)
            promedio = round(km_total / (motrices * days_elapsed), 4) if motrices > 0 and days_elapsed > 0 else 0
            rows.append({
                'Operación Cedula': r.get('Operación Cedula', ''),
                'Gerencia': r.get('Gerencia', ''),
                'Motrices': int(motrices),
                'Remolques Únicos': int(r.get('Remolques', 0)),
                'Promedio Diario KM/U': promedio,
            })
        return pd.DataFrame(rows)

    def upload_to_sheets(self, df_resumen: pd.DataFrame, df_kpi: pd.DataFrame,
                         df_processed: pd.DataFrame, df_changes: pd.DataFrame,
                         df_opcedula: pd.DataFrame, df_objectives: pd.DataFrame,
                         df_promedio: pd.DataFrame) -> bool:
        """C: Subir todos los DataFrames a Google Sheets automáticamente (v0.4.0).

        Usa los tabs canónicos definidos en SHEETS_TAB_NAMES. Los tabs viejos
        (Equipos, OpCedula, PromedioKMunitOps) quedan huérfanos en el spreadsheet
        y deben borrarse manualmente al promover esta versión.
        """
        try:
            self.log("Conectando a Google Sheets...", code="SHEETS")
            creds = Credentials.from_service_account_file(Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(Config.SHEETS_ID)

            def write_sheet(tab_name: str, df: pd.DataFrame):
                if df is None or df.empty:
                    return
                df_str = df.fillna('').astype(str)
                data = [df_str.columns.tolist()] + df_str.values.tolist()
                try:
                    ws = sh.worksheet(tab_name)
                except gspread.WorksheetNotFound:
                    ws = sh.add_worksheet(title=tab_name, rows=len(df) + 2, cols=len(df.columns) + 1)
                ws.clear()
                ws.update(data, value_input_option='USER_ENTERED')
                self.log(f"Sheets '{tab_name}': {len(df)} filas", code="SHEETS")

            write_sheet(SHEETS_TAB_NAMES['resumen'], df_resumen)
            write_sheet(SHEETS_TAB_NAMES['por_equipo'], df_kpi)
            write_sheet(SHEETS_TAB_NAMES['trip_data'], df_processed)
            write_sheet(SHEETS_TAB_NAMES['cambios'], df_changes)
            write_sheet(SHEETS_TAB_NAMES['por_operacion'], df_opcedula)
            write_sheet(SHEETS_TAB_NAMES['objetivos'], df_objectives)
            write_sheet(SHEETS_TAB_NAMES['promedio'], df_promedio)

            self.log("Google Sheets actualizado correctamente", code="SHEETS")
            return True

        except Exception as e:
            self.log(f"Error Google Sheets: {e}", LogLevel.ERROR, "SHEETS")
            return False

    def _drop_deadweight(self, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        """Elimina columnas deadweight (intermediarios o constantes) sin fallar si no existen."""
        if df is None or df.empty:
            return df
        to_drop = [c for c in cols if c in df.columns]
        if to_drop:
            return df.drop(columns=to_drop)
        return df

    def _build_resumen_ejecutivo(self, df_opcedula: pd.DataFrame) -> pd.DataFrame:
        """Construye la hoja Resumen agregando df_opcedula por Gerencia + fila TOTAL.

        Una fila por gerencia con totales de unidades por estatus, KM, viajes, diesel,
        y cumplimientos ponderados. Última fila = TOTAL TUMSA.

        Sin lógica de cálculo nueva — solo sumas/promedios ponderados sobre Por Operación.
        """
        if df_opcedula is None or df_opcedula.empty:
            self.log("Sin datos de OpCedula para Resumen", code="RESUMEN")
            return pd.DataFrame()

        df = df_opcedula.copy()

        # Columnas que deben existir; usamos defaults si alguna falta
        num_cols_sum = {
            'Motrices Titulares': 'Unidades Activas',
            'Operando': 'Operando',
            'Taller': 'Taller',
            'Gestoria': 'Gestoría',
            'Sin Op': 'Sin Op',
            'KM Total': 'KM Total',
            'Viajes': 'Viajes',
            'Diesel': 'Diesel LTS',
            'Objetivo KM': 'Objetivo KM',
            'Objetivo Viajes': 'Objetivo Viajes',
        }
        for col in num_cols_sum:
            if col not in df.columns:
                df[col] = 0

        grouped = df.groupby('Gerencia', dropna=False).agg(
            **{out: (src, 'sum') for src, out in num_cols_sum.items()}
        ).reset_index()

        # Métricas derivadas (cumplimientos y rendimiento ponderados)
        grouped['Rendimiento'] = (grouped['KM Total'] / grouped['Diesel LTS']
                                    ).where(grouped['Diesel LTS'] > 0, 0).round(2)
        grouped['Cumplimiento KM %'] = (grouped['KM Total'] / grouped['Objetivo KM'] * 100
                                          ).where(grouped['Objetivo KM'] > 0, 0).round(1)
        grouped['Cumplimiento Viajes %'] = (grouped['Viajes'] / grouped['Objetivo Viajes'] * 100
                                              ).where(grouped['Objetivo Viajes'] > 0, 0).round(1)

        # Nota: la categoría 'Disponible' del Excel legacy no existe como columna
        # separada en Por Operación (la BD agrupa Disponible y Descanso dentro de
        # 'Operando'). Por eso el Resumen no la incluye — sería siempre 0.

        # Reordenar y agregar fila TOTAL
        cols_out = ['Gerencia', 'Unidades Activas', 'Operando', 'Taller',
                    'Gestoría', 'Sin Op', 'KM Total', 'Viajes', 'Diesel LTS',
                    'Rendimiento', 'Objetivo KM', 'Objetivo Viajes',
                    'Cumplimiento KM %', 'Cumplimiento Viajes %']
        grouped = grouped[cols_out]

        total = grouped.drop(columns='Gerencia').sum(numeric_only=True)
        # Rendimiento y cumplimientos del total se recalculan (no se promedian)
        total['Rendimiento'] = round(total['KM Total'] / total['Diesel LTS'], 2) if total['Diesel LTS'] > 0 else 0
        total['Cumplimiento KM %'] = round(total['KM Total'] / total['Objetivo KM'] * 100, 1) if total['Objetivo KM'] > 0 else 0
        total['Cumplimiento Viajes %'] = round(total['Viajes'] / total['Objetivo Viajes'] * 100, 1) if total['Objetivo Viajes'] > 0 else 0
        total_row = pd.DataFrame([{'Gerencia': 'TOTAL TUMSA', **total.to_dict()}])

        resumen = pd.concat([grouped, total_row], ignore_index=True)
        self.log(f"Resumen ejecutivo: {len(grouped)} gerencias + TOTAL", code="RESUMEN")
        return resumen

    def save_results(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame, df_changes: pd.DataFrame,
                     df_opcedula: pd.DataFrame, output_path: str, df_objectives: pd.DataFrame = None,
                     df_promedio: pd.DataFrame = None, df_cedulas_audit: pd.DataFrame = None,
                     upload_sheets: bool = True) -> Optional[str]:
        """Generar archivo Excel + subida automática a Google Sheets (v0.4.0).

        Orden de hojas: Resumen → Por Equipo → Viajes → Resumen de Cambios →
        Por Operación → Objetivos → Promedio KM por Unidad → Cedulas Rellenadas.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"KPIs_Transport_{timestamp}.xlsx"
            full_path = Path(output_path) / filename

            # Aplicar drops de deadweight (Tier 1) sin tocar la lógica de cálculo
            df_kpi = self._drop_deadweight(df_kpi, KPI_EQUIPO_DEADWEIGHT_COLS)
            df_opcedula = self._drop_deadweight(df_opcedula, KPI_OPCEDULA_DEADWEIGHT_COLS)
            df_processed = self._drop_deadweight(df_processed, TRIP_DEADWEIGHT_COLS)

            df_processed_formatted = df_processed.copy()
            if 'Fecha creación' in df_processed_formatted.columns:
                df_processed_formatted['Fecha creación'] = df_processed_formatted['Fecha creación'].dt.strftime("%d/%m/%Y")
            # Naming canónico de columna de operación cedula en outputs (v0.4.0).
            # Internamente el pipeline usa 'Operación cedula' (c minúscula); aquí
            # publicamos 'Operación Cedula' para consistencia con Por Operación.
            if 'Operación cedula' in df_processed_formatted.columns:
                df_processed_formatted = df_processed_formatted.rename(columns={'Operación cedula': 'Operación Cedula'})
            if 'OpCedula Foto' in df_processed_formatted.columns:
                pass  # `OpCedula Foto` permanece (es el snapshot, distinto del histórico)
            # df_kpi también puede tener la columna en minúscula
            if 'Operación cedula' in df_kpi.columns:
                df_kpi = df_kpi.rename(columns={'Operación cedula': 'Operación Cedula'})

            # Construir Resumen ejecutivo a partir de Por Operación
            df_resumen = self._build_resumen_ejecutivo(df_opcedula)

            with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
                # 1. Resumen ejecutivo (vista de página principal)
                if not df_resumen.empty:
                    df_resumen.to_excel(writer, sheet_name=SHEET_NAMES['resumen'], index=False)

                # 2. KPIs por equipo (drill-down)
                df_kpi.to_excel(writer, sheet_name=SHEET_NAMES['por_equipo'], index=False)

                # 3. Viajes denormalizados (fuente única Looker)
                df_processed_formatted.to_excel(writer, sheet_name=SHEET_NAMES['trip_data'], index=False)

                # 4. Cambios operacionales
                if not df_changes.empty:
                    df_changes.to_excel(writer, sheet_name=SHEET_NAMES['cambios'], index=False)
                    self.log(f"Hoja {SHEET_NAMES['cambios']}: {len(df_changes)} cambios", code="CHG")
                else:
                    self.log(f"Hoja {SHEET_NAMES['cambios']}: Sin cambios operacionales", code="CHG")

                # 5. KPIs por operación cédula
                if not df_opcedula.empty:
                    df_opcedula.to_excel(writer, sheet_name=SHEET_NAMES['por_operacion'], index=False)
                    self.log(f"Hoja {SHEET_NAMES['por_operacion']}: {len(df_opcedula)} operaciones", code="OPCED")
                else:
                    self.log(f"Hoja {SHEET_NAMES['por_operacion']}: Sin operaciones", code="OPCED")

                # 6. Objetivos
                if df_objectives is not None and not df_objectives.empty:
                    df_objectives.to_excel(writer, sheet_name=SHEET_NAMES['objetivos'], index=False)

                # 7. Promedio KM por unidad (benchmark)
                if df_promedio is not None and not df_promedio.empty:
                    df_promedio.to_excel(writer, sheet_name=SHEET_NAMES['promedio'], index=False)

                # 8. Auditoría de forward-fill (solo cuando source='db')
                if df_cedulas_audit is not None and not df_cedulas_audit.empty:
                    df_cedulas_audit.to_excel(writer, sheet_name=SHEET_NAMES['audit'], index=False)
                    rellenadas = (df_cedulas_audit['Origen'] == 'forward_fill').sum()
                    self.log(f"Hoja {SHEET_NAMES['audit']}: {rellenadas} días por forward-fill "
                             f"de {len(df_cedulas_audit)} totales", code="AUDIT")

                self._format_excel_columns(writer)

            self.log(f"Archivo: {filename}", code="SAVE")

            # Subida automática a Google Sheets (con Resumen incluido)
            if upload_sheets:
                self.upload_to_sheets(
                    df_resumen, df_kpi, df_processed_formatted, df_changes, df_opcedula,
                    df_objectives if df_objectives is not None else pd.DataFrame(),
                    df_promedio if df_promedio is not None else pd.DataFrame(),
                )

            return str(full_path)

        except Exception as e:
            self.log(f"Error generación archivo: {e}", LogLevel.ERROR, "ERR")
            return None

    def _format_excel_columns(self, writer):
        """Formatear columnas de Excel de manera eficiente."""
        try:
            for sheet_name in list(SHEET_NAMES.values()):
                if sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    for column in worksheet.columns:
                        max_length = min(max(len(str(cell.value or '')) for cell in column) + 2, 50)
                        worksheet.column_dimensions[column[0].column_letter].width = max_length
        except Exception:
            pass
    
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
                        cedulas_source: str = None) -> Optional[str]:
        """Ejecutar proceso completo optimizado con comodatos, cambios y OpCedula.

        `cedulas_source`: "db" | "excel" | "sheets" | None (usa Config.CEDULAS_SOURCE).
        """
        try:
            self.log("=== INICIO PROCESO KPI ===", code="START")

            data = self.load_data(trips_file, fuel_file, cedulas_folder, objectives_file,
                                  cedulas_source=cedulas_source)
            if not data:
                return None
            
            analysis_date = datetime.now()
            if not data['trips'].empty and 'Fecha creación' in data['trips'].columns:
                trip_dates = pd.to_datetime(data['trips']['Fecha creación'], errors='coerce').dropna()
                if not trip_dates.empty:
                    analysis_date = trip_dates.max()
            
            unit_mapping = self.create_unit_mapping(data['cedulas'], analysis_date)
            
            obj_mapping = None
            if data['objectives'] is not None:
                obj_mapping = self.process_objectives(data['objectives'], unit_mapping, analysis_date)
            
            df_processed = self.process_trips_optimized(data['trips'], data['cedulas'], data['fuel'], obj_mapping)
            # A: Columnas extra para Looker Studio (antes en Sheets)
            df_processed = self._add_trip_extra_columns(df_processed, data['cedulas'])
            # Tendencia KM aditiva por fila (como Objetivo KM Total)
            df_processed = self._add_tendencia_complement_to_trips(df_processed)
            df_kpi = self.create_kpi_summary_optimized(df_processed, data['cedulas'], unit_mapping, obj_mapping, analysis_date)
            df_kpi = self.add_trailer_equipment_optimized(df_kpi, df_processed)
            df_final = self.finalize_output(df_kpi)

            df_changes = self.change_tracker.track_operation_changes(data['cedulas'], obj_mapping)

            df_opcedula = self.create_opcedula_summary(df_processed, data['cedulas'], obj_mapping)

            # Denormalizar KPIs de período y OpCedula a cada fila de Viajes (multi-filtro Looker)
            df_processed = self._denormalize_kpis_to_trips(df_processed, df_final, df_opcedula)

            # A: Tabla PromedioKMunitOps
            max_date = df_processed['Fecha creación'].max()
            days_elapsed = max_date.day
            df_promedio = self._build_promedio_km_sheet(df_opcedula, days_elapsed)

            result_path = self.save_results(
                df_final, df_processed, df_changes, df_opcedula, output_path,
                df_objectives=data['objectives'],
                df_promedio=df_promedio,
                df_cedulas_audit=data.get('cedulas_audit'),
                upload_sheets=True
            )

            if result_path:
                self.log("=== PROCESO COMPLETADO ===", code="END")
            else:
                self.log("=== PROCESO CON ERRORES ===", code="ERR")

            return result_path
            
        except Exception as e:
            self.log(f"Error crítico: {e}", LogLevel.ERROR, "CRIT")
            return None


import pandas as pd
import numpy as np
import tkinter as tk
import threading
import calendar
import os
import platform
import subprocess
import re
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional, List, Tuple
from pathlib import Path
from functools import lru_cache
from enum import Enum
from collections import Counter
import gspread
from google.oauth2.service_account import Credentials

class LogLevel(Enum):
    ERROR = 1
    INFO = 2
    DEBUG = 3

class Config:
    """Configuración centralizada del sistema KPI Generator."""
    COLUMNS = {
        "trips": ["Número de Viaje", "Fecha creación", "Centro", "Tipo De Operación", 
                  "KMLiqCargadoFinal", "KMLiqVacioFinal", "Ruta", "Denominación", 
                  "Alias Origen", "Alias Destino", "ClaveCategoria", "Distancia", 
                  "StatusViaje", "Equipo Motriz", "Equipo Remolque 1", "Equipo Dolly", 
                  "Equipo Remolque 2"],
        "fuel": ["Número de Viaje", "Equipo Motriz", "Fecha carga combustible", 
                 "Cantidad Litros Real", "Precio Unitario Real", "Importe Total Real", "StatusVale"],
        "units": ["Unidades", "Gerencia", "Operación", "Tipo de Unidad", "Circuito", "Operando"],
        "objectives": ["Gerencia", "Operación Cedula", "Objetivo KM", "Objetivo Viajes"]
    }
    
    SPECIAL_CIRCUITS = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}

    CREDENTIALS_PATH = str(Path(__file__).parent / "project-9406a3c3-f626-4941-a65-a817bae635c9.json")
    SHEETS_ID = "1sv8P004Ej85D_GF4YwEmoBO1XqWR1KYdGOSb1FJWM8Y"
    CEDULA_SHEET_ID = "18lw2_Rv-j_vwXTwXXGKX5-BZ_8t8MNfz6IHuLaIKBf0"
    SHEETS_SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    OUTPUT_COLUMNS = [
        'Fecha Ultima modif', 'Denominación del equipo', 'Tipo de equipo', 'Operación cedula',
        'Unidades', 'Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Estatus',
        'Fecha Inicio', 'Fecha Fin', 'Días Periodo', 'Días Operando', 'Días Disponible', 'Días Gestoría', 'Días Taller',
        '% Operativo',
        'KMLiqCargadoFinal', 'KMLiqVacioFinal', 'KM Total', 'Diesel LTS', 'Viajes', 'Rendimiento',
        'KM/h', 'Densidad Viaje', 'Tendencia KM',
        'Obj KM Diario', 'Obj Viajes Diario', 'Objetivo KM Total', 'Objetivo Viajes Total',
        'Cump. KM periodo', 'Cump. Viaje periodo',
        'Número de Viaje', 'Fecha Ult Viaje', 'Centro', 'Tipo De Operación',
        'Ruta', 'Denominación', 'Alias Origen', 'Alias Destino', 'ClaveCategoria'
    ]

class ComodatoManager:
    """Gestor modular de registros comodato para días sin viajes."""
    
    def __init__(self, base_id=2000000000):
        self.comodato_id = base_id
    
    def _get_operacion_cedula_comodato(self, operacion: str, circuito: str, tipo_unidad: str) -> str:
        """Generar cédula de operación para comodatos según reglas de negocio."""
        circuito_upper = circuito.upper()
        operacion_upper = operacion.upper()
        tipo_unidad_upper = tipo_unidad.upper()
        
        special_circuits = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}
        
        if circuito_upper in special_circuits:
            return f"{operacion_upper} {tipo_unidad_upper}"
        return f"{operacion_upper} {circuito_upper}"
    
    def create_comodatos(self, df_trips: pd.DataFrame, df_cedulas: pd.DataFrame, 
                        unit_mapping: Dict, log_func=print) -> pd.DataFrame:
        """Generar registros comodato para días sin viajes, respetando fechas de ingreso/egreso.
        
        IMPORTANTE: Solo genera comodatos para unidades que ESTÁN en cédulas.
        Las unidades fantasma (solo en viajes) NO generan comodatos.
        """
        comodatos = []
        units_in_cedula = df_cedulas['Unidades'].unique()
        
        for unit in units_in_cedula:
            unit_str = str(unit)
            if unit_str not in unit_mapping:
                continue
            
            # Verificar que la unidad esté en cédula (no sea fantasma)
            if not unit_mapping[unit_str].get('En Cedula', True):
                continue  # Skip unidades fantasma
                
            unit_cedulas = df_cedulas[df_cedulas['Unidades'] == unit]
            unit_trips = df_trips[df_trips['Equipo Motriz'] == unit_str]
            
            # Obtener rango de fechas de aparición en cédulas (respeta ingreso/egreso)
            primera_fecha_cedula = unit_cedulas['Fecha Cedula_dt'].min().date()
            ultima_fecha_cedula = unit_cedulas['Fecha Cedula_dt'].max().date()
            
            # Fechas donde la unidad está en cédula
            cedula_dates = set(unit_cedulas['Fecha Cedula_dt'].dt.date)
            
            # Fechas donde la unidad tuvo viajes
            trip_dates = set(unit_trips['Fecha creación_date']) if not unit_trips.empty else set()
            
            # Fechas faltantes: días en cédula sin viajes
            missing_dates = cedula_dates - trip_dates
            
            # IMPORTANTE: Filtrar solo fechas dentro del rango de aparición
            # NO generar comodatos antes del ingreso ni después del egreso
            missing_dates = {
                fecha for fecha in missing_dates 
                if primera_fecha_cedula <= fecha <= ultima_fecha_cedula
            }
            
            if missing_dates:
                info = unit_mapping[unit_str]
                for fecha_missing in missing_dates:
                    # Obtener info de cédula para esa fecha
                    cedula_day = unit_cedulas[unit_cedulas['Fecha Cedula_dt'].dt.date == fecha_missing]
                    if not cedula_day.empty:
                        cedula_info = cedula_day.iloc[0]
                        operacion_cedula_dia = self._get_operacion_cedula_comodato(
                            cedula_info['Operación'], 
                            cedula_info['Circuito'], 
                            cedula_info['Tipo de Unidad']
                        )
                        
                        comodatos.append({
                            'Número de Viaje': self.comodato_id,
                            'Fecha creación': pd.Timestamp(fecha_missing),
                            'Fecha creación_date': fecha_missing,
                            'Centro': 'COMODATO',
                            'Tipo De Operación': 'COMODATO',
                            'Equipo Motriz': unit_str,
                            'StatusViaje': 'X',
                            'KMLiqCargadoFinal': 0,
                            'KMLiqVacioFinal': 0,
                            'Distancia': 0,
                            'KM_cargado': 0,
                            'KM_vacio': 0,
                            'KM_total': 0,
                            'Diesel_LTS': 0,
                            'Rendimiento': 0,
                            'Viajes_count': 0,
                            'Gerencia': cedula_info['Gerencia'],
                            'Operación': cedula_info['Operación'],
                            'Tipo de Unidad': cedula_info['Tipo de Unidad'],
                            'Circuito': cedula_info['Circuito'],
                            'Operando': cedula_info['Operando'],
                            'Operación cedula': operacion_cedula_dia,
                            'Ruta': 'COMODATO',
                            'Denominación': 'COMODATO',
                            'Alias Origen': 'COMODATO',
                            'Alias Destino': 'COMODATO',
                            'ClaveCategoria': 'COM'
                        })
                        self.comodato_id += 1
        
        if comodatos:
            log_func(f"[COM] {len(comodatos)} comodatos (solo unidades en cédula)")
        
        return pd.DataFrame(comodatos)
    
    def integrate_comodatos(self, df_trips: pd.DataFrame, comodatos: pd.DataFrame) -> pd.DataFrame:
        """Integrar comodatos manteniendo estructura original."""
        if comodatos.empty:
            return df_trips
        
        try:
            comodatos['Número de Viaje'] = comodatos['Número de Viaje'].astype('int64')
            df_trips['Número de Viaje'] = df_trips['Número de Viaje'].astype('int64')
            
            if 'Fecha creación' not in df_trips.columns:
                fecha_cols = [col for col in df_trips.columns if 'fecha' in col.lower() or 'creacion' in col.lower()]
                if fecha_cols:
                    df_trips['Fecha creación'] = df_trips[fecha_cols[0]]
                else:
                    raise ValueError("No se encontró columna de fecha en df_trips")
            
            df_trips['Fecha creación'] = pd.to_datetime(df_trips['Fecha creación'], errors='coerce')
            comodatos['Fecha creación'] = pd.to_datetime(comodatos['Fecha creación'], errors='coerce')
            
            combined = pd.concat([df_trips, comodatos], ignore_index=True)
            
            if 'Fecha creación' in combined.columns and 'Equipo Motriz' in combined.columns:
                combined = combined.sort_values(['Equipo Motriz', 'Fecha creación'], na_position='last').reset_index(drop=True)
            else:
                combined = combined.reset_index(drop=True)
            
            return combined
            
        except Exception as e:
            print(f"[ERROR] Integrate comodatos: {e}")
            print(f"[DEBUG] df_trips columns: {list(df_trips.columns)}")
            print(f"[DEBUG] comodatos columns: {list(comodatos.columns) if not comodatos.empty else 'Empty'}")
            return df_trips

class ChangeTracker:
    """Rastreador de cambios en operaciones de unidades."""
    
    def __init__(self, log_callback=print):
        self.log_func = log_callback
    
    def track_operation_changes(self, df_cedulas: pd.DataFrame, obj_mapping: Dict = None) -> pd.DataFrame:
        """Detectar cambios de operación cédula por unidad, incluyendo ingresos y egresos."""
        changes = []
        
        # Obtener rango completo de fechas
        fecha_min_global = df_cedulas['Fecha Cedula_dt'].min()
        fecha_max_global = df_cedulas['Fecha Cedula_dt'].max()
        
        total_units = len(df_cedulas['Unidades'].unique())
        units_with_changes = 0
        ingresos = 0
        egresos = 0
        
        for unit in df_cedulas['Unidades'].unique():
            unit_data = df_cedulas[df_cedulas['Unidades'] == unit].sort_values('Fecha Cedula_dt')
            
            unit_changes = self._detect_unit_changes(unit_data, obj_mapping, fecha_min_global, fecha_max_global)
            
            if unit_changes:
                units_with_changes += 1
                # Contar ingresos y egresos
                for change in unit_changes:
                    if change.get('Tipo Cambio') == 'INGRESO':
                        ingresos += 1
                    elif change.get('Tipo Cambio') == 'EGRESO':
                        egresos += 1
                changes.extend(unit_changes)
        
        self.log_func(f"[CHG] Analizadas {total_units} unidades: {ingresos} ingresos, {egresos} egresos, {len(changes)-ingresos-egresos} cambios operacionales")
        
        if changes:
            self.log_func(f"[CHG] Total: {len(changes)} registros de cambios")
            return pd.DataFrame(changes)
        else:
            self.log_func("[CHG] Sin cambios detectados")
            return pd.DataFrame()
    
    def _detect_unit_changes(self, unit_data: pd.DataFrame, obj_mapping: Dict = None, 
                           fecha_min_global: pd.Timestamp = None, fecha_max_global: pd.Timestamp = None) -> List[Dict]:
        """Detectar cambios para una unidad específica: ingresos, egresos y cambios operacionales."""
        changes = []
        
        # Obtener fechas de primera y última aparición
        primera_fecha = unit_data['Fecha Cedula_dt'].min()
        ultima_fecha = unit_data['Fecha Cedula_dt'].max()
        
        # Primera fila para información base
        first_row = unit_data.iloc[0]
        last_row = unit_data.iloc[-1]
        
        # 1. DETECTAR INGRESO (unidad aparece después del inicio del período)
        if fecha_min_global is not None and primera_fecha > fecha_min_global:
            primera_operacion = self._get_operacion_cedula(
                first_row['Operación'], first_row['Circuito'], first_row['Tipo de Unidad']
            )
            tipo_unidad = first_row['Tipo de Unidad']
            
            # Objetivos de la operación de ingreso
            obj_km_ingreso = 0
            obj_viajes_ingreso = 0
            if obj_mapping and primera_operacion in obj_mapping:
                obj_km_ingreso = obj_mapping[primera_operacion].get('Objetivo KM Diario', 0)
                obj_viajes_ingreso = obj_mapping[primera_operacion].get('Objetivo Viajes Diario', 0)
            
            changes.append({
                'Equipo Motriz': str(first_row['Unidades']),
                'Fecha cambio': primera_fecha.strftime("%d/%m/%Y"),
                'Tipo Cambio': 'INGRESO',
                'Operacion inicial': f'POR ASIGNAR {tipo_unidad}',
                'Operacion final': primera_operacion,
                'Gerencia inicial': 'PENDIENTE',
                'Gerencia final': first_row['Gerencia'],
                'Objetivo diario inicial KM': 0,
                'Objetivo diario final KM': obj_km_ingreso,
                'Objetivo diario inicial Viajes': 0,
                'Objetivo diario final Viajes': obj_viajes_ingreso
            })
        
        # 2. DETECTAR CAMBIOS OPERACIONALES — vectorizado con shift()
        ud = unit_data.copy()
        op_up   = ud['Operación'].str.upper()
        circ_up = ud['Circuito'].str.upper()
        tipo_up = ud['Tipo de Unidad'].str.upper()
        ud['_op_ced']  = np.where(circ_up.isin(Config.SPECIAL_CIRCUITS), op_up + ' ' + tipo_up, op_up + ' ' + circ_up)
        ud['_prev_op'] = ud['_op_ced'].shift()
        ud['_prev_ger'] = ud['Gerencia'].shift()

        changed = ud[ud['_prev_op'].notna() & (ud['_op_ced'] != ud['_prev_op'])]
        for _, row in changed.iterrows():
            prev_op = row['_prev_op']
            curr_op = row['_op_ced']
            prev_km  = obj_mapping[prev_op].get('Objetivo KM Diario', 0)  if obj_mapping and prev_op in obj_mapping else 0
            prev_v   = obj_mapping[prev_op].get('Objetivo Viajes Diario', 0) if obj_mapping and prev_op in obj_mapping else 0
            curr_km  = obj_mapping[curr_op].get('Objetivo KM Diario', 0)  if obj_mapping and curr_op in obj_mapping else 0
            curr_v   = obj_mapping[curr_op].get('Objetivo Viajes Diario', 0) if obj_mapping and curr_op in obj_mapping else 0
            changes.append({
                'Equipo Motriz': str(row['Unidades']),
                'Fecha cambio': row['Fecha Cedula_dt'].strftime("%d/%m/%Y"),
                'Tipo Cambio': 'OPERACIONAL',
                'Operacion inicial': prev_op,
                'Operacion final': curr_op,
                'Gerencia inicial': row['_prev_ger'],
                'Gerencia final': row['Gerencia'],
                'Objetivo diario inicial KM': prev_km,
                'Objetivo diario final KM': curr_km,
                'Objetivo diario inicial Viajes': prev_v,
                'Objetivo diario final Viajes': curr_v
            })
        
        # 3. DETECTAR EGRESO (unidad deja de aparecer antes del fin del período)
        if fecha_max_global is not None and ultima_fecha < fecha_max_global:
            ultima_operacion = self._get_operacion_cedula(
                last_row['Operación'], last_row['Circuito'], last_row['Tipo de Unidad']
            )
            tipo_unidad = last_row['Tipo de Unidad']
            
            # Objetivos de la operación de egreso
            obj_km_egreso = 0
            obj_viajes_egreso = 0
            if obj_mapping and ultima_operacion in obj_mapping:
                obj_km_egreso = obj_mapping[ultima_operacion].get('Objetivo KM Diario', 0)
                obj_viajes_egreso = obj_mapping[ultima_operacion].get('Objetivo Viajes Diario', 0)
            
            # Fecha de egreso: día siguiente a la última aparición
            fecha_egreso = ultima_fecha + pd.Timedelta(days=1)
            
            changes.append({
                'Equipo Motriz': str(last_row['Unidades']),
                'Fecha cambio': fecha_egreso.strftime("%d/%m/%Y"),
                'Tipo Cambio': 'EGRESO',
                'Operacion inicial': ultima_operacion,
                'Operacion final': f'POR ASIGNAR {tipo_unidad}',
                'Gerencia inicial': last_row['Gerencia'],
                'Gerencia final': 'PENDIENTE',
                'Objetivo diario inicial KM': obj_km_egreso,
                'Objetivo diario final KM': 0,
                'Objetivo diario inicial Viajes': obj_viajes_egreso,
                'Objetivo diario final Viajes': 0
            })
        
        return changes
    
    def _get_operacion_cedula(self, operacion: str, circuito: str, tipo_unidad: str) -> str:
        """Generar cédula de operación según reglas de negocio."""
        circuito_upper = circuito.upper()
        operacion_upper = operacion.upper()
        tipo_unidad_upper = tipo_unidad.upper()
        
        if circuito_upper in Config.SPECIAL_CIRCUITS:
            return f"{operacion_upper} {tipo_unidad_upper}"
        return f"{operacion_upper} {circuito_upper}"

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
                  cedulas_tab: str = None) -> Optional[Dict]:
        """Cargar y validar archivos de entrada optimizado."""
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
            
            if cedulas_sheet_id:
                df_cedulas = self.load_cedula_from_sheets(cedulas_sheet_id, cedulas_tab)
            else:
                df_cedulas = self.load_daily_cedulas(cedulas_folder)
            if df_cedulas is None:
                return None
            data['cedulas'] = df_cedulas
            
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

        # Cuenta remolques — cuántos remolques lleva este viaje (0, 1 o 2)
        r1_ok = df['Equipo Remolque 1'].notna() & (df['Equipo Remolque 1'].astype(str).str.strip() != '')
        r2_ok = df['Equipo Remolque 2'].notna() & (df['Equipo Remolque 2'].astype(str).str.strip() != '')
        df['Cuenta remolques'] = r1_ok.astype(int) + r2_ok.astype(int)

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
                'Operacion_cedula': r.get('Operación Cedula', ''),
                'Gerencia': r.get('Gerencia', ''),
                'Motrices': int(motrices),
                'Remolques_Unicos': int(r.get('Remolques', 0)),
                'Promedio_Diario_KM_U': promedio
            })
        return pd.DataFrame(rows)

    def upload_to_sheets(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame,
                         df_changes: pd.DataFrame, df_opcedula: pd.DataFrame,
                         df_objectives: pd.DataFrame, df_promedio: pd.DataFrame) -> bool:
        """C: Subir todos los DataFrames a Google Sheets automáticamente."""
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

            write_sheet('Equipos', df_kpi)
            write_sheet('Viajes', df_processed)
            write_sheet('Cambios', df_changes)
            write_sheet('OpCedula', df_opcedula)
            write_sheet('Objetivos', df_objectives)
            write_sheet('PromedioKMunitOps', df_promedio)

            self.log("Google Sheets actualizado correctamente", code="SHEETS")
            return True

        except Exception as e:
            self.log(f"Error Google Sheets: {e}", LogLevel.ERROR, "SHEETS")
            return False

    def save_results(self, df_kpi: pd.DataFrame, df_processed: pd.DataFrame, df_changes: pd.DataFrame,
                     df_opcedula: pd.DataFrame, output_path: str, df_objectives: pd.DataFrame = None,
                     df_promedio: pd.DataFrame = None, upload_sheets: bool = True) -> Optional[str]:
        """Generar archivo Excel + subida automática a Google Sheets."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"KPIs_Transport_{timestamp}.xlsx"
            full_path = Path(output_path) / filename

            df_processed_formatted = df_processed.copy()
            if 'Fecha creación' in df_processed_formatted.columns:
                df_processed_formatted['Fecha creación'] = df_processed_formatted['Fecha creación'].dt.strftime("%d/%m/%Y")
            
            with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
                df_kpi.to_excel(writer, sheet_name='KPIs per Equipment', index=False)
                df_processed_formatted.to_excel(writer, sheet_name='Trip Data', index=False)
                
                if not df_changes.empty:
                    df_changes.to_excel(writer, sheet_name='Resumen de Cambios', index=False)
                    self.log(f"Hoja Resumen Cambios: {len(df_changes)} cambios", code="CHG")
                else:
                    self.log("Hoja Resumen Cambios: Sin cambios operacionales para reportar", code="CHG")

                if not df_opcedula.empty:
                    df_opcedula.to_excel(writer, sheet_name='KPIs OpCedula', index=False)
                    self.log(f"Hoja KPIs OpCedula: {len(df_opcedula)} operaciones", code="OPCED")
                else:
                    self.log("Hoja KPIs OpCedula: Sin operaciones para reportar", code="OPCED")

                # A: Hojas extra que antes vivían en Google Sheets
                if df_objectives is not None and not df_objectives.empty:
                    df_objectives.to_excel(writer, sheet_name='Objetivos', index=False)
                if df_promedio is not None and not df_promedio.empty:
                    df_promedio.to_excel(writer, sheet_name='PromedioKMunitOps', index=False)

                self._format_excel_columns(writer)

            self.log(f"Archivo: {filename}", code="SAVE")

            # C: Subida automática a Google Sheets
            if upload_sheets:
                self.upload_to_sheets(df_kpi, df_processed_formatted, df_changes, df_opcedula,
                                      df_objectives if df_objectives is not None else pd.DataFrame(),
                                      df_promedio if df_promedio is not None else pd.DataFrame())

            return str(full_path)

        except Exception as e:
            self.log(f"Error generación archivo: {e}", LogLevel.ERROR, "ERR")
            return None

    def _format_excel_columns(self, writer):
        """Formatear columnas de Excel de manera eficiente."""
        try:
            for sheet_name in ['KPIs per Equipment', 'Trip Data', 'Resumen de Cambios', 'KPIs OpCedula',
                                'Objetivos', 'PromedioKMunitOps']:
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

    def generate_report(self, trips_file: str, fuel_file: str, cedulas_folder: str, output_path: str, objectives_file: str = None) -> Optional[str]:
        """Ejecutar proceso completo optimizado con comodatos, cambios y OpCedula."""
        try:
            self.log("=== INICIO PROCESO KPI ===", code="START")
            
            data = self.load_data(trips_file, fuel_file, cedulas_folder, objectives_file)
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

class ScrollableFrame:
    """Componente de interfaz para contenido scrolleable."""
    def __init__(self, parent):
        self.main_frame = tk.Frame(parent)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        self.canvas = tk.Canvas(self.main_frame, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.scrollable_frame = tk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.scrollable_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        
    def _on_canvas_configure(self, event):
        canvas_width = event.width
        self.canvas.itemconfig(self.canvas_window, width=canvas_width)
        
    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

class KPIGeneratorGUI:
    """Interfaz gráfica optimizada para generación de reportes KPI con comodatos, cambios y OpCedula."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("KPI Generator v12.2 - OpCedula Period-Aware + Google Sheets")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        self.setup_professional_theme()
        
        self.paths = {
            "trips": tk.StringVar(),
            "fuel": tk.StringVar(), 
            "cedulas": tk.StringVar(),
            "objectives": tk.StringVar(),
            "output": tk.StringVar()
        }
        
        self.processor = DataProcessor(self.log, LogLevel.INFO)
        self.setup_ui()
    
    def setup_professional_theme(self):
        """Configurar tema visual."""
        self.colors = {
            'bg_primary': '#1a1d29',
            'bg_secondary': '#252836',
            'bg_card': '#2d3142',
            'accent_primary': '#6366f1',
            'accent_secondary': '#ec4899',
            'accent_success': '#10b981',
            'accent_info': '#06b6d4',
            'text_primary': '#ffffff',
            'text_secondary': '#9ca3af',
            'border': '#374151'
        }
        
        self.root.configure(bg=self.colors['bg_primary'])
        
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('Vertical.TScrollbar',
                       background=self.colors['bg_secondary'],
                       troughcolor=self.colors['bg_card'],
                       borderwidth=0,
                       arrowcolor=self.colors['text_secondary'])
        
        style.configure('Professional.Horizontal.TProgressbar',
                       background=self.colors['accent_info'],
                       troughcolor=self.colors['bg_secondary'],
                       borderwidth=0,
                       lightcolor=self.colors['accent_info'],
                       darkcolor=self.colors['accent_info'])
    
    def setup_ui(self):
        """Configurar interfaz de usuario completa."""
        self.scroll_frame = ScrollableFrame(self.root)
        
        main_container = tk.Frame(self.scroll_frame.scrollable_frame, bg=self.colors['bg_primary'], padx=30, pady=25)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        header_frame = tk.Frame(main_container, bg=self.colors['bg_primary'], height=80)
        header_frame.pack(fill="x", pady=(0, 25))
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(header_frame,
                              text="KPI Generator v12.2",
                              bg=self.colors['bg_primary'],
                              fg=self.colors['text_primary'],
                              font=('Segoe UI', 28, 'bold'))
        title_label.pack(side="left", pady=15)
        
        subtitle_label = tk.Label(header_frame,
                                 text="Con OpCedula, Ingresos y Egresos",
                                 bg=self.colors['bg_primary'],
                                 fg=self.colors['text_secondary'],
                                 font=('Segoe UI', 12))
        subtitle_label.pack(side="left", padx=(15, 0), pady=20)
        
        files_card = self.create_card_frame(main_container, 
                                          "Configuración de Fuentes de Datos", 
                                          "Seleccione los archivos y carpetas requeridos")
        files_card.pack(fill="x", pady=(0, 15))
        
        file_configs = [
            ("Viajes", "trips", "🚚"),
            ("Combustible", "fuel", "⛽"),
            ("Cédulas", "cedulas", "📅"),
            ("Objetivos", "objectives", "🎯"),
            ("Directorio Salida", "output", "💾")
        ]
        
        for label, key, icon in file_configs:
            self.create_file_row(files_card, label, key, icon)
        
        log_card = self.create_card_frame(main_container, 
                                        "Monitor del Sistema", 
                                        "Seguimiento con códigos simplificados")
        log_card.pack(fill="x", pady=(0, 15))
        
        log_control_frame = tk.Frame(log_card, bg=self.colors['bg_card'])
        log_control_frame.pack(fill="x", padx=5, pady=(0, 10))
        
        tk.Label(log_control_frame, text="Nivel de Log:", 
                bg=self.colors['bg_card'], fg=self.colors['text_primary'],
                font=('Segoe UI', 10)).pack(side="left")
        
        self.log_level_var = tk.StringVar(value="INFO")
        log_combo = ttk.Combobox(log_control_frame, textvariable=self.log_level_var,
                               values=["ERROR", "INFO", "DEBUG"], state="readonly", width=10)
        log_combo.pack(side="left", padx=(10, 0))
        log_combo.bind("<<ComboboxSelected>>", self.change_log_level)
        
        log_container = tk.Frame(log_card, bg=self.colors['bg_secondary'])
        log_container.pack(fill="x", padx=5, pady=5)
        
        text_frame = tk.Frame(log_container, bg=self.colors['bg_secondary'])
        text_frame.pack(fill="x", padx=10, pady=10)
        
        self.log_text = tk.Text(text_frame,
                               bg=self.colors['bg_secondary'],
                               fg=self.colors['text_primary'],
                               font=('Consolas', 9),
                               border=0,
                               wrap=tk.WORD,
                               height=12,
                               insertbackground=self.colors['accent_info'])
        
        log_scrollbar = tk.Scrollbar(text_frame, 
                                   command=self.log_text.yview,
                                   bg=self.colors['bg_secondary'],
                                   troughcolor=self.colors['bg_card'],
                                   activebackground=self.colors['accent_primary'])
        
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scrollbar.pack(side="right", fill="y")
        
        self.setup_control_panel()
        
        self.log("Sistema KPI Generator v11 iniciado")
    
    def change_log_level(self, event=None):
        """Cambiar nivel de logging dinámicamente."""
        level_map = {"ERROR": LogLevel.ERROR, "INFO": LogLevel.INFO, "DEBUG": LogLevel.DEBUG}
        self.processor.log_level = level_map[self.log_level_var.get()]
        self.log(f"[CFG] Nivel de log: {self.log_level_var.get()}")
    
    def create_card_frame(self, parent, title, subtitle=""):
        """Crear componente de tarjeta profesional."""
        card_frame = tk.Frame(parent, bg=self.colors['bg_card'], pady=15, padx=20)
        
        header_frame = tk.Frame(card_frame, bg=self.colors['bg_card'])
        header_frame.pack(fill="x", pady=(0, 15))
        
        title_label = tk.Label(header_frame, 
                              text=title,
                              bg=self.colors['bg_card'],
                              fg=self.colors['text_primary'],
                              font=('Segoe UI', 14, 'bold'))
        title_label.pack(anchor="w")
        
        if subtitle:
            subtitle_label = tk.Label(header_frame,
                                    text=subtitle,
                                    bg=self.colors['bg_card'],
                                    fg=self.colors['text_secondary'],
                                    font=('Segoe UI', 9))
            subtitle_label.pack(anchor="w")
        
        return card_frame
    
    def create_file_row(self, parent, label_text, key, icon="📁"):
        """Crear fila de selección de archivo."""
        row_frame = tk.Frame(parent, bg=self.colors['bg_card'], pady=8)
        row_frame.pack(fill="x", pady=3)
        
        label_frame = tk.Frame(row_frame, bg=self.colors['bg_card'])
        label_frame.pack(side="left", padx=(0, 15))
        
        icon_label = tk.Label(label_frame, 
                             text=icon,
                             bg=self.colors['bg_card'],
                             font=('Segoe UI', 12))
        icon_label.pack(side="left", padx=(0, 8))
        
        text_label = tk.Label(label_frame,
                             text=label_text,
                             bg=self.colors['bg_card'],
                             fg=self.colors['text_primary'],
                             font=('Segoe UI', 10),
                             width=12,
                             anchor="w")
        text_label.pack(side="left")
        
        entry_frame = tk.Frame(row_frame, bg=self.colors['bg_secondary'], height=35)
        entry_frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
        entry_frame.pack_propagate(False)
        
        entry = tk.Entry(entry_frame,
                        textvariable=self.paths[key],
                        bg=self.colors['bg_secondary'],
                        fg=self.colors['text_primary'],
                        border=0,
                        font=('Segoe UI', 10),
                        insertbackground=self.colors['text_primary'])
        entry.pack(fill="both", padx=12, pady=8)
        
        if key in ["output", "cedulas"]:
            cmd = self.select_folder
        else:
            cmd = lambda k=key: self.select_file(k)
        
        btn_frame = tk.Frame(row_frame, bg=self.colors['accent_primary'], height=35, width=80)
        btn_frame.pack(side="right")
        btn_frame.pack_propagate(False)
        
        btn = tk.Button(btn_frame,
                       text="Buscar",
                       command=cmd,
                       bg=self.colors['accent_primary'],
                       fg='white',
                       border=0,
                       relief='flat',
                       font=('Segoe UI', 9, 'bold'),
                       cursor='hand2',
                       activebackground=self.colors['accent_secondary'])
        btn.pack(fill="both", expand=True)
        
        return row_frame
    
    def setup_control_panel(self):
        """Configurar panel de control optimizado."""
        self.controls_frame = tk.Frame(self.root, bg=self.colors['bg_card'], pady=15)
        self.controls_frame.pack(side="bottom", fill="x")
        
        progress_container = tk.Frame(self.controls_frame, bg=self.colors['bg_card'])
        progress_container.pack(fill="x", padx=50, pady=(0, 15))
        
        self.progress = ttk.Progressbar(progress_container,
                                      mode='indeterminate',
                                      length=500,
                                      style='Professional.Horizontal.TProgressbar')
        self.progress.pack()
        
        buttons_frame = tk.Frame(self.controls_frame, bg=self.colors['bg_card'])
        buttons_frame.pack()
        
        self.process_btn = tk.Button(buttons_frame,
                                   text="🚀 EJECUTAR ANÁLISIS",
                                   command=self.start_processing,
                                   bg=self.colors['accent_success'],
                                   fg='white',
                                   font=('Segoe UI', 11, 'bold'),
                                   border=0,
                                   relief='flat',
                                   padx=25,
                                   pady=10,
                                   cursor='hand2',
                                   activebackground='#059669')
        self.process_btn.pack(side="left", padx=5)
        
        self.clear_cache_btn = tk.Button(buttons_frame,
                                       text="🗑️ LIMPIAR CACHE",
                                       command=self.clear_cache,
                                       bg=self.colors['accent_info'],
                                       fg='white',
                                       font=('Segoe UI', 11, 'bold'),
                                       border=0,
                                       relief='flat',
                                       padx=25,
                                       pady=10,
                                       cursor='hand2',
                                       activebackground='#0891b2')
        self.clear_cache_btn.pack(side="left", padx=5)
        
        self.clear_btn = tk.Button(buttons_frame,
                                 text="🔄 RESETEAR",
                                 command=self.clear_all,
                                 bg=self.colors['accent_info'],
                                 fg='white',
                                 font=('Segoe UI', 11, 'bold'),
                                 border=0,
                                 relief='flat',
                                 padx=25,
                                 pady=10,
                                 cursor='hand2',
                                 activebackground='#0891b2')
        self.clear_btn.pack(side="left", padx=5)
        
        self.close_btn = tk.Button(buttons_frame,
                                 text="❌ SALIR",
                                 command=self.close_application,
                                 bg=self.colors['accent_secondary'],
                                 fg='white',
                                 font=('Segoe UI', 11, 'bold'),
                                   border=0,
                                 relief='flat',
                                 padx=25,
                                 pady=10,
                                 cursor='hand2',
                                 activebackground='#dc2626')
        self.close_btn.pack(side="left", padx=5)
    
    def clear_cache(self):
        """Limpiar cache del procesador."""
        self.processor._get_operacion_cedula.cache_clear()
        self.processor._parse_cedula_filename.cache_clear()
        self.processor._get_daily_objective.cache_clear()
        self.processor._objective_cache.clear()
        self.processor._cedula_cache.clear()
        self.log("[CACHE] Cache limpiado")
    
    def select_file(self, key: str):
        """Seleccionar archivo de entrada."""
        filename = filedialog.askopenfilename(
            title=f"Seleccionar archivo de {key}",
            filetypes=[("Excel", "*.xlsx *.xls")]
        )
        if filename:
            self.paths[key].set(filename)
            self.log(f"[FILE] {key}: {Path(filename).name}")
    
    def select_folder(self):
        """Seleccionar directorio o carpeta."""
        folder = filedialog.askdirectory(title="Seleccionar directorio")
        if folder:
            if not self.paths["cedulas"].get():
                self.paths["cedulas"].set(folder)
                self.log(f"[FOLDER] Cédulas: {folder}")
            else:
                self.paths["output"].set(folder)
                self.log(f"[FOLDER] Salida: {folder}")
    
    def clear_all(self):
        """Limpiar configuración y registro."""
        for path_var in self.paths.values():
            path_var.set("")
        
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        
        self.clear_cache()
        self.log("[RESET] Sistema reseteado")
    
    def close_application(self):
        """Cerrar aplicación."""
        if messagebox.askokcancel("Confirmar Cierre", "¿Confirma el cierre del sistema?"):
            self.log("[EXIT] Sistema cerrado")
            self.root.destroy()
    
    def log(self, message: str):
        """Registrar evento en el monitor del sistema con códigos."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if any(keyword in message.lower() for keyword in ["[err]", "error", "crítico"]):
            color = self.colors['accent_secondary']
            prefix = "🚨"
        elif any(keyword in message.lower() for keyword in ["[ok]", "completado", "exitoso", "generado"]):
            color = self.colors['accent_success']
            prefix = "✅"
        elif any(keyword in message.lower() for keyword in ["[proc]", "[load]", "[kpi]", "procesando"]):
            color = self.colors['accent_info']
            prefix = "⚙️"
        elif any(keyword in message.lower() for keyword in ["[com]", "comodato"]):
            color = '#f59e0b'
            prefix = "📦"
        elif any(keyword in message.lower() for keyword in ["[chg]", "cambio"]):
            color = '#8b5cf6'
            prefix = "🔄"
        elif any(keyword in message.lower() for keyword in ["[opced]", "opcedula"]):
            color = '#06b6d4'
            prefix = "📊"
        elif any(keyword in message.lower() for keyword in ["[phantom]", "fantasma"]):
            color = '#a855f7'
            prefix = "👻"
        else:
            color = self.colors['text_primary']
            prefix = "ℹ️"
        
        self.log_text.config(state=tk.NORMAL)
        
        self.log_text.insert(tk.END, f"[{timestamp}] ", 'timestamp')
        self.log_text.tag_config('timestamp', foreground=self.colors['text_secondary'])
        
        self.log_text.insert(tk.END, f"{prefix} ", 'prefix')
        self.log_text.tag_config('prefix', foreground=color)
        
        self.log_text.insert(tk.END, f"{message}\n", 'message')
        self.log_text.tag_config('message', foreground=color)
        
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update_idletasks()
    
    def validate_inputs(self) -> bool:
        """Validar configuración de entrada."""
        required_fields = ['trips', 'fuel', 'cedulas', 'output']
        
        for key in required_fields:
            if not self.paths[key].get().strip():
                messagebox.showerror("Error de Validación", f"Debe seleccionar: {key.title()}")
                return False
        
        for key in ['trips', 'fuel']:
            if not Path(self.paths[key].get()).exists():
                messagebox.showerror("Error de Archivo", f"El archivo {key} no existe")
                return False
        
        if not Path(self.paths["cedulas"].get()).is_dir():
            messagebox.showerror("Error de Carpeta", "La carpeta de cédulas no es válida")
            return False
        
        objectives_path = self.paths["objectives"].get().strip()
        if objectives_path and not Path(objectives_path).exists():
            messagebox.showerror("Error de Archivo", "El archivo de objetivos no existe")
            return False
        
        if not Path(self.paths["output"].get()).is_dir():
            messagebox.showerror("Error de Directorio", "El directorio de salida no es válido")
            return False
                
        return True
    
    def start_processing(self):
        """Iniciar proceso de análisis."""
        if not self.validate_inputs():
            return
        
        self.process_btn.config(state="disabled", text="⏳ PROCESANDO...")
        self.progress.start(10)
        
        threading.Thread(target=self.process_data, daemon=True).start()
    
    def process_data(self):
        """Ejecutar procesamiento de datos en hilo separado."""
        try:
            objectives_file = self.paths["objectives"].get().strip()
            objectives_file = objectives_file if objectives_file else None
            
            result = self.processor.generate_report(
                self.paths["trips"].get(),
                self.paths["fuel"].get(),
                self.paths["cedulas"].get(),
                self.paths["output"].get(),
                objectives_file
            )
            
            self.root.after(0, self.processing_complete, result)
            
        except Exception as e:
            self.root.after(0, self.processing_error, str(e))
    
    def processing_complete(self, result: Optional[str]):
        """Manejar finalización exitosa del procesamiento."""
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        
        if result:
            self.log("[SUCCESS] Análisis completado")
            if messagebox.askyesno("Proceso Completado", 
                                 f"Reporte generado:\n{Path(result).name}\n\n¿Desea abrir el archivo?"):
                self.open_file(result)
        else:
            messagebox.showerror("Error de Procesamiento", "Error durante el análisis")
    
    def processing_error(self, error: str):
        """Manejar errores durante el procesamiento."""
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        self.log(f"[ERR] Error: {error}")
        messagebox.showerror("Error del Sistema", f"Error crítico: {error}")
    
    def open_file(self, file_path: str):
        """Abrir archivo generado en el sistema."""
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)
            elif system == "Darwin":
                subprocess.run(["open", file_path])
            else:
                subprocess.run(["xdg-open", file_path])
        except Exception as e:
            self.log(f"[ERR] Error abriendo archivo: {e}")
    
    def run(self):
        """Iniciar aplicación."""
        self.log("[START] KPI Generator v11 iniciado")
        self.root.mainloop()

if __name__ == "__main__":
    """Punto de entrada principal del sistema optimizado con resumen de cambios y OpCedula."""
    app = KPIGeneratorGUI()
    app.run()

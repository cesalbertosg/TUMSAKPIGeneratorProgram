"""I/O Google Sheets: carga de cedula horizontal y sync del workbook KPI.

Funciones puras extraidas de `DataProcessor` (v0.4.2 → v0.4.3) para aislar
acceso a la API de Google Sheets del motor de calculo. Reciben
`log_callback` para respetar el sistema de logging del caller.

Publico:
- `load_cedula_from_sheet(sheet_id, log_callback, tab_name)` -> DataFrame | None
- `sync_workbook_to_sheets(sheets_id, dfs, log_callback)` -> bool

Nota: ambos importan `google-auth` + `gspread`. Para tests unitarios que no
toquen la red, mockear `gspread.authorize` o `Credentials.from_service_account_file`.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from kpi_generator.config import Config, LogLevel
from kpi_generator.io.excel import fill_missing_dates

LogCallback = Callable[..., None]


def load_cedula_from_sheet(sheet_id: str, log: LogCallback,
                           tab_name: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Cargar cedula mensual desde Google Sheets (formato horizontal) y devolverla
    en formato vertical (una fila por unidad+dia) compatible con el pipeline.

    El sheet de cedula tiene una columna por dia (encabezado DD/MM/YYYY) y una
    fila por unidad. Esta funcion:
    1. Localiza el header (fila con 'Unidad' y 'Gerencia').
    2. Extrae columnas-fecha (regex DD/MM/YYYY) y columnas-metadato.
    3. Filtra filas que parecen ID de unidad (regex `[A-Za-z][A-Za-z0-9]+`).
    4. Genera registros (unidad, fecha, operando) por cada celda dia.
    5. Forward-fill por unidad (celda vacia hereda del dia anterior).
    6. Rellena fechas globalmente ausentes via `fill_missing_dates`.

    Devuelve `None` ante cualquier error de conexion, parseo o estructura.
    """
    date_col_re = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    unit_id_re = re.compile(r'^[A-Za-z][A-Za-z0-9]+$')  # C070, T317, FL7, etc.
    subtotal_re = re.compile(r'^\d+\s+al\s+\d+|en\s+adelante', re.IGNORECASE)
    skip_col_names = {'Taller', 'Gestoría', 'Sin operador', 'Sin Operador', ''}

    try:
        log("Conectando a Google Sheets para cédula", code="LOAD")
        creds = Credentials.from_service_account_file(
            Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)

        if tab_name is None:
            tab_name = sh.worksheets()[0].title
            log(f"Tab seleccionado: {tab_name}", LogLevel.DEBUG, "LOAD")

        ws = sh.worksheet(tab_name)
        all_rows = ws.get_all_values()

        if not all_rows:
            log("Sheet vacía", LogLevel.ERROR, "ERR")
            return None

        # Header principal: fila con 'Unidad' y 'Gerencia'
        header_idx = next(
            (i for i, row in enumerate(all_rows) if 'Unidad' in row and 'Gerencia' in row),
            None,
        )
        if header_idx is None:
            log("Encabezado de cédula no encontrado en el sheet", LogLevel.ERROR, "ERR")
            return None

        header = all_rows[header_idx]
        data_rows = all_rows[header_idx + 1:]

        date_col_indices = [i for i, h in enumerate(header) if date_col_re.match(h)]
        if not date_col_indices:
            log("Sin columnas de fecha en el sheet", LogLevel.ERROR, "ERR")
            return None

        unit_col_idx = header.index('Unidad')

        unit_rows = [
            row for row in data_rows
            if len(row) > unit_col_idx and unit_id_re.match(row[unit_col_idx].strip())
        ]
        if not unit_rows:
            log("Sin unidades válidas en el sheet", LogLevel.ERROR, "ERR")
            return None

        meta_col_indices = [
            i for i, h in enumerate(header)
            if not date_col_re.match(h.strip())
            and not subtotal_re.match(h.strip())
            and h.strip() not in skip_col_names
        ]

        records = []
        for row in unit_rows:
            padded = row + [''] * max(0, len(header) - len(row))
            meta = {
                Config.CEDULA_COLUMN_ALIASES.get(header[i], header[i]): padded[i].strip()
                for i in meta_col_indices
            }
            for col_idx in date_col_indices:
                records.append({
                    **meta,
                    'Fecha Cedula': header[col_idx],
                    'Operando': padded[col_idx].strip() if col_idx < len(padded) else '',
                })

        df = pd.DataFrame(records)
        df = df.rename(columns={'Unidad': 'Unidades'})
        df['Unidades'] = df['Unidades'].str.strip().str.upper()
        df['Fecha Cedula_dt'] = pd.to_datetime(df['Fecha Cedula'], dayfirst=True)
        df = df.sort_values(['Unidades', 'Fecha Cedula_dt'])

        # Forward-fill por unidad: celda vacia hereda del dia anterior
        df['Operando'] = (
            df.groupby('Unidades')['Operando']
            .transform(lambda s: s.replace('', None).ffill())
            .fillna('Desconocido')
        )

        df = fill_missing_dates(df)

        log(
            f"Cédula Sheets: {df['Unidades'].nunique()} unidades, "
            f"{df['Fecha Cedula_dt'].nunique()} días",
            code="OK",
        )
        return df

    except Exception as e:
        log(f"Error carga cédula desde Sheets: {e}", LogLevel.ERROR, "ERR")
        return None


def sync_workbook_to_sheets(sheets_id: str, dfs: dict[str, pd.DataFrame],
                            log: LogCallback) -> bool:
    """Sube todos los DataFrames a Google Sheets, un tab por entry de `dfs`.

    `dfs` es un dict `{nombre_tab: dataframe}`. Cada DataFrame se sobreescribe
    completamente (clear + update) o se crea como tab nuevo si no existia.
    DataFrames vacios o `None` se omiten silenciosamente.

    Devuelve `True` si todo OK, `False` ante cualquier excepcion (loggea el error).
    """
    try:
        log("Conectando a Google Sheets...", code="SHEETS")
        creds = Credentials.from_service_account_file(
            Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheets_id)

        for tab_name, df in dfs.items():
            if df is None or df.empty:
                continue
            df_str = df.fillna('').astype(str)
            data = [df_str.columns.tolist()] + df_str.values.tolist()
            try:
                ws = sh.worksheet(tab_name)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=tab_name, rows=len(df) + 2, cols=len(df.columns) + 1)
            ws.clear()
            ws.update(data, value_input_option='USER_ENTERED')
            log(f"Sheets '{tab_name}': {len(df)} filas", code="SHEETS")

        log("Google Sheets actualizado correctamente", code="SHEETS")
        return True

    except Exception as e:
        log(f"Error Google Sheets: {e}", LogLevel.ERROR, "SHEETS")
        return False

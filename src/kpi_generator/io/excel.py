"""I/O Excel: carga de cedulas locales y escritura del workbook KPI.

Funciones puras extraidas de `DataProcessor` (v0.4.2 → v0.4.3) para aislar
acceso al filesystem del motor de calculo. Reciben `log_callback` para
respetar el sistema de logging del caller.

Publico:
- `parse_cedula_filename(filename)` -> datetime | None
- `fill_missing_dates(df_cedulas)` -> DataFrame (forward-fill por fecha)
- `load_daily_cedulas(folder, log_callback)` -> DataFrame | None
- `write_workbook(dfs, output_dir, log_callback)` -> Path (archivo generado)
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from kpi_generator.config import Config, LogLevel

# Tipo del callback de logging usado por las funciones de este modulo.
# Firma: log(message: str, level: LogLevel = INFO, code: str | None = None)
LogCallback = Callable[..., None]


@lru_cache(maxsize=128)
def parse_cedula_filename(filename: str) -> Optional[datetime]:
    """Extraer fecha del nombre de archivo de cedula.

    Reconoce los formatos historicos del proyecto:
    - `Cedula DDMMYYYY.xlsx` (canonico)
    - `Cedula D M YYYY.xlsx` (separadores con espacios)
    - `Cédula DDMMYYYY.xlsx` (con tilde)
    - `.xls` legacy.

    Devuelve `None` si el archivo no coincide con ningun patron o si la
    fecha extraida no es valida (mes 13, dia 32, etc.).
    """
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


def fill_missing_dates(df_cedulas: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill por fecha sobre el rango completo del DataFrame de cedulas.

    Para cada dia ausente en el rango [min, max], replica el snapshot del dia
    anterior mas cercano (todas las unidades, mismas asignaciones).
    """
    date_range = pd.date_range(
        start=df_cedulas['Fecha Cedula_dt'].min(),
        end=df_cedulas['Fecha Cedula_dt'].max(),
        freq='D',
    )
    existing_sorted = sorted(set(df_cedulas['Fecha Cedula_dt']))
    existing_set = set(existing_sorted)
    missing_dates = sorted(d for d in date_range if d not in existing_set)

    if not missing_dates:
        return df_cedulas

    # Snapshot por fecha existente — solo se lee una vez por fecha
    snapshots = {d: df_cedulas[df_cedulas['Fecha Cedula_dt'] == d] for d in existing_sorted}

    fill_frames = [df_cedulas]
    ptr = 0
    for missing_date in missing_dates:
        # Avanzar puntero hasta la fecha anterior mas cercana (O(n) total)
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


def load_daily_cedulas(cedulas_folder: str, log: LogCallback) -> Optional[pd.DataFrame]:
    """Cargar y consolidar cedulas diarias desde una carpeta de .xlsx.

    Cada archivo `Cedula DDMMYYYY.xlsx` aporta una fecha. Las fechas ausentes
    en el rango se rellenan con `fill_missing_dates`.

    Devuelve `None` ante cualquier error (carpeta invalida, archivos con
    formato no reconocido, columnas faltantes, etc.) — el caller debe
    interpretar `None` como fallo y consultar el log.
    """
    try:
        log("Cargando cédulas", code="LOAD")
        folder_path = Path(cedulas_folder)

        if not folder_path.exists() or not folder_path.is_dir():
            log("Carpeta cédulas inválida", LogLevel.ERROR, "ERR")
            return None

        cedula_files = [
            (file_path, parse_cedula_filename(file_path.name))
            for file_path in folder_path.glob("*.xlsx")
        ]

        valid_files = [(f, d) for f, d in cedula_files if d is not None]
        invalid_files = [f.name for f, d in cedula_files if d is None]

        if invalid_files:
            log(f"Archivos formato inválido: {len(invalid_files)}", LogLevel.ERROR, "ERR")
            return None

        if not valid_files:
            log("Sin archivos válidos", LogLevel.ERROR, "ERR")
            return None

        valid_files.sort(key=lambda x: x[1])

        consolidated_cedulas = []
        required_cols = Config.COLUMNS["units"]

        for file_path, fecha in valid_files:
            try:
                df = pd.read_excel(file_path)

                if not all(col in df.columns for col in required_cols):
                    missing = [col for col in required_cols if col not in df.columns]
                    log(f"Columnas faltantes en {file_path.name}: {missing}",
                        LogLevel.ERROR, "ERR")
                    return None

                df['Fecha Cedula'] = fecha.strftime("%d/%m/%Y")
                df['Fecha Cedula_dt'] = fecha
                consolidated_cedulas.append(df)

            except Exception as e:
                log(f"Error procesando {file_path.name}: {e}", LogLevel.ERROR, "ERR")
                return None

        df_cedulas = pd.concat(consolidated_cedulas, ignore_index=True)
        df_cedulas = fill_missing_dates(df_cedulas)

        log(f"Cédulas: {len(df_cedulas)} registros", code="OK")
        return df_cedulas

    except Exception as e:
        log(f"Error carga cédulas: {e}", LogLevel.ERROR, "ERR")
        return None


def _format_excel_columns(writer, sheet_names: list[str]) -> None:
    """Auto-ajusta el ancho de columnas (max 50 chars) en cada hoja del workbook."""
    try:
        for sheet_name in sheet_names:
            if sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = min(max(len(str(cell.value or '')) for cell in column) + 2, 50)
                    worksheet.column_dimensions[column[0].column_letter].width = max_length
    except Exception:
        # El auto-ajuste es cosmetico: si falla, el archivo igual se guarda.
        pass


def write_workbook(dfs: dict[str, Optional[pd.DataFrame]], output_dir: str,
                   log: LogCallback) -> Optional[Path]:
    """Escribir un workbook KPI con timestamp al directorio dado.

    `dfs` es un dict ordenado `{nombre_hoja: DataFrame}`. El orden del dict
    determina el orden de las pestañas. DataFrames `None` o vacios se omiten
    (la hoja simplemente no se crea).

    El nombre del archivo es `KPIs_Transport_YYYYMMDD_HHMMSS.xlsx` y se
    crea dentro de `output_dir`.

    Devuelve el `Path` del archivo creado, o `None` ante error de I/O.
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"KPIs_Transport_{timestamp}.xlsx"
        full_path = Path(output_dir) / filename

        with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
            written_sheets: list[str] = []
            for sheet_name, df in dfs.items():
                if df is None or df.empty:
                    continue
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                written_sheets.append(sheet_name)
            _format_excel_columns(writer, written_sheets)

        log(f"Archivo: {filename}", code="SAVE")
        return full_path

    except Exception as e:
        log(f"Error generación archivo: {e}", LogLevel.ERROR, "ERR")
        return None

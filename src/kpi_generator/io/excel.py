"""I/O Excel: carga de cedulas locales y escritura del workbook KPI.

Funciones puras extraidas de `DataProcessor` (v0.4.2 → v0.4.3) para aislar
acceso al filesystem del motor de calculo. Reciben `log_callback` para
respetar el sistema de logging del caller.

Publico:
- `parse_cedula_filename(filename)` -> datetime | None
- `fill_missing_dates(df_cedulas)` -> DataFrame (forward-fill por fecha)
- `load_daily_cedulas(folder, log_callback)` -> DataFrame | None
- `save_cedula_as_completa(df_cedulas, folder, log_callback)` -> None
- `load_local_cedulas_for_crossfill(folder, log_callback)` -> DataFrame
- `crossfill_cedulas(df_primary, df_local, log_callback)` -> (DataFrame, list)
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

    Acepta sufijos despues de la fecha (ej. "Cedula 01062026 Completa.xlsx"),
    formato que Beto guarda manualmente con info extra de operadores.
    """
    suffix = r'(?:\s+[\wÀ-ÿ]+)*\.xlsx?'
    patterns = [
        r'cedula\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})' + suffix,
        r'c[eé]dula\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})' + suffix,
        r'cedula\s*(\d{2})(\d{2})(\d{4})' + suffix,
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

                for src, dst in Config.CEDULA_COLUMN_ALIASES.items():
                    if src in df.columns and dst not in df.columns:
                        df = df.rename(columns={src: dst})

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


def save_cedula_as_completa(df_cedulas: pd.DataFrame, cedulas_folder: str, log: LogCallback) -> None:
    """Respaldo local de la cédula obtenida vía Sheets.

    Por cada fecha única en `df_cedulas`, si la carpeta no tiene ya un
    archivo cuyo `parse_cedula_filename` resuelva a esa fecha, escribe
    `Cedula DDMMYYYY Completa.xlsx`. Nunca sobrescribe archivos existentes
    (preserva ediciones manuales). Cualquier error se loggea y no aborta
    el pipeline.
    """
    try:
        folder_path = Path(cedulas_folder)
        if not folder_path.exists() or not folder_path.is_dir():
            log("Carpeta cédulas inválida para respaldo local", LogLevel.ERROR, "ERR")
            return

        existing_dates = {
            parse_cedula_filename(f.name) for f in folder_path.glob("*.xlsx")
        }
        existing_dates.discard(None)

        cols = [
            c for c in Config.COLUMNS["units"] + Config.COLUMNS["units_extra"]
            if c in df_cedulas.columns
        ]

        for fecha in sorted(df_cedulas['Fecha Cedula_dt'].unique()):
            fecha_dt = pd.Timestamp(fecha).to_pydatetime()
            if fecha_dt in existing_dates:
                continue

            df_dia = df_cedulas[df_cedulas['Fecha Cedula_dt'] == fecha][cols]
            filename = f"Cedula {fecha_dt.strftime('%d%m%Y')} Completa.xlsx"
            df_dia.to_excel(folder_path / filename, engine='openpyxl', index=False)
            log(f"Respaldo cédula local: {filename}", code="SAVE")

    except Exception as e:
        log(f"Error respaldo local cédula: {e}", LogLevel.ERROR, "ERR")


def load_local_cedulas_for_crossfill(cedulas_folder: str, log: LogCallback) -> pd.DataFrame:
    """Variante best-effort de `load_daily_cedulas` para cruce de información.

    A diferencia de `load_daily_cedulas`, cualquier problema (carpeta
    inválida, sin archivos válidos, columnas faltantes) devuelve un
    DataFrame vacío con un log informativo — es información complementaria
    opcional, no una fuente requerida.
    """
    try:
        folder_path = Path(cedulas_folder)
        if not folder_path.exists() or not folder_path.is_dir():
            return pd.DataFrame()

        cedula_files = [
            (file_path, parse_cedula_filename(file_path.name))
            for file_path in folder_path.glob("*.xlsx")
        ]
        valid_files = [(f, d) for f, d in cedula_files if d is not None]
        if not valid_files:
            return pd.DataFrame()

        required_cols = Config.COLUMNS["units"]
        consolidated = []
        for file_path, fecha in valid_files:
            try:
                df = pd.read_excel(file_path)

                for src, dst in Config.CEDULA_COLUMN_ALIASES.items():
                    if src in df.columns and dst not in df.columns:
                        df = df.rename(columns={src: dst})

                if not all(col in df.columns for col in required_cols):
                    continue

                df['Unidades'] = df['Unidades'].astype(str).str.strip().str.upper()
                df['Fecha Cedula_dt'] = fecha
                consolidated.append(df)
            except Exception:
                continue

        if not consolidated:
            return pd.DataFrame()

        df_local = pd.concat(consolidated, ignore_index=True)
        log(f"Cédulas locales para cruce: {len(df_local)} registros", code="INFO")
        return df_local

    except Exception as e:
        log(f"Cédulas locales para cruce no disponibles: {e}", code="INFO")
        return pd.DataFrame()


def crossfill_cedulas(
    df_primary: pd.DataFrame, df_local: pd.DataFrame, log: LogCallback
) -> tuple[pd.DataFrame, list[tuple[str, object, str]]]:
    """Completa columnas vacías de `df_primary` con valores de `df_local`.

    Cruce por `['Unidades', 'Fecha Cedula_dt']`. Para cada columna de
    `Config.COLUMNS["units"][1:] + Config.COLUMNS["units_extra"]` presente
    en ambos frames, si `df_primary` viene vacío/NaN se rellena desde
    `df_local`. Devuelve el frame resultante y la lista de
    `(Unidad, Fecha, Campo)` completados, para reporte de inconsistencias.
    """
    fillable_cols = [
        c for c in Config.COLUMNS["units"][1:] + Config.COLUMNS["units_extra"]
        if c in df_primary.columns and c in df_local.columns
    ]
    if not fillable_cols:
        return df_primary, []

    merge_cols = ['Unidades', 'Fecha Cedula_dt']
    df_local_subset = df_local[merge_cols + fillable_cols].copy()

    # pandas 3 infiere dtype 'str' para columnas de texto (no 'object'); ese
    # dtype solo acepta strings/NA y revienta con TypeError si la cedula
    # local trae un valor no-string (p.ej. 'No Operador' leido como int64
    # desde Excel cuando todos los valores de esa columna son numericos).
    # Forzar 'object' en ambos lados antes del cruce evita el error al
    # asignar `merged.loc[to_fill, col] = merged.loc[to_fill, local_col]`.
    df_primary = df_primary.copy()
    for col in fillable_cols:
        if df_primary[col].dtype != object:
            df_primary[col] = df_primary[col].astype(object)
        if df_local_subset[col].dtype != object:
            df_local_subset[col] = df_local_subset[col].astype(object)

    merged = df_primary.merge(
        df_local_subset, on=merge_cols, how='left', suffixes=('', '_local')
    )

    crossfill_log: list[tuple[str, object, str]] = []
    for col in fillable_cols:
        local_col = f"{col}_local"
        if local_col not in merged.columns:
            continue

        primary_empty = merged[col].isna() | (merged[col].astype(str).str.strip() == '')
        local_available = merged[local_col].notna() & (merged[local_col].astype(str).str.strip() != '')
        to_fill = primary_empty & local_available

        for idx in merged.index[to_fill]:
            crossfill_log.append((merged.at[idx, 'Unidades'], merged.at[idx, 'Fecha Cedula_dt'], col))

        merged.loc[to_fill, col] = merged.loc[to_fill, local_col]
        merged = merged.drop(columns=[local_col])

    return merged, crossfill_log


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

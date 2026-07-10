"""I/O Google Sheets: carga de cedula horizontal y sync del workbook KPI.

Funciones puras extraidas de `DataProcessor` (v0.4.2 → v0.4.3) para aislar
acceso a la API de Google Sheets del motor de calculo. Reciben
`log_callback` para respetar el sistema de logging del caller.

Publico:
- `load_cedula_from_sheet(sheet_id, log_callback, tab_name, use_revision_history)` -> DataFrame | None
- `load_cedulas_for_period(sheet_id, log_callback, fecha_min, fecha_max, tab_name, cedulas_folder)` -> DataFrame | None
  Reemplaza a `load_cedula_from_sheet` en `_load_cedulas_by_source` (fuente "sheets"):
  prioriza archivos fisicos diarios por fecha y solo recurre a Drive API para los
  huecos, evitando que el valor VIGENTE del sheet se aplique a fechas pasadas.
- `fetch_dates_from_revisions(sheet_id, log, dates, tab_name, save_folder,
  approximate_older, lineage)` -> dict[date, DataFrame]
  Fuente unica de la logica Drive (v0.6.5): la usan load_cedulas_for_period y el
  gap-filler del modo excel (via callback armado en el processor). Best-effort.
- `sync_workbook_to_sheets(sheets_id, dfs, log_callback)` -> bool

Nota: todas importan `google-auth` + `gspread`. Para tests unitarios que no
toquen la red, mockear `gspread.authorize` o `Credentials.from_service_account_file`.
"""

from __future__ import annotations

import io as _io
import re
import time as _time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import gspread
import pandas as pd
import requests as _req
from google.oauth2.service_account import Credentials

from kpi_generator.config import Config, LogLevel
from kpi_generator.io.excel import fill_missing_dates, parse_cedula_filename, parse_cedula_filename_ex
from kpi_generator.lineage import ArchivoCedula, CedulaLineage

LogCallback = Callable[..., None]

# Columnas estáticas del tab que cambian cuando una unidad cambia de operación.
# Son las que necesitan el parche por historial de revisiones.
_STATIC_COLS_TO_PATCH = ['Gerencia', 'Operación', 'Circuito', 'Tipo de Unidad']


# ---------------------------------------------------------------------------
# Helpers privados — historial de revisiones Drive API
# ---------------------------------------------------------------------------

def _list_revisions(sheet_id: str, creds) -> list[dict]:
    """Lista TODAS las revisiones disponibles del spreadsheet vía Drive API v3.

    Devuelve lista ordenada ascendente por modifiedTime. Vacía si el Drive API
    no está disponible o no hay revisiones.
    """
    try:
        from googleapiclient.discovery import build
        drive_svc = build('drive', 'v3', credentials=creds)
        all_revs: list[dict] = []
        page_token = None
        while True:
            kwargs: dict = dict(
                fileId=sheet_id,
                fields='nextPageToken,revisions(id,modifiedTime)',
                pageSize=200,
            )
            if page_token:
                kwargs['pageToken'] = page_token
            resp = drive_svc.revisions().list(**kwargs).execute()
            all_revs.extend(resp.get('revisions', []))
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        return all_revs  # API devuelve orden cronológico ascendente
    except Exception:
        return []


def _revision_for_date(revisions: list[dict], target_date) -> dict | None:
    """Revisión más reciente cuya modifiedTime <= fin del target_date (23:59:59 UTC).

    target_date: datetime.date
    Devuelve None si ninguna revisión precede al target_date.
    """
    end_of_day = datetime(
        target_date.year, target_date.month, target_date.day,
        23, 59, 59, tzinfo=timezone.utc,
    )
    best: dict | None = None
    for rev in revisions:
        rev_time = datetime.fromisoformat(rev['modifiedTime'].replace('Z', '+00:00'))
        if rev_time <= end_of_day:
            best = rev  # iteración ascendente → el último válido es el más reciente
    return best


def _fetch_revision_raw(sheet_id: str, revision_id: str, creds,
                        tab_name: str) -> list[list[str]] | None:
    """Descarga una revisión específica como XLSX y devuelve sus valores
    como lista de listas de strings (mismo formato que ws.get_all_values()).

    Usa el endpoint de export de Google Sheets con el parámetro ?revision=.
    Refresca el token de la service account antes de la descarga.
    Devuelve None si la descarga o el parseo falla.
    """
    try:
        import google.auth.transport.requests
        creds.refresh(google.auth.transport.requests.Request())

        url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
            f"?format=xlsx&revision={revision_id}"
        )
        resp = _req.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
        resp.raise_for_status()

        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')
            df = pd.read_excel(_io.BytesIO(resp.content), sheet_name=tab_name,
                               header=None, dtype=str)
        df = df.fillna('')
        return [row.tolist() for _, row in df.iterrows()]
    except Exception:
        return None


def _extract_unit_statics(all_rows: list[list[str]],
                           cols_to_extract: list[str]) -> dict[str, dict[str, str]]:
    """Extrae los valores de cols_to_extract para cada unidad de un snapshot.

    Aplica CEDULA_COLUMN_ALIASES al header para normalizar nombres de columna.
    Devuelve {unit_upper: {col_canonical: valor}}.
    Devuelve {} si no encuentra el header o no hay unidades.
    """
    unit_id_re = re.compile(r'^[A-Za-z][A-Za-z0-9]+$')

    header_idx = next(
        (i for i, row in enumerate(all_rows) if 'Unidad' in row and 'Gerencia' in row),
        None,
    )
    if header_idx is None:
        return {}

    header = all_rows[header_idx]
    unit_col_idx = header.index('Unidad')

    canonical = [Config.CEDULA_COLUMN_ALIASES.get(h.strip(), h.strip()) for h in header]
    col_to_idx = {col: canonical.index(col) for col in cols_to_extract if col in canonical}
    if not col_to_idx:
        return {}

    result: dict[str, dict[str, str]] = {}
    for row in all_rows[header_idx + 1:]:
        if len(row) <= unit_col_idx:
            continue
        unit = row[unit_col_idx].strip()
        if not unit_id_re.match(unit):
            continue
        padded = row + [''] * max(0, len(header) - len(row))
        result[unit.upper()] = {col: padded[idx].strip() for col, idx in col_to_idx.items()}

    return result


def _patch_static_from_revisions(df: pd.DataFrame, sheet_id: str,
                                  creds, tab_name: str,
                                  live_all_rows: list[list[str]],
                                  log: LogCallback) -> pd.DataFrame:
    """Reemplaza las columnas estáticas en df con valores históricos de revisiones.

    Para cada fecha presente en df, busca la revisión Drive API más reciente
    que estuviera vigente al final de ese día y sustituye Gerencia/Operación/
    Circuito/Tipo de Unidad con los valores de ese snapshot.

    Si no hay revisión anterior a una fecha, usa la revisión más antigua
    disponible como aproximación. Si el Drive API falla o no hay revisiones,
    df queda sin cambios (los valores actuales del sheet prevalecen).
    """
    revisions = _list_revisions(sheet_id, creds)
    if not revisions:
        log("Historial Drive API: sin revisiones disponibles, usando estado actual",
            LogLevel.DEBUG, "REV")
        return df

    log(f"Historial Drive API: {len(revisions)} revisiones disponibles", LogLevel.DEBUG, "REV")

    # Fechas únicas presentes en df
    dates_in_df = sorted(df['Fecha Cedula_dt'].dt.date.unique())

    # Mapear cada fecha → revisión aplicable
    date_to_rev: dict = {}
    oldest_rev = revisions[0]
    for d in dates_in_df:
        rev = _revision_for_date(revisions, d)
        # Si ninguna revisión precede a la fecha, usar la más antigua como aproximación
        date_to_rev[d] = rev if rev is not None else oldest_rev

    # Agrupar fechas por revision_id para minimizar descargas
    rev_id_to_dates: dict[str, list] = defaultdict(list)
    for d, rev in date_to_rev.items():
        rev_id_to_dates[rev['id']].append(d)

    # Para la revisión más reciente (la que coincide con el live sheet),
    # reusar live_all_rows en lugar de descargar de nuevo.
    newest_rev_id = revisions[-1]['id']

    # Construir registros de parche: {(unit, date): {col: val}}
    patch_records: list[dict] = []
    revs_downloaded = 0
    revs_failed = 0

    for rev_id, dates in rev_id_to_dates.items():
        if rev_id == newest_rev_id:
            snapshot_rows = live_all_rows
        else:
            snapshot_rows = _fetch_revision_raw(sheet_id, rev_id, creds, tab_name)
            if snapshot_rows is None:
                revs_failed += 1
                log(f"Revisión {rev_id}: descarga fallida, manteniendo valor actual",
                    LogLevel.DEBUG, "REV")
                continue
            revs_downloaded += 1

        unit_statics = _extract_unit_statics(snapshot_rows, _STATIC_COLS_TO_PATCH)
        for d in dates:
            for unit, statics in unit_statics.items():
                patch_records.append({'_date': d, 'Unidades': unit, **statics})

    if not patch_records:
        return df

    log(f"Historial: {revs_downloaded} revisiones descargadas"
        + (f", {revs_failed} fallidas" if revs_failed else ""),
        LogLevel.DEBUG, "REV")

    # Aplicar parches al df mediante merge vectorizado
    df_patches = pd.DataFrame(patch_records)
    static_cols_present = [c for c in _STATIC_COLS_TO_PATCH if c in df.columns and c in df_patches.columns]

    df['_date'] = df['Fecha Cedula_dt'].dt.date
    df = df.merge(
        df_patches.rename(columns={c: f'_rev_{c}' for c in static_cols_present}),
        on=['_date', 'Unidades'],
        how='left',
    )
    for col in static_cols_present:
        rev_col = f'_rev_{col}'
        if rev_col in df.columns:
            # Usar valor de revisión donde exista; mantener actual donde no
            df[col] = df[rev_col].where(df[rev_col].notna() & (df[rev_col] != ''), df[col])
            df = df.drop(columns=[rev_col])
    df = df.drop(columns=['_date'])

    return df


# ---------------------------------------------------------------------------
# Helpers para load_cedulas_for_period
# ---------------------------------------------------------------------------

def _parse_header_date(value: str) -> date | None:
    """Fecha de un encabezado de columna de cédula, tolerante al origen.

    - `DD/MM/YYYY`: formato del sheet vivo (gspread `get_all_values`).
    - `YYYY-MM-DD[ 00:00:00]`: celdas datetime del XLSX exportado por el
      endpoint de revisiones, renderizadas por pandas con `dtype=str`.
      (Bug real 09/07/2026: las revisiones intermedias extraían 0 registros
      porque solo se aceptaba DD/MM/YYYY.)
    """
    s = str(value).strip()
    if s.endswith(' 00:00:00'):
        s = s[:-9].strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _extract_cedula_vertical_for_date(
    all_rows: list[list[str]],
    target_date: date,
) -> list[dict]:
    """Extrae snapshot vertical de una cédula horizontal para una fecha específica.

    Busca la columna de fecha más reciente <= target_date en all_rows.
    Devuelve lista de dicts {Unidades, Gerencia, Operación, …, Operando,
    Fecha Cedula} o lista vacía si falla.
    """
    unit_id_re = re.compile(r'^[A-Za-z][A-Za-z0-9]+$')
    subtotal_re = re.compile(r'^\d+\s+al\s+\d+|en\s+adelante', re.IGNORECASE)
    skip_col_names = {'Taller', 'Gestoría', 'Sin operador', 'Sin Operador', ''}

    header_idx = next(
        (i for i, row in enumerate(all_rows) if 'Unidad' in row and 'Gerencia' in row),
        None,
    )
    if header_idx is None:
        return []

    header = all_rows[header_idx]
    unit_col_idx = header.index('Unidad')

    # Construir lista (col_index, col_date) para todas las columnas de fecha
    date_col_pairs: list[tuple[int, date]] = []
    for i, h in enumerate(header):
        d = _parse_header_date(h)
        if d is not None:
            date_col_pairs.append((i, d))

    if not date_col_pairs:
        return []

    # Columna más reciente <= target_date; si ninguna califica, la más antigua
    valid = [(i, d) for i, d in date_col_pairs if d <= target_date]
    best_col_idx, _ = max(valid, key=lambda x: x[1]) if valid else date_col_pairs[0]

    # Columnas meta (no son fechas, no son subtotales, no son categorías a omitir)
    meta_col_indices = [
        i for i, h in enumerate(header)
        if _parse_header_date(h) is None
        and not subtotal_re.match(h.strip())
        and h.strip() not in skip_col_names
    ]

    records = []
    for row in all_rows[header_idx + 1:]:
        if len(row) <= unit_col_idx:
            continue
        unit = row[unit_col_idx].strip()
        if not unit_id_re.match(unit):
            continue
        padded = row + [''] * max(0, len(header) - len(row))
        meta = {
            Config.CEDULA_COLUMN_ALIASES.get(header[i].strip(), header[i].strip()): padded[i].strip()
            for i in meta_col_indices
        }
        meta['Operando'] = padded[best_col_idx] if best_col_idx < len(padded) else ''
        meta['Fecha Cedula'] = target_date.strftime('%d/%m/%Y')
        records.append(meta)

    return records


def _finalize_cedulas_df(df: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill Operando vacío entre fechas y rellena días ausentes."""
    if 'Operando' in df.columns:
        df = df.sort_values(['Unidades', 'Fecha Cedula_dt'])
        df['Operando'] = (
            df.groupby('Unidades')['Operando']
            .transform(lambda s: s.replace('', None).ffill().fillna('Desconocido'))
        )
    return fill_missing_dates(df)


def fetch_dates_from_revisions(
    sheet_id: str,
    log: LogCallback,
    dates: list[date],
    tab_name: Optional[str] = None,
    save_folder: Optional[str] = None,
    approximate_older: bool = True,
    lineage: Optional[CedulaLineage] = None,
) -> dict[date, pd.DataFrame]:
    """Descarga snapshots de cédula del historial de revisiones para `dates`.

    Fuente única de la lógica Drive (v0.6.5): la usan `load_cedulas_for_period`
    (fuente sheets) y el gap-filler del modo excel (vía callback armado en el
    processor, para que `io/excel.py` no importe red).

    **Best-effort**: sin credenciales / offline / Drive caído devuelve `{}` (o
    parcial) sin lanzar — el caller decide el fallback (forward-fill).

    - Para cada fecha usa la revisión más reciente <= fin del día.
    - `approximate_older=True` (semántica histórica de la fuente sheets):
      fechas anteriores a toda revisión se aproximan con la revisión más
      antigua. Con `False` (modo excel), esas fechas se omiten y quedan al
      forward-fill del caller — un archivo aproximado se volvería autoritativo
      en la siguiente corrida.
    - Devuelve `dict fecha -> DataFrame` normalizado (Unidades upper,
      `Fecha Cedula`/`Fecha Cedula_dt`), mismo formato que los frames físicos.
    - Con `save_folder`, guarda cada día como `Cedula DDMMYYYY Completa.xlsx`
      **sin sobrescribir** archivos existentes.
    - Registra en `lineage` las fechas Drive y las advertencias.
    """
    result: dict[date, pd.DataFrame] = {}
    if not dates:
        return result

    try:
        creds = Credentials.from_service_account_file(
            Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        if tab_name is None:
            tab_name = sh.worksheets()[0].title
        ws = sh.worksheet(tab_name)
        live_all_rows = ws.get_all_values()
        revisions = _list_revisions(sheet_id, creds)
        log(f"Drive API: {len(revisions)} revisiones disponibles", LogLevel.DEBUG, "REV")
    except Exception as e:
        log(f"Sheets/Drive no disponible para completar fechas: {e}", LogLevel.ERROR, "WARN")
        if lineage is not None:
            lineage.advertencias.append(
                f"Sheets/Drive no disponible para completar {len(dates)} fechas ({e})"
            )
        return result

    newest_rev_id = revisions[-1]['id'] if revisions else None
    oldest_rev = revisions[0] if revisions else None

    # Agrupar por revision_id para minimizar descargas
    rev_id_to_dates: dict[str, list[date]] = defaultdict(list)
    sin_revision: list[date] = []
    for d in sorted(dates):
        rev = _revision_for_date(revisions, d) if revisions else None
        if rev is None and approximate_older:
            rev = oldest_rev  # aproximación si la fecha es anterior a la primera revisión
        if rev:
            rev_id_to_dates[rev['id']].append(d)
        else:
            sin_revision.append(d)

    if sin_revision:
        msg = (f"{len(sin_revision)} fechas sin revisión Drive que las cubra "
               f"(desde {sin_revision[0].strftime('%d/%m/%Y')}): quedan a forward-fill")
        log(msg, LogLevel.ERROR, "WARN")
        if lineage is not None:
            lineage.advertencias.append(msg)

    folder_path = Path(save_folder) if save_folder else None

    for rev_id, dates_for_rev in rev_id_to_dates.items():
        snapshot_rows = (
            live_all_rows if rev_id == newest_rev_id
            else _fetch_revision_raw(sheet_id, rev_id, creds, tab_name)
        )
        if snapshot_rows is None:
            log(f"Revisión {rev_id}: descarga fallida", LogLevel.DEBUG, "REV")
            continue

        for d in dates_for_rev:
            records = _extract_cedula_vertical_for_date(snapshot_rows, d)
            if not records:
                continue

            df_d = pd.DataFrame(records)
            df_d = df_d.rename(columns={'Unidad': 'Unidades'})
            if 'Unidades' not in df_d.columns:
                continue
            df_d['Unidades'] = df_d['Unidades'].str.strip().str.upper()
            df_d['Fecha Cedula'] = d.strftime('%d/%m/%Y')
            df_d['Fecha Cedula_dt'] = pd.Timestamp(d)
            result[d] = df_d
            if lineage is not None:
                lineage.fechas_drive.append(d)

            # Guardar xlsx en carpeta para reusar en futuras ejecuciones.
            # Nunca sobrescribe: si Beto crea después un diario a mano para
            # esta fecha, la fusión v0.6.4 le da la autoridad al diario.
            if folder_path is not None:
                try:
                    cols_save = [
                        c for c in Config.COLUMNS["units"] + Config.COLUMNS["units_extra"]
                        if c in df_d.columns
                    ]
                    fname = f"Cedula {d.strftime('%d%m%Y')} Completa.xlsx"
                    out_path = folder_path / fname
                    if not out_path.exists():
                        df_d[cols_save].to_excel(out_path, engine='openpyxl', index=False)
                        log(f"Drive API → guardado: {fname}", code="SAVE")
                except Exception:
                    pass

    return result


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def load_cedula_from_sheet(sheet_id: str, log: LogCallback,
                           tab_name: Optional[str] = None,
                           use_revision_history: bool = True) -> Optional[pd.DataFrame]:
    """Cargar cedula mensual desde Google Sheets (formato horizontal) y devolverla
    en formato vertical (una fila por unidad+dia) compatible con el pipeline.

    El sheet de cedula tiene una columna por dia (encabezado DD/MM/YYYY) y una
    fila por unidad. Esta funcion:
    1. Localiza el header (fila con 'Unidad' y 'Gerencia').
    2. Extrae columnas-fecha (regex DD/MM/YYYY) y columnas-metadato.
    3. Filtra filas que parecen ID de unidad (regex `[A-Za-z][A-Za-z0-9]+`).
    4. Genera registros (unidad, fecha, operando) por cada celda dia.
    5. Si use_revision_history=True (default): reemplaza las columnas estáticas
       (Gerencia/Operación/Circuito/Tipo de Unidad) con los valores históricos
       de las revisiones Drive API correspondientes a cada fecha.
    6. Forward-fill por unidad (celda vacia hereda del dia anterior).
    7. Rellena fechas globalmente ausentes via `fill_missing_dates`.

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

        # Paso 5: parche de columnas estáticas con historial de revisiones
        if use_revision_history:
            df = _patch_static_from_revisions(df, sheet_id, creds, tab_name, all_rows, log)

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


# Reintentos ante errores transitorios de Google (v0.6.6 — incidente 09/07/2026:
# un 503 momentáneo obligaba a rehacer el pipeline completo de ~3-5min solo para
# reintentar la subida). 3 intentos totales; delay antes del 2°/3° intento.
_SHEETS_MAX_ATTEMPTS = 3
_SHEETS_BACKOFF_SECONDS = (2, 8)
_SHEETS_TRANSIENT_API_CODES = {429, 500, 502, 503, 504}


def _is_transient_sheets_error(e: Exception) -> bool:
    """Errores que vale la pena reintentar: rate-limit/5xx de la API o red caída.

    NO reintenta credenciales/permisos (401/403) — esos fallan rápido, igual que hoy.
    """
    if isinstance(e, gspread.exceptions.APIError):
        return e.code in _SHEETS_TRANSIENT_API_CODES
    return isinstance(e, (_req.exceptions.ConnectionError, _req.exceptions.Timeout))


def sync_workbook_to_sheets(sheets_id: str, dfs: dict[str, pd.DataFrame],
                            log: LogCallback,
                            sleep_fn: Callable[[float], None] = _time.sleep) -> bool:
    """Sube todos los DataFrames a Google Sheets, un tab por entry de `dfs`.

    `dfs` es un dict `{nombre_tab: dataframe}`. Cada DataFrame se sobreescribe
    completamente (clear + update) o se crea como tab nuevo si no existia.
    DataFrames vacios o `None` se omiten silenciosamente.

    Reintenta con backoff (`_SHEETS_MAX_ATTEMPTS` intentos) errores transitorios
    (429/5xx, timeouts de red) — reconecta y re-sube todo desde cero en cada intento
    (`ws.clear()+update()` es idempotente, así que no hay riesgo de estado parcial).
    Errores de credenciales/permisos no se reintentan.

    `sleep_fn` es inyectable para tests (evita esperar tiempo real).

    Devuelve `True` si todo OK, `False` tras agotar los intentos (loggea el error).
    """
    for attempt in range(1, _SHEETS_MAX_ATTEMPTS + 1):
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
            if _is_transient_sheets_error(e) and attempt < _SHEETS_MAX_ATTEMPTS:
                delay = _SHEETS_BACKOFF_SECONDS[attempt - 1]
                log(f"Error transitorio en Sheets (intento {attempt}/{_SHEETS_MAX_ATTEMPTS}): "
                    f"{e} — reintentando en {delay}s...", LogLevel.ERROR, "SHEETS")
                sleep_fn(delay)
                continue
            log(f"Error Google Sheets: {e}", LogLevel.ERROR, "SHEETS")
            return False

    return False  # inalcanzable (el for siempre retorna), por completitud de tipos


def load_cedulas_for_period(
    sheet_id: str,
    log: LogCallback,
    fecha_min: date,
    fecha_max: date,
    tab_name: Optional[str] = None,
    cedulas_folder: Optional[str] = None,
    lineage: Optional[CedulaLineage] = None,
) -> Optional[pd.DataFrame]:
    """Carga cédulas para [fecha_min, fecha_max] con prioridad por fecha:
    1. Archivo físico en cedulas_folder (autoritativo — cédulas guardadas a mano)
    2. Revisión Drive API para esa fecha (descargada y guardada en carpeta)
    3. forward-fill para gaps residuales

    Reemplaza load_cedula_from_sheet en el flujo source='sheets'.
    `lineage` (opcional, v0.6.4) acumula la trazabilidad: archivos físicos
    leídos, fechas cubiertas por Drive y fechas que quedaron a forward-fill.
    """
    n_days = (fecha_max - fecha_min).days + 1
    all_dates = [fecha_min + timedelta(days=i) for i in range(n_days)]

    # ------------------------------------------------------------------
    # Paso 1: archivos físicos ya en carpeta para el rango
    # ------------------------------------------------------------------
    dates_physical: dict[date, pd.DataFrame] = {}
    archivos_por_fecha: dict[date, ArchivoCedula] = {}
    folder_path: Optional[Path] = None

    if cedulas_folder:
        folder_path = Path(cedulas_folder)
        if folder_path.exists() and folder_path.is_dir():
            for f in sorted(folder_path.glob("*.xlsx")):
                parsed = parse_cedula_filename_ex(f.name)
                if parsed is None:
                    continue
                d = parsed.fecha.date()
                if not (fecha_min <= d <= fecha_max):
                    continue
                try:
                    df_f = pd.read_excel(f, dtype=str).fillna('')
                    for src, dst in Config.CEDULA_COLUMN_ALIASES.items():
                        if src in df_f.columns and dst not in df_f.columns:
                            df_f = df_f.rename(columns={src: dst})
                    if 'Unidades' not in df_f.columns:
                        continue
                    df_f['Unidades'] = df_f['Unidades'].str.strip().str.upper()
                    df_f['Fecha Cedula'] = d.strftime('%d/%m/%Y')
                    df_f['Fecha Cedula_dt'] = pd.Timestamp(d)
                    dates_physical[d] = df_f
                    if lineage is not None:
                        archivo = ArchivoCedula(
                            nombre=f.name, fecha=d, variante=parsed.variante,
                            mtime=datetime.fromtimestamp(f.stat().st_mtime),
                            filas=len(df_f),
                        )
                        previo = archivos_por_fecha.get(d)
                        if previo is not None:
                            previo.rol = 'descartado'
                            previo.detalle = ('reemplazado por otro archivo de la misma '
                                              'fecha (orden del glob)')
                        archivos_por_fecha[d] = archivo
                        lineage.archivos.append(archivo)
                except Exception:
                    continue

    n_phys = len(dates_physical)
    if lineage is not None:
        lineage.fechas_fisicas = sorted(dates_physical)
    log(
        f"Archivos físicos en rango: {n_phys} días"
        + (f" ({min(dates_physical)} → {max(dates_physical)})" if dates_physical else ""),
        code="PHYS",
    )

    # ------------------------------------------------------------------
    # Pasos 2-3: fechas faltantes → historial de revisiones Drive API
    # (lógica centralizada en fetch_dates_from_revisions, v0.6.5)
    # ------------------------------------------------------------------
    dates_missing = [d for d in all_dates if d not in dates_physical]
    if dates_missing:
        log(f"Fechas sin archivo físico: {len(dates_missing)} → consultando Drive API",
            code="REV")

    fetched = fetch_dates_from_revisions(
        sheet_id, log, dates_missing,
        tab_name=tab_name,
        save_folder=cedulas_folder if folder_path is not None else None,
        approximate_older=True,  # semántica histórica de la fuente sheets
        lineage=lineage,
    )
    n_drive_added = len(fetched)
    for d, df_d in fetched.items():
        dates_physical[d] = df_d

    # ------------------------------------------------------------------
    # Paso 4: reporte de cobertura
    # ------------------------------------------------------------------
    n_gap = sum(1 for d in all_dates if d not in dates_physical)
    if lineage is not None:
        lineage.fechas_ffill = sorted(d for d in all_dates if d not in dates_physical)
    log(
        f"Cobertura cédulas: {n_phys} físicos, {n_drive_added} Drive API"
        + (f", {n_gap} gap (forward-fill)" if n_gap else ", cobertura completa"),
        code="COV",
    )

    # ------------------------------------------------------------------
    # Paso 5: construir DataFrame final
    # ------------------------------------------------------------------
    available = [dates_physical[d] for d in all_dates if d in dates_physical]
    if not available:
        log("Sin datos de cédula disponibles para el período", LogLevel.ERROR, "ERR")
        return None

    df = pd.concat(available, ignore_index=True)
    df['Fecha Cedula_dt'] = pd.to_datetime(df['Fecha Cedula_dt'])
    log(
        f"Cédulas período: {df['Unidades'].nunique()} unidades, "
        f"{df['Fecha Cedula_dt'].nunique()} días",
        code="OK",
    )
    return _finalize_cedulas_df(df)

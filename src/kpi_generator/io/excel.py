"""I/O Excel: carga de cedulas locales y escritura del workbook KPI.

Funciones puras extraidas de `DataProcessor` (v0.4.2 → v0.4.3) para aislar
acceso al filesystem del motor de calculo. Reciben `log_callback` para
respetar el sistema de logging del caller.

Publico:
- `parse_cedula_filename(filename)` -> datetime | None
- `parse_cedula_filename_ex(filename)` -> ParsedCedula(fecha, variante) | None
- `fill_missing_dates(df_cedulas)` -> DataFrame (forward-fill por fecha)
- `load_daily_cedulas(folder, log_callback, *, lineage=None)` -> DataFrame | None
- `save_cedula_as_completa(df_cedulas, folder, log_callback)` -> None
- `load_local_cedulas_for_crossfill(folder, log_callback)` -> DataFrame
- `crossfill_cedulas(df_primary, df_local, log_callback)` -> (DataFrame, list)
- `write_workbook(dfs, output_dir, log_callback)` -> Path (archivo generado)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import pandas as pd

from kpi_generator.config import Config, LogLevel
from kpi_generator.lineage import ArchivoCedula, CedulaLineage

# Tipo del callback de logging usado por las funciones de este modulo.
# Firma: log(message: str, level: LogLevel = INFO, code: str | None = None)
LogCallback = Callable[..., None]


class ParsedCedula(NamedTuple):
    """Resultado de `parse_cedula_filename_ex`."""

    fecha: datetime
    variante: str  # 'diario' | 'variante'


@lru_cache(maxsize=128)
def parse_cedula_filename_ex(filename: str) -> Optional[ParsedCedula]:
    """Extraer fecha Y tipo de variante del nombre de archivo de cedula.

    Reconoce los formatos historicos del proyecto:
    - `Cedula DDMMYYYY.xlsx` (canonico)
    - `Cedula D M YYYY.xlsx` (separadores con espacios)
    - `Cédula DDMMYYYY.xlsx` (con tilde)
    - `.xls` legacy.

    Clasificacion (v0.6.4):
    - `variante='diario'`: nombre canonico sin palabras extra — la cedula
      fisica guardada a mano, autoritativa.
    - `variante='variante'`: cualquier palabra extra antes o despues de la
      fecha ("Cedula completa 01072026.xlsx", "Cedula 01062026 Completa.xlsx")
      — tipicamente descargas de Drive; en fusion solo rellenan vacios.

    Devuelve `None` si el archivo no coincide con ningun patron o si la
    fecha extraida no es valida (mes 13, dia 32, etc.).
    """
    # Palabras opcionales (solo letras) entre "cedula" y la fecha, para no
    # consumir los digitos; cubre "Cedula completa DDMMYYYY.xlsx". Capturadas
    # para clasificar diario vs variante.
    infix = r'((?:\s+[a-zà-ÿ]+)*)'
    suffix = r'((?:\s+[\wÀ-ÿ]+)*)\.xlsx?'
    patterns = [
        r'cedula' + infix + r'\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})' + suffix,
        r'c[eé]dula' + infix + r'\s*(\d{1,2})\s*(\d{1,2})\s*(\d{4})' + suffix,
        r'cedula' + infix + r'\s*(\d{2})(\d{2})(\d{4})' + suffix,
    ]
    for pattern in patterns:
        match = re.search(pattern, filename.lower())
        if match:
            extra_infix, day, month, year, extra_suffix = match.groups()
            try:
                fecha = datetime(int(year), int(month), int(day))
            except ValueError:
                continue
            es_variante = bool(extra_infix.strip() or extra_suffix.strip())
            return ParsedCedula(fecha, 'variante' if es_variante else 'diario')
    return None


@lru_cache(maxsize=128)
def parse_cedula_filename(filename: str) -> Optional[datetime]:
    """Extraer fecha del nombre de archivo de cedula (wrapper histórico).

    Delegado a `parse_cedula_filename_ex`; conserva la firma original
    (solo fecha) que usan `io.sheets`, `save_cedula_as_completa`,
    `load_local_cedulas_for_crossfill`, la GUI y el CLI `diff-cedulas`.
    """
    parsed = parse_cedula_filename_ex(filename)
    return parsed.fecha if parsed else None


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


def _fusionar_cedulas_mismo_dia(
    entradas: list[tuple[ArchivoCedula, pd.DataFrame]],
    log: LogCallback,
    lineage: Optional[CedulaLineage] = None,
) -> pd.DataFrame:
    """Fusion complementaria de varios archivos fisicos de la MISMA fecha.

    Regla v0.6.4 ("fisico manda al 100%"):
    - base = el archivo 'diario' (autoritativo campo por campo); con 2+
      diarios gana el de mtime mas reciente (resto 'descartado'); sin
      diarios, base = la variante de mtime mas reciente.
    - las variantes solo RELLENAN celdas vacias de la base (reutiliza
      `crossfill_cedulas`, que nunca pisa un valor presente).
    - unidades presentes solo en la variante NO se agregan: el diario
      define el universo de unidades del dia (decision (a) del plan).

    Las claves se normalizan (strip+upper) solo para el cruce; los valores
    de `Unidades` del archivo base se conservan tal cual.
    """
    diarios = [(a, df) for a, df in entradas if a.variante == 'diario']
    variantes = [(a, df) for a, df in entradas if a.variante == 'variante']

    if diarios:
        diarios.sort(key=lambda e: e[0].mtime, reverse=True)
        base_arch, base_df = diarios[0]
        for arch, _df in diarios[1:]:
            arch.rol = 'descartado'
            arch.detalle = 'diario duplicado de la misma fecha (mtime anterior)'
            log(f"Diario duplicado descartado: {arch.nombre}", LogLevel.ERROR, "WARN")
        complementos = variantes
    else:
        variantes.sort(key=lambda e: e[0].mtime, reverse=True)
        base_arch, base_df = variantes[0]
        complementos = variantes[1:]

    base_arch.rol = 'base'
    base_df = base_df.copy()
    base_df['_unidades_orig'] = base_df['Unidades']
    base_df['Unidades'] = base_df['Unidades'].astype(str).str.strip().str.upper()
    base_keys = set(base_df['Unidades'])

    for arch, comp_df in complementos:
        comp_df = comp_df.copy()
        comp_df['Unidades'] = comp_df['Unidades'].astype(str).str.strip().str.upper()
        comp_df = comp_df.drop_duplicates(subset=['Unidades', 'Fecha Cedula_dt'], keep='first')
        solo_variante = sorted(set(comp_df['Unidades']) - base_keys)

        # Columnas que la variante trae y la base no (p. ej. Operador en
        # diarios de 6 columnas): se crean vacias en la base para que el
        # crossfill pueda aportarlas — sigue sin pisar nada del diario.
        aportables = [
            c for c in Config.COLUMNS["units"][1:] + Config.COLUMNS["units_extra"]
            if c in comp_df.columns and c not in base_df.columns
        ]
        for col in aportables:
            base_df[col] = None

        base_df, fills = crossfill_cedulas(base_df, comp_df, log)

        arch.rol = 'complemento'
        detalles = [f"{len(fills)} celdas completadas en la base"]
        if solo_variante:
            detalles.append(f"{len(solo_variante)} unidades solo-variante ignoradas")
        arch.detalle = "; ".join(detalles)

        if lineage is not None:
            lineage.fusion_fills.extend(fills)
            if solo_variante:
                muestra = ", ".join(solo_variante[:5]) + ("…" if len(solo_variante) > 5 else "")
                lineage.advertencias.append(
                    f"{arch.nombre}: {len(solo_variante)} unidades solo en la variante "
                    f"no se agregaron ({muestra})"
                )
        log(f"Fusión {base_arch.nombre} + {arch.nombre}: {arch.detalle}", code="FUSION")

    base_df['Unidades'] = base_df['_unidades_orig']
    return base_df.drop(columns=['_unidades_orig'])


def load_daily_cedulas(cedulas_folder: str, log: LogCallback, *,
                       lineage: Optional[CedulaLineage] = None,
                       fecha_min=None, fecha_max=None,
                       gap_fetcher: Optional[Callable] = None) -> Optional[pd.DataFrame]:
    """Cargar y consolidar cedulas diarias desde una carpeta de .xlsx.

    Cada archivo `Cedula DDMMYYYY.xlsx` aporta una fecha. Las fechas ausentes
    en el rango se rellenan con `fill_missing_dates`.

    Blindaje v0.6.4 ("fisico manda al 100%"):
    - Los archivos se agrupan por fecha; si conviven un diario y variantes
      ("Completa") de la misma fecha se fusionan de forma complementaria
      (`_fusionar_cedulas_mismo_dia`) — antes ambos entraban al concat y
      duplicaban viajes en el merge aguas abajo.
    - Unidad repetida dentro de un mismo archivo → keep-first + WARN.
    - Invariante post-consolidacion: (Unidades, Fecha Cedula_dt) unico;
      si se viola → falla dura (None).
    - `lineage` (opcional) acumula trazabilidad para la hoja "Fuente Cedulas".

    Gap-filler Drive (v0.6.5, keyword-only, default = comportamiento previo):
    - Con `gap_fetcher` + `fecha_min`/`fecha_max` (datetime.date, tipicamente
      el rango de viajes del zmov): las fechas del rango SIN archivo fisico se
      piden al fetcher (callback `list[date] -> dict[date, DataFrame]`, armado
      en el processor con `io.sheets.fetch_dates_from_revisions` — este modulo
      no importa red). Lo fisico jamas se toca ni se re-descarga. Best-effort:
      si el fetcher falla, esas fechas quedan al forward-fill como siempre.

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
            (file_path, parse_cedula_filename_ex(file_path.name))
            for file_path in folder_path.glob("*.xlsx")
        ]

        valid_files = [(f, p) for f, p in cedula_files if p is not None]
        invalid_files = [f.name for f, p in cedula_files if p is None]

        if invalid_files:
            log(f"Archivos formato inválido: {len(invalid_files)}", LogLevel.ERROR, "ERR")
            return None

        if not valid_files:
            log("Sin archivos válidos", LogLevel.ERROR, "ERR")
            return None

        # Deteccion de carpeta sospechosa (la trampa del incidente de junio:
        # correr modo excel sobre una carpeta de descargas de Drive).
        n_diarios = sum(1 for _f, p in valid_files if p.variante == 'diario')
        n_variantes = len(valid_files) - n_diarios
        fechas_diario = {p.fecha for _f, p in valid_files if p.variante == 'diario'}
        fechas_variante = {p.fecha for _f, p in valid_files if p.variante == 'variante'}
        traslape = fechas_diario & fechas_variante
        if traslape:
            log(f"Carpeta mixta: {len(traslape)} fecha(s) con diario Y variante de "
                "la misma fecha — el diario manda; la variante solo rellena vacíos",
                LogLevel.ERROR, "WARN")
            if lineage is not None:
                lineage.carpeta_mixta = True
        elif n_diarios and n_variantes:
            # Diarios y variantes en fechas DISTINTAS: estado normal de una
            # carpeta auto-completada por el gap-filler Drive (v0.6.5) — no
            # es sospechoso, no dispara advertencia.
            log(f"Carpeta combinada: {n_diarios} diarios + {n_variantes} variantes "
                "en fechas distintas", code="INFO")
        elif n_variantes and not n_diarios:
            msg = (f"La carpeta contiene SOLO variantes 'Completa' ({n_variantes} archivos) "
                   "— verifica que sea la carpeta de cédulas físicas diarias")
            log(msg, LogLevel.ERROR, "WARN")
            if lineage is not None:
                lineage.advertencias.append(msg)

        required_cols = Config.COLUMNS["units"]
        por_fecha: dict[datetime, list[tuple[ArchivoCedula, pd.DataFrame]]] = {}

        for file_path, parsed in sorted(valid_files, key=lambda x: (x[1].fecha, x[0].name)):
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

                fecha = parsed.fecha
                df['Fecha Cedula'] = fecha.strftime("%d/%m/%Y")
                df['Fecha Cedula_dt'] = fecha

                # Unidad repetida dentro del mismo archivo → keep-first + WARN
                # (decision (b) del plan: no bloquear produccion por un typo).
                key = df['Unidades'].astype(str).str.strip().str.upper()
                dup_mask = key.duplicated(keep='first')
                if dup_mask.any():
                    duplicadas = sorted(key[dup_mask].unique())
                    muestra = ", ".join(duplicadas[:5]) + ("…" if len(duplicadas) > 5 else "")
                    log(f"{file_path.name}: {len(duplicadas)} unidades repetidas "
                        f"(se conservó la primera): {muestra}", LogLevel.ERROR, "WARN")
                    if lineage is not None:
                        for unidad in duplicadas:
                            lineage.dedup_intra.append((unidad, fecha, file_path.name))
                    df = df.loc[~dup_mask].reset_index(drop=True)

                archivo = ArchivoCedula(
                    nombre=file_path.name,
                    fecha=fecha,
                    variante=parsed.variante,
                    mtime=datetime.fromtimestamp(file_path.stat().st_mtime),
                    filas=len(df),
                )
                por_fecha.setdefault(fecha, []).append((archivo, df))

            except Exception as e:
                log(f"Error procesando {file_path.name}: {e}", LogLevel.ERROR, "ERR")
                return None

        consolidated_cedulas = []
        for fecha in sorted(por_fecha):
            entradas = por_fecha[fecha]
            if len(entradas) == 1:
                entradas[0][0].rol = 'unico'
                consolidated_cedulas.append(entradas[0][1])
            else:
                consolidated_cedulas.append(_fusionar_cedulas_mismo_dia(entradas, log, lineage))
            if lineage is not None:
                lineage.archivos.extend(a for a, _df in entradas)

        df_cedulas = pd.concat(consolidated_cedulas, ignore_index=True)
        fechas_fisicas = sorted(por_fecha)

        # Gap-filler Drive (v0.6.5): completar fechas del rango de viajes que
        # no tienen archivo fisico. Solo fechas faltantes — lo fisico manda.
        fechas_drive: list = []
        if gap_fetcher is not None and fecha_min is not None and fecha_max is not None:
            fisicas_dates = {f.date() if hasattr(f, 'date') else f for f in fechas_fisicas}
            n_dias = (fecha_max - fecha_min).days + 1
            faltantes = [fecha_min + timedelta(days=i) for i in range(n_dias)]
            faltantes = [d for d in faltantes if d not in fisicas_dates]
            if faltantes:
                log(f"Fechas del rango de viajes sin archivo físico: {len(faltantes)} "
                    "→ consultando historial Drive", code="REV")
                try:
                    descargadas = gap_fetcher(faltantes) or {}
                except Exception as e:
                    log(f"Relleno Drive falló ({e}); las fechas faltantes quedan a "
                        "forward-fill", LogLevel.ERROR, "WARN")
                    if lineage is not None:
                        lineage.advertencias.append(f"Relleno Drive falló: {e}")
                    descargadas = {}

                # Solo fechas realmente solicitadas: lo fisico es intocable
                # por construccion, aunque el fetcher devolviera de mas.
                faltantes_set = set(faltantes)
                descargadas = {d: v for d, v in descargadas.items() if d in faltantes_set}

                for d in sorted(descargadas):
                    df_d = descargadas[d]
                    if df_d is None or df_d.empty:
                        continue
                    if not all(c in df_d.columns for c in required_cols):
                        log(f"Día Drive {d}: columnas incompletas, se omite "
                            "(queda a forward-fill)", LogLevel.ERROR, "WARN")
                        continue
                    df_d = df_d.drop_duplicates(subset=['Unidades'], keep='first')
                    df_cedulas = pd.concat([df_cedulas, df_d], ignore_index=True)
                    fechas_drive.append(d)

                log(f"Historial Drive aportó {len(fechas_drive)} de {len(faltantes)} "
                    "fechas faltantes; el resto queda a forward-fill", code="COV")

        # Invariante v0.6.4: (Unidades, Fecha) unico tras consolidar — un
        # duplicado aqui multiplica viajes en el merge aguas abajo, asi que
        # es preferible no generar reporte a generar uno inflado.
        clave = (df_cedulas['Unidades'].astype(str).str.strip().str.upper()
                 + '|' + df_cedulas['Fecha Cedula_dt'].astype(str))
        duplicados = clave[clave.duplicated()]
        if not duplicados.empty:
            log(f"Invariante violado: {duplicados.nunique()} pares (Unidad, Fecha) "
                f"duplicados tras consolidar (ej. {duplicados.iloc[0]}) — "
                "abortando para no duplicar viajes", LogLevel.ERROR, "ERR")
            return None

        df_cedulas = fill_missing_dates(df_cedulas)

        if lineage is not None:
            lineage.fechas_fisicas = list(fechas_fisicas)
            # Registro idempotente: fetch_dates_from_revisions ya registra las
            # fechas Drive cuando recibe lineage; esto cubre fetchers que no.
            for d in fechas_drive:
                if d not in lineage.fechas_drive:
                    lineage.fechas_drive.append(d)
            cubiertas_ts = {pd.Timestamp(f) for f in fechas_fisicas}
            cubiertas_ts |= {pd.Timestamp(d) for d in fechas_drive}
            lineage.fechas_ffill = sorted(
                pd.Timestamp(d) for d in pd.to_datetime(df_cedulas['Fecha Cedula_dt']).unique()
                if pd.Timestamp(d) not in cubiertas_ts
            )

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
    # Dos archivos locales que resuelvan a la misma fecha duplicarian la
    # clave y el merge left multiplicaria filas del primario (producto
    # cartesiano de viajes aguas abajo).
    df_local_subset = df_local_subset.drop_duplicates(subset=merge_cols, keep='first')

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

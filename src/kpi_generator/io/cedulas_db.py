"""Cargador de cédulas desde PostgreSQL — reemplaza `load_daily_cedulas` cuando
`CEDULAS_SOURCE=db`.

Contrato de retorno IDÉNTICO a `DataProcessor.load_daily_cedulas`:
DataFrame con columnas:
    Unidades, Gerencia, Operación, Tipo de Unidad, Circuito, Operando,
    Fecha Cedula, Fecha Cedula_dt

Las 6 columnas de negocio son las únicas que el pipeline (processor.py +
comodato.py + change_tracker.py) consume; cualquier columna adicional del
Excel original (no_operador, operador, observaciones) no afecta downstream.

Además devuelve un DataFrame de auditoría que documenta qué (unidad, día) fue
rellenado por forward-fill vs leído real de la BD.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from kpi_generator.config import Config
from kpi_generator.io.postgres import get_connection


# Columnas que el resto del pipeline espera (contrato de load_daily_cedulas)
EXCEL_COLUMNS = ["Unidades", "Gerencia", "Operación", "Tipo de Unidad", "Circuito", "Operando"]

# Mapeo BD (snake_case) → Excel (contrato)
DB_TO_EXCEL = {
    "unidades": "Unidades",
    "gerencia": "Gerencia",
    "operacion": "Operación",
    "tipo_unidad": "Tipo de Unidad",
    "circuito": "Circuito",
    "estatus_2": "Operando",  # confirmado: estatus_2 ↔ Operando
}

# Sentinel column injected by the SQL query to distinguish seed rows from real ones.
_ORIGEN_COL = "origen"


def _build_query(schema: str, table: str) -> sql.Composed:
    """Compone el query con identificadores seguros (psycopg2.sql.Identifier).

    Evita SQL injection si Config.PG_CEDULA_SCHEMA/TABLE fueran manipulados.
    """
    return sql.SQL("""
WITH ultima_previa AS (
  SELECT DISTINCT ON (unidades)
    unidades, gerencia, operacion, tipo_unidad, circuito,
    estatus_2,
    fecha::date AS fecha_dia,
    'previa'    AS origen
  FROM {schema}.{table}
  WHERE fecha::date < %(fecha_min)s
  ORDER BY unidades, fecha::timestamp DESC
),
dentro_rango AS (
  SELECT DISTINCT ON (unidades, fecha::date)
    unidades, gerencia, operacion, tipo_unidad, circuito,
    estatus_2,
    fecha::date AS fecha_dia,
    'rango'     AS origen
  FROM {schema}.{table}
  WHERE fecha::date BETWEEN %(fecha_min)s AND %(fecha_max)s
  ORDER BY unidades, fecha::date, fecha::timestamp DESC
)
SELECT * FROM ultima_previa
UNION ALL
SELECT * FROM dentro_rango
ORDER BY unidades, fecha_dia;
""").format(schema=sql.Identifier(schema), table=sql.Identifier(table))


def load_cedulas_from_db(
    fecha_min: date,
    fecha_max: date,
    log_func: Callable[[str], None] = print,
    schema: str | None = None,
    table: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carga cédulas desde Postgres y devuelve un DataFrame equivalente al de Excel.

    Args:
        fecha_min: primera fecha del rango (inclusiva, derivada de zmov.XLSX)
        fecha_max: última fecha del rango (inclusiva)
        log_func: callback para logging
        schema, table: override del schema/tabla (default desde Config)

    Returns:
        (df_cedulas, df_fechas_rellenadas):
          - df_cedulas: una fila por (Unidades, Fecha Cedula_dt) en el rango,
            con las 6 columnas del contrato + Fecha Cedula + Fecha Cedula_dt
          - df_fechas_rellenadas: registro auxiliar con columnas
            [Unidades, Fecha Cedula, Origen ('real'|'forward_fill'),
             Fecha Cedula Origen]
    """
    schema = schema or Config.PG_CEDULA_SCHEMA
    table = table or Config.PG_CEDULA_TABLE

    log_func(f"[DB] Consultando {schema}.{table} para rango "
             f"{fecha_min.isoformat()} a {fecha_max.isoformat()}")

    query = _build_query(schema, table)

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, {"fecha_min": fecha_min, "fecha_max": fecha_max})
            rows = cur.fetchall()

    if not rows:
        log_func("[DB] Query devolvió 0 filas — la BD no tiene cobertura del rango")
        return _empty_result()

    df_raw = pd.DataFrame(rows)
    n_previa = int((df_raw[_ORIGEN_COL] == 'previa').sum())
    n_rango = int((df_raw[_ORIGEN_COL] == 'rango').sum())
    log_func(f"[DB] Recibidas {len(df_raw)} filas crudas "
             f"({n_previa} semillas previas, {n_rango} dentro de rango)")

    df_cedulas, df_audit = build_daily_snapshot(df_raw, fecha_min, fecha_max)

    n_real = int((df_audit['Origen'] == 'real').sum())
    n_ffill = int((df_audit['Origen'] == 'forward_fill').sum())
    log_func(f"[DB] Snapshot diario: {len(df_cedulas)} filas "
             f"({n_ffill} rellenadas, {n_real} reales)")

    return df_cedulas, df_audit


def build_daily_snapshot(
    df_raw: pd.DataFrame,
    fecha_min: date,
    fecha_max: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconstruye un snapshot diario por unidad usando forward-fill (vectorizado).

    Para cada (unidad, día) en el rango:
      - Si hay registro real ese día (origen='rango' con fecha exacta) → úsalo
      - Si no, replica el último registro previo conocido (forward-fill)
      - Si no hay previo en absoluto, omite la fila (la unidad aún no existía)

    La implementación usa `MultiIndex.from_product` + `reindex` + `groupby.ffill`
    para evitar loops Python puros. Complejidad ~O((N+M) log N) en vez de O(N*M).

    Expuesta como API pública (sin guión bajo) para que los tests unitarios la
    puedan ejercitar con datos sintéticos sin pasar por la BD.
    """
    if df_raw.empty:
        return _empty_result()

    df = df_raw.rename(columns=DB_TO_EXCEL).copy()
    df["Fecha Cedula_dt"] = pd.to_datetime(df["fecha_dia"])

    # Detectar duplicados (unidad, fecha) que podrían venir si la query no
    # garantiza unicidad — por seguridad nos quedamos con el último (mayor fecha_ts)
    df = df.drop_duplicates(subset=["Unidades", "Fecha Cedula_dt"], keep="last")

    rango_completo = pd.date_range(start=fecha_min, end=fecha_max, freq="D")
    unidades = df["Unidades"].unique()

    # Incluir fechas de semillas previas (anteriores al rango) en el índice para
    # que el ffill las propague hacia adelante. Si solo construimos el índice con
    # `rango_completo`, las semillas previas desaparecen tras el reindex.
    fechas_previas = pd.to_datetime(
        df.loc[df[_ORIGEN_COL] == 'previa', "Fecha Cedula_dt"].unique()
    )
    fechas_indice = pd.DatetimeIndex(
        sorted(set(fechas_previas).union(set(rango_completo)))
    )

    # Index canónico (unidad × día) para reindex
    idx = pd.MultiIndex.from_product(
        [unidades, fechas_indice],
        names=["Unidades", "Fecha Cedula_dt"],
    )

    # Set-index + reindex llena con NaN los pares (unidad, día) sin dato
    cols_a_propagar = EXCEL_COLUMNS[1:]  # todas menos Unidades (es índice)
    df_indexed = df.set_index(["Unidades", "Fecha Cedula_dt"])[cols_a_propagar + [_ORIGEN_COL, "fecha_dia"]]
    df_full = df_indexed.reindex(idx)

    # Forward-fill por unidad (groupby preserva fronteras entre unidades)
    df_full = df_full.groupby(level="Unidades").ffill()

    # Marcamos origen: 'real' si la fecha del registro fuente coincide con el día del índice
    df_full = df_full.reset_index()
    df_full["_origen_dia"] = pd.to_datetime(df_full["fecha_dia"])
    df_full["Origen"] = pd.Series(
        ["real" if a == b else "forward_fill"
         for a, b in zip(df_full["Fecha Cedula_dt"], df_full["_origen_dia"])],
        index=df_full.index,
    )

    # Recortar al rango pedido (descartar filas de fechas previas que solo eran semillas)
    df_full = df_full[
        (df_full["Fecha Cedula_dt"] >= pd.Timestamp(fecha_min)) &
        (df_full["Fecha Cedula_dt"] <= pd.Timestamp(fecha_max))
    ]

    # Tirar filas sin dato propagado (unidad no existía aún al inicio del rango)
    df_full = df_full.dropna(subset=cols_a_propagar, how="all")

    # Si quedó alguna fila con dato pero Origen NaN (porque _origen_dia es NaT),
    # eso solo pasa si la semilla 'previa' nunca tuvo fecha_dia válida; las tratamos
    # como forward_fill por defecto.
    df_full["Origen"] = df_full["Origen"].fillna("forward_fill")

    # Construir el DataFrame de snapshot con el contrato Excel
    df_snapshot = df_full[["Unidades", "Fecha Cedula_dt"] + cols_a_propagar].copy()
    df_snapshot["Fecha Cedula"] = df_snapshot["Fecha Cedula_dt"].dt.strftime("%d/%m/%Y")
    df_snapshot = df_snapshot[EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"]]
    df_snapshot = df_snapshot.sort_values(["Unidades", "Fecha Cedula_dt"]).reset_index(drop=True)

    # Construir auditoría
    df_audit = pd.DataFrame({
        "Unidades": df_full["Unidades"].values,
        "Fecha Cedula": df_full["Fecha Cedula_dt"].dt.strftime("%d/%m/%Y").values,
        "Origen": df_full["Origen"].values,
        "Fecha Cedula Origen": df_full["_origen_dia"].dt.strftime("%d/%m/%Y").fillna("").values,
    }).reset_index(drop=True)

    return df_snapshot, df_audit


def _empty_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve DataFrames vacíos con el shape correcto."""
    df_snapshot = pd.DataFrame(columns=EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"])
    df_audit = pd.DataFrame(columns=["Unidades", "Fecha Cedula", "Origen", "Fecha Cedula Origen"])
    return df_snapshot, df_audit

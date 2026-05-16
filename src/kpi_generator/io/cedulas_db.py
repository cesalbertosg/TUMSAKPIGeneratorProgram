"""Cargador de cédulas desde PostgreSQL — reemplaza `load_daily_cedulas` cuando
`CEDULAS_SOURCE=db`.

Contrato de retorno IDÉNTICO a `DataProcessor.load_daily_cedulas`:
DataFrame con columnas:
    Unidades, Gerencia, Operación, Tipo de Unidad, Circuito, Operando,
    Fecha Cedula, Fecha Cedula_dt

Además devuelve un DataFrame de auditoría que documenta qué (unidad, día) fue
rellenado por forward-fill vs leído real de la BD.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import pandas as pd
from psycopg2.extras import RealDictCursor

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


_QUERY = """
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
"""


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
    from kpi_generator.config import Config

    schema = schema or Config.PG_CEDULA_SCHEMA
    table = table or Config.PG_CEDULA_TABLE

    log_func(f"[DB] Consultando {schema}.{table} para rango "
             f"{fecha_min.isoformat()} → {fecha_max.isoformat()}")

    query = _QUERY.format(schema=schema, table=table)

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, {"fecha_min": fecha_min, "fecha_max": fecha_max})
            rows = cur.fetchall()

    if not rows:
        log_func("[DB] Query devolvió 0 filas — la BD no tiene cobertura del rango")
        return _empty_result()

    df_raw = pd.DataFrame(rows)
    log_func(f"[DB] Recibidas {len(df_raw)} filas crudas "
             f"({(df_raw['origen'] == 'previa').sum()} semillas previas, "
             f"{(df_raw['origen'] == 'rango').sum()} dentro de rango)")

    df_cedulas, df_audit = _build_daily_snapshot(df_raw, fecha_min, fecha_max, log_func)

    log_func(f"[DB] Snapshot diario: {len(df_cedulas)} filas "
             f"({(df_audit['Origen'] == 'forward_fill').sum()} rellenadas, "
             f"{(df_audit['Origen'] == 'real').sum()} reales)")

    return df_cedulas, df_audit


def _build_daily_snapshot(
    df_raw: pd.DataFrame,
    fecha_min: date,
    fecha_max: date,
    log_func: Callable[[str], None],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconstruye un snapshot diario por unidad usando forward-fill.

    Para cada (unidad, día) en el rango:
      - Si hay registro en `dentro_rango` para esa fecha exacta, úsalo (origen='real')
      - Si no, usa el último registro previo (origen='forward_fill')
      - Si no hay previo y la unidad aparece más tarde en el rango, se omite
        para ese día (la unidad aún no existía en la flota)
    """
    df_raw = df_raw.rename(columns=DB_TO_EXCEL)
    df_raw["Fecha Cedula_dt"] = pd.to_datetime(df_raw["fecha_dia"])
    df_raw = df_raw.sort_values(["Unidades", "Fecha Cedula_dt"]).reset_index(drop=True)

    rango_completo = pd.date_range(start=fecha_min, end=fecha_max, freq="D")
    unidades = df_raw["Unidades"].unique()

    snapshot_rows = []
    audit_rows = []

    for unidad in unidades:
        sub = df_raw[df_raw["Unidades"] == unidad].copy()

        ultima = None
        ultima_fecha_origen = None
        for fecha in rango_completo:
            real = sub[sub["Fecha Cedula_dt"] == fecha]
            if not real.empty:
                row = real.iloc[0].to_dict()
                ultima = row
                ultima_fecha_origen = fecha.date()
                origen = "real"
            elif ultima is not None:
                row = dict(ultima)
                row["Fecha Cedula_dt"] = fecha
                origen = "forward_fill"
            else:
                continue

            snapshot_row = {col: row[col] for col in EXCEL_COLUMNS}
            snapshot_row["Fecha Cedula_dt"] = fecha
            snapshot_row["Fecha Cedula"] = fecha.strftime("%d/%m/%Y")
            snapshot_rows.append(snapshot_row)

            audit_rows.append({
                "Unidades": unidad,
                "Fecha Cedula": fecha.strftime("%d/%m/%Y"),
                "Origen": origen,
                "Fecha Cedula Origen": (ultima_fecha_origen.strftime("%d/%m/%Y")
                                         if ultima_fecha_origen else ""),
            })

    df_snapshot = pd.DataFrame(snapshot_rows)
    df_audit = pd.DataFrame(audit_rows)

    if not df_snapshot.empty:
        df_snapshot = df_snapshot[EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"]]
        df_snapshot = df_snapshot.sort_values(["Unidades", "Fecha Cedula_dt"]).reset_index(drop=True)

    return df_snapshot, df_audit


def _empty_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve DataFrames vacíos con el shape correcto."""
    df_snapshot = pd.DataFrame(columns=EXCEL_COLUMNS + ["Fecha Cedula", "Fecha Cedula_dt"])
    df_audit = pd.DataFrame(columns=["Unidades", "Fecha Cedula", "Origen", "Fecha Cedula Origen"])
    return df_snapshot, df_audit

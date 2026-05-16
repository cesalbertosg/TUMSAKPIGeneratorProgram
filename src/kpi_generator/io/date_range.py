"""Derivar el rango de fechas [min, max] a partir del archivo de viajes (zmov.XLSX).

Optimizado para no cargar el DataFrame completo: lee solo la columna
'Fecha creación' usando `pd.read_excel(..., usecols=...)`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

FECHA_COL = "Fecha creación"


class DateRangeError(RuntimeError):
    """El archivo de viajes no contiene fechas válidas."""


def derive_date_range(trips_file: str | Path) -> tuple[date, date]:
    """Devuelve (fecha_min, fecha_max) según la columna 'Fecha creación' del Excel de viajes.

    Lee solo esa columna para minimizar I/O. Levanta `DateRangeError` si la
    columna no existe o no contiene fechas parseables.
    """
    path = Path(trips_file)
    if not path.exists():
        raise DateRangeError(f"Archivo de viajes no encontrado: {path}")

    try:
        df = pd.read_excel(path, usecols=[FECHA_COL])
    except ValueError as e:
        raise DateRangeError(
            f"Columna '{FECHA_COL}' no encontrada en {path.name}. Error: {e}"
        ) from e

    fechas = pd.to_datetime(df[FECHA_COL], errors="coerce").dropna()
    if fechas.empty:
        raise DateRangeError(
            f"La columna '{FECHA_COL}' en {path.name} no tiene fechas válidas"
        )

    return fechas.min().date(), fechas.max().date()

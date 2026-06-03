"""Contexto temporal del periodo analizado.

`PeriodContext` centraliza las tres variables que dictan el corte de calculo
del KPI v0.5.0:

- `dias_mes`        — dias totales del mes calendario analizado (28-31).
- `dias_corrientes` — dia 1 del mes hasta la fecha del ultimo viaje global
                      (inclusive). Es el corte de la corrida.
- `dias_restantes`  — dias_mes - dias_corrientes. Usado para proyecciones.

Pre-condicion: el archivo `zmov.XLSX` cubre un solo mes calendario. Si hay
viajes en multiples meses, `from_trips` lanza `ValueError` — el caller debe
filtrar o particionar antes.

Ejemplo:
    >>> import pandas as pd
    >>> df = pd.DataFrame({'Fecha creación': pd.to_datetime(['2026-06-02 09:15'])})
    >>> ctx = PeriodContext.from_trips(df)
    >>> ctx.dias_mes, ctx.dias_corrientes, ctx.dias_restantes
    (30, 2, 28)
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass

import pandas as pd

# Columna canonica de fecha en `zmov.XLSX` (tal como la importa pandas).
TRIPS_DATE_COL = 'Fecha creación'


@dataclass(frozen=True)
class PeriodContext:
    """Contexto temporal inmutable del mes en analisis.

    Construyase via `from_trips(df_trips)`; el constructor base no valida
    coherencia entre `anio`/`mes` y `fecha_ultimo_viaje`.
    """

    anio: int
    mes: int  # 1-12
    fecha_ultimo_viaje: pd.Timestamp  # ultimo viaje global (con o sin tiempo)

    def __post_init__(self) -> None:
        if not 1 <= self.mes <= 12:
            raise ValueError(f"mes debe estar en [1, 12], llego {self.mes}")
        if self.fecha_ultimo_viaje.year != self.anio or self.fecha_ultimo_viaje.month != self.mes:
            raise ValueError(
                f"fecha_ultimo_viaje ({self.fecha_ultimo_viaje.date()}) "
                f"no pertenece al mes {self.anio}-{self.mes:02d}"
            )

    @property
    def dias_mes(self) -> int:
        """Dias totales del mes calendario (28, 29, 30 o 31)."""
        return calendar.monthrange(self.anio, self.mes)[1]

    @property
    def dias_corrientes(self) -> int:
        """Dia del mes del ultimo viaje global (= dia 1..dias_mes)."""
        return self.fecha_ultimo_viaje.day

    @property
    def dias_restantes(self) -> int:
        """Dias del mes posteriores al corte (puede ser 0 al cierre)."""
        return self.dias_mes - self.dias_corrientes

    @property
    def fecha_inicio_mes(self) -> pd.Timestamp:
        """Primer dia del mes, normalizado a 00:00."""
        return pd.Timestamp(year=self.anio, month=self.mes, day=1)

    @property
    def fecha_fin_mes(self) -> pd.Timestamp:
        """Ultimo dia del mes, normalizado a 00:00."""
        return pd.Timestamp(year=self.anio, month=self.mes, day=self.dias_mes)

    @property
    def fecha_corte(self) -> pd.Timestamp:
        """Fecha del corte de analisis (normalizada a dia, sin tiempo)."""
        return pd.Timestamp(self.fecha_ultimo_viaje.date())

    def rango_corriente(self) -> pd.DatetimeIndex:
        """Rango diario [dia 1 del mes, fecha_corte] (ambos inclusive)."""
        return pd.date_range(self.fecha_inicio_mes, self.fecha_corte, freq='D')

    @classmethod
    def from_trips(cls, df_trips: pd.DataFrame,
                   date_col: str = TRIPS_DATE_COL) -> "PeriodContext":
        """Deriva el contexto desde el DataFrame de viajes.

        Reglas:
        - `df_trips` debe tener al menos una fila con fecha valida.
        - Todas las fechas deben pertenecer al mismo mes calendario.
        - El corte es el MAXIMO de las fechas de viaje.
        """
        if date_col not in df_trips.columns:
            raise ValueError(f"falta columna '{date_col}' en df_trips")
        fechas = pd.to_datetime(df_trips[date_col], errors='coerce').dropna()
        if fechas.empty:
            raise ValueError(f"df_trips no tiene fechas validas en '{date_col}'")

        meses = fechas.dt.to_period('M').unique()
        if len(meses) > 1:
            meses_str = ', '.join(str(m) for m in sorted(meses))
            raise ValueError(
                f"df_trips abarca varios meses ({meses_str}); el pipeline analiza un solo mes"
            )

        ultimo = fechas.max()
        return cls(
            anio=int(ultimo.year),
            mes=int(ultimo.month),
            fecha_ultimo_viaje=pd.Timestamp(ultimo),
        )

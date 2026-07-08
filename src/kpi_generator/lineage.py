"""Trazabilidad de la carga de cédulas (v0.6.4).

Cada corrida acumula en `CedulaLineage` qué fuente efectiva se usó, qué
carpeta, qué archivos se cargaron (y con qué rol cuando hubo fusión de
duplicados del mismo día), qué fechas quedaron cubiertas por físico /
Drive / forward-fill, y qué fallbacks o advertencias ocurrieron.

El objeto se crea en `DataProcessor.load_data`, se pasa como parámetro
acumulador a los loaders (`io.excel.load_daily_cedulas`,
`io.sheets.load_cedulas_for_period`) y termina en dos lugares:
- la hoja "Fuente Cedulas" del Excel de salida (`to_dataframe`), y
- el log GUI/CLI (`resumen_linea`).

Módulo top-level sin dependencias del paquete (solo pandas) para que
`io/` y `domain/` puedan importarlo sin ciclos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

# Columnas de la hoja "Fuente Cedulas".
LINEAGE_SHEET_COLUMNS = [
    'Categoría', 'Fecha', 'Archivo', 'Variante', 'Rol', 'Filas', 'Modificado', 'Detalle',
]


def _fmt_fecha(value) -> str:
    """dd/mm/yyyy tolerante a date/datetime/Timestamp/None."""
    if value is None:
        return ''
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    return str(value)


@dataclass
class ArchivoCedula:
    """Un archivo físico de cédula leído durante la carga."""

    nombre: str
    fecha: object            # date/datetime de la fecha parseada del nombre
    variante: str            # 'diario' | 'variante'
    mtime: datetime
    filas: int
    rol: str = 'unico'       # 'unico' | 'base' | 'complemento' | 'descartado'
    detalle: str = ''


@dataclass
class CedulaLineage:
    """Acumulador de trazabilidad de una corrida de carga de cédulas."""

    fuente_solicitada: str
    fuente_efectiva: str = ''
    carpeta: str | None = None
    archivos: list[ArchivoCedula] = field(default_factory=list)
    fechas_fisicas: list = field(default_factory=list)
    fechas_ffill: list = field(default_factory=list)
    fechas_drive: list = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    carpeta_mixta: bool = False
    # (unidad, fecha, campo) completados por fusión diario+variante del mismo
    # día — el processor los vuelca a la hoja "Inconsistencias".
    fusion_fills: list[tuple] = field(default_factory=list)
    # (unidad, fecha, archivo) de filas descartadas por unidad duplicada
    # dentro de un mismo archivo físico (keep-first).
    dedup_intra: list[tuple] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _archivos_activos(self) -> list[ArchivoCedula]:
        return [a for a in self.archivos if a.rol != 'descartado']

    def _origen_por_fecha(self) -> dict:
        """Mapa fecha → origen ('fisico-diario'|'fisico-variante'|'fusion'|'drive'|'ffill')."""
        por_fecha: dict = {}
        conteo: dict = {}
        for a in self._archivos_activos():
            key = _fmt_fecha(a.fecha)
            conteo[key] = conteo.get(key, 0) + 1
            por_fecha[key] = 'fisico-diario' if a.variante == 'diario' else 'fisico-variante'
        for key, n in conteo.items():
            if n > 1:
                por_fecha[key] = 'fusion'
        for d in self.fechas_drive:
            por_fecha[_fmt_fecha(d)] = 'drive'
        for d in self.fechas_ffill:
            por_fecha[_fmt_fecha(d)] = 'ffill'
        # Fechas físicas sin archivo registrado (p. ej. fuente sheets, que no
        # construye ArchivoCedula por revisión) quedan como 'fisico'.
        for d in self.fechas_fisicas:
            por_fecha.setdefault(_fmt_fecha(d), 'fisico')
        return por_fecha

    def to_dataframe(self) -> pd.DataFrame:
        """Tabla plana para la hoja "Fuente Cedulas" (bloques por Categoría)."""
        try:
            from kpi_generator import __version__ as _version
        except Exception:
            _version = '?'

        def fila(categoria: str, fecha: str = '', archivo: str = '', variante: str = '',
                 rol: str = '', filas='', modificado: str = '', detalle: str = '') -> dict:
            return {
                'Categoría': categoria, 'Fecha': fecha, 'Archivo': archivo,
                'Variante': variante, 'Rol': rol, 'Filas': filas,
                'Modificado': modificado, 'Detalle': detalle,
            }

        rows = [
            fila('CORRIDA', detalle=f"Fuente solicitada: {self.fuente_solicitada or '?'}"),
            fila('CORRIDA', detalle=f"Fuente efectiva: {self.fuente_efectiva or '?'}"),
            fila('CORRIDA', detalle=f"Carpeta de cédulas: {self.carpeta or '(sin carpeta)'}"),
            fila('CORRIDA', detalle=(
                f"KPI Generator v{_version} | Generado: "
                f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )),
        ]
        for fb in self.fallbacks:
            rows.append(fila('FALLBACK', detalle=fb))
        if self.carpeta_mixta:
            rows.append(fila('ADVERTENCIA', detalle=(
                'Carpeta mixta: conviven archivos diarios y variantes (Completa) — '
                'el diario manda; la variante solo rellenó vacíos'
            )))
        for adv in self.advertencias:
            rows.append(fila('ADVERTENCIA', detalle=adv))
        for a in sorted(self.archivos, key=lambda x: (_fmt_fecha(x.fecha)[6:10],
                                                      _fmt_fecha(x.fecha)[3:5],
                                                      _fmt_fecha(x.fecha)[0:2],
                                                      x.nombre)):
            rows.append(fila(
                'ARCHIVO', fecha=_fmt_fecha(a.fecha), archivo=a.nombre,
                variante=a.variante, rol=a.rol, filas=a.filas,
                modificado=a.mtime.strftime('%d/%m/%Y %H:%M') if a.mtime else '',
                detalle=a.detalle,
            ))
        origenes = self._origen_por_fecha()
        for key in sorted(origenes, key=lambda k: (k[6:10], k[3:5], k[0:2])):
            rows.append(fila('FECHA', fecha=key, detalle=f"Origen: {origenes[key]}"))

        return pd.DataFrame(rows, columns=LINEAGE_SHEET_COLUMNS)

    def resumen_linea(self) -> str:
        """Resumen de una línea para log `[SRC]`, CLI y diálogo de éxito de la GUI."""
        partes = [f"Fuente efectiva: {(self.fuente_efectiva or '?').upper()}"]
        if self.carpeta:
            partes.append(f"Carpeta: {self.carpeta}")
        activos = self._archivos_activos()
        if activos:
            n_diarios = sum(1 for a in activos if a.variante == 'diario')
            n_variantes = sum(1 for a in activos if a.variante == 'variante')
            partes.append(f"{len(activos)} archivos ({n_diarios} diarios, {n_variantes} variantes)")
        if self.fechas_fisicas:
            try:
                reales = sorted(self.fechas_fisicas)
            except TypeError:
                reales = list(self.fechas_fisicas)
            partes.append(f"físico {_fmt_fecha(reales[0])}→{_fmt_fecha(reales[-1])} ({len(reales)} días)")
        if self.fechas_drive:
            partes.append(f"{len(self.fechas_drive)} días Drive")
        partes.append(f"{len(self.fechas_ffill)} días ffill")
        if self.carpeta_mixta:
            partes.append("CARPETA MIXTA")
        if self.fallbacks:
            partes.append(f"FALLBACK: {'; '.join(self.fallbacks)}")
        return " | ".join(partes)

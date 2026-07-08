"""Configuración centralizada del sistema KPI Generator.

Carga variables desde `.env`. Las variables identificadoras (IDs de Sheets,
host/BD de Postgres) no tienen default — deben estar definidas en `.env`.
Las variables operativas (rutas, modos, timeouts) conservan defaults seguros.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    return _PROJECT_ROOT


class LogLevel(Enum):
    ERROR = 1
    INFO = 2
    DEBUG = 3


class Config:
    """Configuración centralizada del sistema KPI Generator."""

    COLUMNS = {
        "trips": [
            "Número de Viaje", "Fecha creación", "Centro", "Tipo De Operación",
            "KMLiqCargadoFinal", "KMLiqVacioFinal", "Ruta", "Denominación",
            "Alias Origen", "Alias Destino", "ClaveCategoria", "Distancia",
            "StatusViaje", "Equipo Motriz", "Equipo Remolque 1", "Equipo Dolly",
            "Equipo Remolque 2",
        ],
        "fuel": [
            "Número de Viaje", "Equipo Motriz", "Fecha carga combustible",
            "Cantidad Litros Real", "Precio Unitario Real", "Importe Total Real", "StatusVale",
        ],
        "units": ["Unidades", "Gerencia", "Operación", "Tipo de Unidad", "Circuito", "Operando"],
        "units_extra": ["Operador", "No Operador", "Estatus Operador", "Observaciones"],
        "objectives": ["Gerencia", "Operación Cedula", "Objetivo KM", "Objetivo Viajes"],
    }

    # Alias de nombres de columna por fuente (case-sensitive a propósito:
    # 'Estatus' del archivo "Completa" — códigos cortos A/I/B/S — es distinto
    # de 'ESTATUS' — vocabulario de status, igual que Operando).
    CEDULA_COLUMN_ALIASES = {
        'Unidad': 'Unidades',
        'UNIDAD': 'Unidades',
        'ESTATUS': 'Operando',
        'ESTATUS2': 'Operando',
        'Estatus': 'Estatus Operador',
        'OPERADOR': 'Operador',
        'NO OPERADOR': 'No Operador',
        'OBSERVACIONES': 'Observaciones',
    }

    # Valores por defecto cuando un campo categórico de cédula viene vacío.
    CEDULA_FIELD_DEFAULTS = {
        'Gerencia': 'Pendiente',
        'Operación': 'SIN ASIGNAR',
        'Circuito': 'TERCERO',
    }

    # Heurística por prefijo de número económico cuando no hay info de viajes
    # para inferir 'Tipo de Unidad'.
    CEDULA_TIPO_UNIDAD_POR_PREFIJO = {
        'L': 'CAMIONETA',
        'C': 'TORTHON',
        'T': 'SENCILLO',
    }

    SPECIAL_CIRCUITS = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}

    CREDENTIALS_PATH = str(
        _project_root() / os.getenv("GOOGLE_CREDENTIALS_PATH", "secrets/google_service_account.json")
    )
    SHEETS_ID = os.getenv("SHEETS_ID_KPI", "")
    CEDULA_SHEET_ID = os.getenv("SHEETS_ID_CEDULAS", "")
    SHEETS_SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    DATA_INPUT_DIR = _project_root() / os.getenv("DATA_INPUT_DIR", "data-input")
    OUTPUTS_DIR = _project_root() / os.getenv("OUTPUTS_DIR", "Outputs")

    # --- Fuente de cédulas: "db" | "excel" | "sheets" ---
    # Default "db" desde v0.3.0 (Fase 3 — 22/05/2026). La fuente Excel y Sheets
    # se conservan como fallback configurable para casos de VPN caída o validación.
    CEDULAS_SOURCE = os.getenv("CEDULAS_SOURCE", "db").lower()

    # Si CEDULAS_SOURCE=db falla y FALLBACK_ON_DB_ERROR=true, usa FALLBACK_CEDULAS_PATH.
    FALLBACK_ON_DB_ERROR = os.getenv("FALLBACK_ON_DB_ERROR", "false").lower() == "true"
    FALLBACK_CEDULAS_PATH = _project_root() / os.getenv("FALLBACK_CEDULAS_PATH", "data-input/Cedulas")

    # --- GUI ---
    # Paleta de colores: "dark" (default) | "light". Ver `gui/theme.py` para
    # registrar paletas nuevas. Si el nombre no existe, cae a "dark".
    GUI_THEME = os.getenv("KPI_GUI_THEME", "dark").lower()

    # Estado persistente de la GUI (v0.6.4): última fuente de cédulas
    # seleccionada. Vive en APPDATA (fuera del árbol del repo/instalador)
    # para que el dropdown no arranque en el default del .env cada sesión.
    GUI_STATE_PATH = Path(os.getenv("APPDATA", str(Path.home()))) / "KPI Generator" / "gui_state.json"

    # --- Capacidades del runtime ---
    # True si la dependencia opcional `psycopg2` esta instalada y la fuente "db"
    # es posible. False para distribuciones standalone (ej. installer Yaneth)
    # que no necesitan PostgreSQL — la GUI esconde la opcion y el CLI falla
    # con error claro si se solicita explicitamente.
    @staticmethod
    def db_available() -> bool:
        try:
            import psycopg2  # noqa: F401
            return True
        except ImportError:
            return False

    # --- Conexión PostgreSQL Cédula DG ---
    PG_CEDULA_HOST = os.getenv("PG_CEDULA_HOST", "")
    PG_CEDULA_PORT = int(os.getenv("PG_CEDULA_PORT", "5432"))
    PG_CEDULA_DB = os.getenv("PG_CEDULA_DB", "")
    PG_CEDULA_USER = os.getenv("PG_CEDULA_USER", "")
    PG_CEDULA_PASSWORD = os.getenv("PG_CEDULA_PASSWORD", "")
    PG_CEDULA_SCHEMA = os.getenv("PG_CEDULA_SCHEMA", "public")
    PG_CEDULA_TABLE = os.getenv("PG_CEDULA_TABLE", "")

    OUTPUT_COLUMNS = [
        'Fecha Ultima modif', 'Denominación del equipo', 'Tipo de equipo', 'Operación cedula',
        'Unidades', 'Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Estatus',
        'Fecha Inicio', 'Fecha Fin', 'Días Periodo', 'Días Operando', 'Días Disponible', 'Días Gestoría', 'Días Taller',
        '% Operativo',
        'KMLiqCargadoFinal', 'KMLiqVacioFinal', 'KM Total', 'Diesel LTS', 'Viajes', 'Rendimiento',
        'KM/h', 'Densidad Viaje', 'Tendencia KM',
        'Obj KM Diario', 'Obj Viajes Diario', 'Objetivo KM Total', 'Objetivo Viajes Total',
        'Cump. KM periodo', 'Cump. Viaje periodo',
        'Número de Viaje', 'Fecha Ult Viaje', 'Centro', 'Tipo De Operación',
        'Ruta', 'Denominación', 'Alias Origen', 'Alias Destino', 'ClaveCategoria',
    ]

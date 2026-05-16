"""Configuración centralizada del sistema KPI Generator.

Carga variables sensibles desde `.env` cuando existe; cae a defaults
hardcoded para preservar compatibilidad con el monolito original.
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
        "objectives": ["Gerencia", "Operación Cedula", "Objetivo KM", "Objetivo Viajes"],
    }

    SPECIAL_CIRCUITS = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}

    CREDENTIALS_PATH = str(
        _project_root() / os.getenv("GOOGLE_CREDENTIALS_PATH", "secrets/google_service_account.json")
    )
    SHEETS_ID = os.getenv("SHEETS_ID_KPI", "1sv8P004Ej85D_GF4YwEmoBO1XqWR1KYdGOSb1FJWM8Y")
    CEDULA_SHEET_ID = os.getenv("SHEETS_ID_CEDULAS", "18lw2_Rv-j_vwXTwXXGKX5-BZ_8t8MNfz6IHuLaIKBf0")
    SHEETS_SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    DATA_INPUT_DIR = _project_root() / os.getenv("DATA_INPUT_DIR", "data-input")
    OUTPUTS_DIR = _project_root() / os.getenv("OUTPUTS_DIR", "Outputs")

    # --- Fuente de cédulas: "db" | "excel" | "sheets" ---
    # Default "excel" durante migración. Se cambiará a "db" tras validación de Fase 2.
    CEDULAS_SOURCE = os.getenv("CEDULAS_SOURCE", "excel").lower()

    # Si CEDULAS_SOURCE=db falla y FALLBACK_ON_DB_ERROR=true, usa FALLBACK_CEDULAS_PATH.
    FALLBACK_ON_DB_ERROR = os.getenv("FALLBACK_ON_DB_ERROR", "false").lower() == "true"
    FALLBACK_CEDULAS_PATH = _project_root() / os.getenv("FALLBACK_CEDULAS_PATH", "data-input/Cedulas")

    # --- Conexión PostgreSQL Cédula DG ---
    PG_CEDULA_HOST = os.getenv("PG_CEDULA_HOST", "172.17.1.4")
    PG_CEDULA_PORT = int(os.getenv("PG_CEDULA_PORT", "5432"))
    PG_CEDULA_DB = os.getenv("PG_CEDULA_DB", "cedula_direccion")
    PG_CEDULA_USER = os.getenv("PG_CEDULA_USER", "")
    PG_CEDULA_PASSWORD = os.getenv("PG_CEDULA_PASSWORD", "")
    PG_CEDULA_SCHEMA = os.getenv("PG_CEDULA_SCHEMA", "public")
    PG_CEDULA_TABLE = os.getenv("PG_CEDULA_TABLE", "cedula_unidades")

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

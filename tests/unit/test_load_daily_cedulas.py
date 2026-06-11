"""Tests para `io.excel`: cedula versatil + respaldo local + cross-fill.

Cubre el plan "Cedula: fuente versatil + normalizacion + respaldo local +
hoja de inconsistencias":

1. `parse_cedula_filename` reconoce sufijos extra (ej. "Completa").
2. `load_daily_cedulas` aplica `Config.CEDULA_COLUMN_ALIASES` antes de
   validar columnas requeridas.
3. `save_cedula_as_completa` escribe un archivo por fecha sin sobrescribir
   archivos existentes.
4. `load_local_cedulas_for_crossfill` es best-effort (nunca devuelve None).
5. `crossfill_cedulas` completa columnas vacias del primario desde el local.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from kpi_generator.io.excel import (
    crossfill_cedulas,
    load_daily_cedulas,
    load_local_cedulas_for_crossfill,
    parse_cedula_filename,
    save_cedula_as_completa,
)

_NOLOG = lambda *_a, **_k: None  # noqa: E731


# ---------- parse_cedula_filename: sufijos extra ----------

def test_sufijo_completa() -> None:
    assert parse_cedula_filename("Cedula 01062026 Completa.xlsx") == datetime(2026, 6, 1)


def test_sufijo_completa_con_tilde_y_espacios() -> None:
    assert parse_cedula_filename("Cédula 01 06 2026 Completa.xlsx") == datetime(2026, 6, 1)


# ---------- load_daily_cedulas: alias de columnas ----------

def test_load_daily_cedulas_con_columnas_aliasadas(tmp_path) -> None:
    """Archivo formato 'Completa' (Unidad/ESTATUS/OPERADOR/...) se carga sin error."""
    df = pd.DataFrame([
        {
            'Unidad': 'C070',
            'Gerencia': 'CUE',
            'Operación': 'VEND',
            'Tipo de Unidad': 'TRACTOCAMION FULL',
            'Circuito': 'DEDICADO',
            'ESTATUS': 'Operando',
            'OPERADOR': 'Juan Perez',
            'NO OPERADOR': 'OP-12345',
            'OBSERVACIONES': 'Ninguna',
        },
    ])
    df.to_excel(tmp_path / "Cedula 01062026 Completa.xlsx", engine='openpyxl', index=False)

    result = load_daily_cedulas(str(tmp_path), _NOLOG)

    assert result is not None
    assert (result['Unidades'] == 'C070').all()
    assert (result['Operando'] == 'Operando').all()
    assert (result['Operador'] == 'Juan Perez').all()
    assert (result['No Operador'] == 'OP-12345').all()
    assert (result['Observaciones'] == 'Ninguna').all()


# ---------- save_cedula_as_completa ----------

def _df_cedulas_dos_fechas() -> pd.DataFrame:
    return pd.DataFrame([
        {
            'Unidades': 'C070', 'Gerencia': 'CUE', 'Operación': 'VEND',
            'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Fecha Cedula': '01/06/2026', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
        },
        {
            'Unidades': 'C070', 'Gerencia': 'CUE', 'Operación': 'VEND',
            'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Taller',
            'Fecha Cedula': '02/06/2026', 'Fecha Cedula_dt': pd.Timestamp('2026-06-02'),
        },
    ])


def test_save_cedula_as_completa_escribe_un_archivo_por_fecha(tmp_path) -> None:
    save_cedula_as_completa(_df_cedulas_dos_fechas(), str(tmp_path), _NOLOG)

    archivos = sorted(p.name for p in tmp_path.glob("*.xlsx"))
    assert "Cedula 01062026 Completa.xlsx" in archivos
    assert "Cedula 02062026 Completa.xlsx" in archivos


def test_save_cedula_as_completa_no_sobrescribe_existente(tmp_path) -> None:
    """Si ya existe un archivo cuyo nombre resuelve a esa fecha, no se escribe otro."""
    existente = tmp_path / "Cedula 01062026.xlsx"
    pd.DataFrame([{'Unidades': 'EDITADO'}]).to_excel(existente, engine='openpyxl', index=False)

    save_cedula_as_completa(_df_cedulas_dos_fechas(), str(tmp_path), _NOLOG)

    # No se creo "Cedula 01062026 Completa.xlsx": ya habia un archivo para esa fecha.
    assert not (tmp_path / "Cedula 01062026 Completa.xlsx").exists()
    # El archivo original no se toco.
    df_original = pd.read_excel(existente)
    assert df_original.loc[0, 'Unidades'] == 'EDITADO'
    # La fecha 02/06 si es nueva -> si se escribe.
    assert (tmp_path / "Cedula 02062026 Completa.xlsx").exists()


# ---------- load_local_cedulas_for_crossfill ----------

def test_load_local_crossfill_carpeta_inexistente_devuelve_vacio(tmp_path) -> None:
    result = load_local_cedulas_for_crossfill(str(tmp_path / "no_existe"), _NOLOG)
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_load_local_crossfill_sin_archivos_devuelve_vacio(tmp_path) -> None:
    result = load_local_cedulas_for_crossfill(str(tmp_path), _NOLOG)
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_load_local_crossfill_normaliza_unidades_a_mayusculas(tmp_path) -> None:
    df = pd.DataFrame([
        {
            'Unidades': 'c070', 'Gerencia': 'CUE', 'Operación': 'VEND',
            'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Operador': 'Juan Perez',
        },
    ])
    df.to_excel(tmp_path / "Cedula 01062026 Completa.xlsx", engine='openpyxl', index=False)

    result = load_local_cedulas_for_crossfill(str(tmp_path), _NOLOG)

    assert not result.empty
    assert (result['Unidades'] == 'C070').all()


# ---------- crossfill_cedulas ----------

def test_crossfill_completa_desde_local_sin_pisar_existente() -> None:
    df_primary = pd.DataFrame([
        {
            'Unidades': 'C070', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'CUE', 'Operación': 'VEND', 'Tipo de Unidad': 'FULL',
            'Circuito': 'DEDICADO', 'Operando': 'Operando',
            'Operador': None, 'Observaciones': None,
        },
        {
            'Unidades': 'C200', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Gerencia': 'MEX', 'Operación': 'DIST', 'Tipo de Unidad': 'TORTHON',
            'Circuito': 'NORTE', 'Operando': 'Operando',
            'Operador': 'Pedro Existente', 'Observaciones': None,
        },
    ])
    df_local = pd.DataFrame([
        {
            'Unidades': 'C070', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Operador': 'Juan Local', 'Observaciones': 'Nota local',
        },
        {
            'Unidades': 'C200', 'Fecha Cedula_dt': pd.Timestamp('2026-06-01'),
            'Operador': 'Pedro Local', 'Observaciones': 'Otra nota',
        },
    ])

    merged, crossfill_log = crossfill_cedulas(df_primary, df_local, _NOLOG)

    fila_c070 = merged[merged['Unidades'] == 'C070'].iloc[0]
    assert fila_c070['Operador'] == 'Juan Local'
    assert fila_c070['Observaciones'] == 'Nota local'

    fila_c200 = merged[merged['Unidades'] == 'C200'].iloc[0]
    # Operador ya tenia valor en primario -> no se sobrescribe.
    assert fila_c200['Operador'] == 'Pedro Existente'
    # Observaciones venia vacio -> se completa desde local.
    assert fila_c200['Observaciones'] == 'Otra nota'

    assert ('C070', pd.Timestamp('2026-06-01'), 'Operador') in crossfill_log
    assert ('C070', pd.Timestamp('2026-06-01'), 'Observaciones') in crossfill_log
    assert ('C200', pd.Timestamp('2026-06-01'), 'Observaciones') in crossfill_log
    assert not any(u == 'C200' and c == 'Operador' for u, _, c in crossfill_log)


def test_crossfill_no_revienta_si_local_trae_columna_numerica(tmp_path) -> None:
    """Reproduce bug real: pandas 3 usa dtype 'str' para columnas de texto del
    primario (cédula de Sheets); si la cédula local "Completa" trae una columna
    de `units_extra` 100% numérica (ej. 'No Operador' con solo claves numéricas),
    `pd.read_excel` la devuelve como int64. Asignar esos valores int64 a una
    columna 'str' con `.loc` revienta con
    `TypeError: Invalid value for dtype 'str'` si no se normaliza el dtype antes.
    """
    df_primary = pd.DataFrame({
        'Unidades': pd.array(['C070'], dtype='str'),
        'Fecha Cedula_dt': [pd.Timestamp('2026-06-01')],
        'No Operador': pd.array([None], dtype='str'),
    })
    df_dia = pd.DataFrame([{
        'Unidades': 'C070', 'Gerencia': 'CUE', 'Operación': 'VEND',
        'Tipo de Unidad': 'FULL', 'Circuito': 'DEDICADO', 'Operando': 'Operando',
        'No Operador': 12345,
    }])
    df_dia.to_excel(tmp_path / "Cedula 01062026 Completa.xlsx", engine='openpyxl', index=False)

    df_local = load_local_cedulas_for_crossfill(str(tmp_path), _NOLOG)

    merged, crossfill_log = crossfill_cedulas(df_primary, df_local, _NOLOG)

    assert merged.loc[0, 'No Operador'] == 12345
    assert ('C070', pd.Timestamp('2026-06-01'), 'No Operador') in crossfill_log

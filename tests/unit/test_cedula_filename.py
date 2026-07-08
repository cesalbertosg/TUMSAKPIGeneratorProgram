"""Tests para `io.excel.parse_cedula_filename`.

Valida el parseo del nombre canonico `Cedula DDMMYYYY.xlsx` y variantes
toleradas (mayusculas, con tilde, espacios extra, separadores).

Casos cubiertos:
1. Formato canonico `Cedula 16052026.xlsx`
2. Variante con tilde `Cédula 01012026.xlsx`
3. Variante todo minusculas `cedula 03062026.xlsx`
4. Separadores con espacios `Cedula 3 6 2026.xlsx`
5. Fecha invalida (mes 13) -> None
6. Archivo sin patron de fecha -> None
7. Extension `.xls` (legacy) tambien se reconoce
"""

from __future__ import annotations

from datetime import datetime

from kpi_generator.io.excel import parse_cedula_filename, parse_cedula_filename_ex


def test_formato_canonico() -> None:
    """`Cedula 16052026.xlsx` -> 16/05/2026."""
    assert parse_cedula_filename("Cedula 16052026.xlsx") == datetime(2026, 5, 16)


def test_variante_con_tilde() -> None:
    """`Cédula 01012026.xlsx` (con tilde) debe parsearse igual."""
    assert parse_cedula_filename("Cédula 01012026.xlsx") == datetime(2026, 1, 1)


def test_minusculas_y_mayusculas() -> None:
    """El parser es case-insensitive."""
    assert parse_cedula_filename("cedula 03062026.xlsx") == datetime(2026, 6, 3)
    assert parse_cedula_filename("CEDULA 03062026.XLSX") == datetime(2026, 6, 3)


def test_separadores_con_espacios() -> None:
    """`Cedula 3 6 2026.xlsx` (dia y mes de 1 digito separados) tambien se acepta."""
    assert parse_cedula_filename("Cedula 3 6 2026.xlsx") == datetime(2026, 6, 3)


def test_fecha_invalida_devuelve_none() -> None:
    """Mes 13 -> ValueError interno -> None (no debe propagar)."""
    assert parse_cedula_filename("Cedula 01132026.xlsx") is None


def test_filename_sin_patron() -> None:
    """Archivo sin nombre de cedula -> None."""
    assert parse_cedula_filename("Reporte.xlsx") is None
    assert parse_cedula_filename("backup_final.xlsx") is None


def test_extension_xls_legacy() -> None:
    """Variantes `.xls` (sin x al final) tambien se reconocen."""
    assert parse_cedula_filename("Cedula 16052026.xls") == datetime(2026, 5, 16)


def test_completa_prefijo_antes_de_fecha() -> None:
    """`Cedula completa 01072026.xlsx` (palabra ANTES de la fecha) — formato julio.

    Regresion: v0.6.2 ignoraba este nombre (solo aceptaba 'completa' como
    sufijo), descartando la cedula autoritativa y re-bajando de Drive.
    """
    assert parse_cedula_filename("Cedula completa 01072026.xlsx") == datetime(2026, 7, 1)
    assert parse_cedula_filename("Cedula completa 01 07 2026.xlsx") == datetime(2026, 7, 1)
    assert parse_cedula_filename("Cedula completa para auto 05072026.xlsx") == datetime(2026, 7, 5)


def test_completa_sufijo_despues_de_fecha() -> None:
    """`Cedula 01062026 Completa.xlsx` (palabra DESPUES de la fecha) — formato junio."""
    assert parse_cedula_filename("Cedula 01062026 Completa.xlsx") == datetime(2026, 6, 1)


# ---------- parse_cedula_filename_ex (v0.6.4): clasificacion diario/variante ----------

def test_ex_nombre_canonico_es_diario() -> None:
    """Nombre canonico sin palabras extra -> variante='diario'."""
    parsed = parse_cedula_filename_ex("Cedula 16052026.xlsx")
    assert parsed is not None
    assert parsed.fecha == datetime(2026, 5, 16)
    assert parsed.variante == 'diario'


def test_ex_separadores_y_tilde_siguen_siendo_diario() -> None:
    """Espacios en la fecha o tilde no convierten el nombre en variante."""
    assert parse_cedula_filename_ex("Cedula 3 6 2026.xlsx").variante == 'diario'
    assert parse_cedula_filename_ex("Cédula 01012026.xlsx").variante == 'diario'
    assert parse_cedula_filename_ex("CEDULA 03062026.XLSX").variante == 'diario'


def test_ex_palabra_extra_es_variante() -> None:
    """Cualquier palabra extra (antes o despues de la fecha) -> 'variante'."""
    assert parse_cedula_filename_ex("Cedula 01062026 Completa.xlsx").variante == 'variante'
    assert parse_cedula_filename_ex("Cedula completa 01072026.xlsx").variante == 'variante'
    assert parse_cedula_filename_ex("Cedula completa para auto 05072026.xlsx").variante == 'variante'


def test_ex_fechas_coinciden_con_wrapper() -> None:
    """El wrapper historico devuelve exactamente la fecha del _ex."""
    for nombre in ["Cedula 16052026.xlsx", "Cedula 01062026 Completa.xlsx"]:
        assert parse_cedula_filename(nombre) == parse_cedula_filename_ex(nombre).fecha


def test_ex_invalido_devuelve_none() -> None:
    assert parse_cedula_filename_ex("Reporte.xlsx") is None
    assert parse_cedula_filename_ex("Cedula 01132026.xlsx") is None

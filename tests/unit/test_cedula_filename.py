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

from kpi_generator.io.excel import parse_cedula_filename


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

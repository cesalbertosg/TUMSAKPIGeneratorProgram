"""Tests para el retry con backoff de `io.sheets.sync_workbook_to_sheets` (v0.6.6).

Incidente real (09/07/2026): un 503 momentáneo de Google obligaba a rehacer el
pipeline completo (~3-5 min) solo para reintentar la subida a Sheets. Estos tests
cubren: retry ante errores transitorios, no-retry ante errores de permisos, y que
se agote la cuenta de intentos sin colgar el proceso.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import gspread
import pandas as pd
import pytest

from kpi_generator.io.sheets import sync_workbook_to_sheets

_NOLOG = lambda *_a, **_k: None  # noqa: E731


def _api_error(code: int) -> gspread.exceptions.APIError:
    response = MagicMock()
    response.json.return_value = {
        "error": {"code": code, "message": f"error {code}", "status": "X"}
    }
    return gspread.exceptions.APIError(response)


def _dfs() -> dict:
    return {"Tab1": pd.DataFrame([{"a": 1, "b": 2}])}


def _mock_gc_ok() -> MagicMock:
    """gspread.Client mockeado que abre el sheet y sube sin error."""
    ws = MagicMock()
    sh = MagicMock()
    sh.worksheet.side_effect = gspread.WorksheetNotFound()
    sh.add_worksheet.return_value = ws
    gc = MagicMock()
    gc.open_by_key.return_value = sh
    return gc


@patch("kpi_generator.io.sheets.Credentials.from_service_account_file", return_value=MagicMock())
@patch("kpi_generator.io.sheets.gspread.authorize")
def test_retry_ante_503_hasta_exito(mock_authorize, _mock_creds) -> None:
    """Dos 503 seguidos y éxito al tercer intento -> True, 2 sleeps con backoff."""
    mock_authorize.side_effect = [_api_error(503), _api_error(503), _mock_gc_ok()]
    sleeps: list[float] = []

    result = sync_workbook_to_sheets("sheet-id", _dfs(), _NOLOG, sleep_fn=sleeps.append)

    assert result is True
    assert mock_authorize.call_count == 3
    assert sleeps == [2, 8]


@patch("kpi_generator.io.sheets.Credentials.from_service_account_file", return_value=MagicMock())
@patch("kpi_generator.io.sheets.gspread.authorize")
def test_error_permisos_no_reintenta(mock_authorize, _mock_creds) -> None:
    """403 (permisos) falla en el primer intento, sin retry ni sleep."""
    mock_authorize.side_effect = [_api_error(403)]
    sleeps: list[float] = []

    result = sync_workbook_to_sheets("sheet-id", _dfs(), _NOLOG, sleep_fn=sleeps.append)

    assert result is False
    assert mock_authorize.call_count == 1
    assert sleeps == []


@patch("kpi_generator.io.sheets.Credentials.from_service_account_file", return_value=MagicMock())
@patch("kpi_generator.io.sheets.gspread.authorize")
def test_agota_intentos_devuelve_false(mock_authorize, _mock_creds) -> None:
    """503 en los 3 intentos -> False, sin colgarse ni lanzar excepción."""
    mock_authorize.side_effect = [_api_error(503), _api_error(503), _api_error(503)]
    sleeps: list[float] = []

    result = sync_workbook_to_sheets("sheet-id", _dfs(), _NOLOG, sleep_fn=sleeps.append)

    assert result is False
    assert mock_authorize.call_count == 3
    assert sleeps == [2, 8]


@patch("kpi_generator.io.sheets.Credentials.from_service_account_file", return_value=MagicMock())
@patch("kpi_generator.io.sheets.gspread.authorize")
def test_exito_primer_intento_sin_retry(mock_authorize, _mock_creds) -> None:
    """Regresión: sin errores, se comporta igual que antes del retry (1 intento)."""
    mock_authorize.return_value = _mock_gc_ok()
    sleeps: list[float] = []

    result = sync_workbook_to_sheets("sheet-id", _dfs(), _NOLOG, sleep_fn=sleeps.append)

    assert result is True
    assert mock_authorize.call_count == 1
    assert sleeps == []


@patch("kpi_generator.io.sheets.Credentials.from_service_account_file", return_value=MagicMock())
@patch("kpi_generator.io.sheets.gspread.authorize")
def test_connection_error_es_transitorio(mock_authorize, _mock_creds) -> None:
    """Errores de red (ConnectionError/Timeout) también reintentan."""
    import requests

    mock_authorize.side_effect = [requests.exceptions.ConnectionError("offline"), _mock_gc_ok()]
    sleeps: list[float] = []

    result = sync_workbook_to_sheets("sheet-id", _dfs(), _NOLOG, sleep_fn=sleeps.append)

    assert result is True
    assert mock_authorize.call_count == 2
    assert sleeps == [2]

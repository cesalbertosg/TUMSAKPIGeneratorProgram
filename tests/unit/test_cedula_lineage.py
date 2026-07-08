"""Tests para `kpi_generator.lineage` (v0.6.4).

Cubre el render de la hoja "Fuente Cedulas" (`to_dataframe`) y el resumen
de una linea para log/GUI (`resumen_linea`).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from kpi_generator.lineage import LINEAGE_SHEET_COLUMNS, ArchivoCedula, CedulaLineage


def _lineage_excel_tipico() -> CedulaLineage:
    lin = CedulaLineage(fuente_solicitada='excel')
    lin.fuente_efectiva = 'excel'
    lin.carpeta = r'C:\KPIs\06 Junio\Cedulas'
    lin.archivos = [
        ArchivoCedula('Cedula 01062026.xlsx', datetime(2026, 6, 1), 'diario',
                      datetime(2026, 6, 2, 9, 0), 577, rol='unico'),
        ArchivoCedula('Cedula 03062026.xlsx', datetime(2026, 6, 3), 'diario',
                      datetime(2026, 6, 4, 9, 0), 577, rol='unico'),
    ]
    lin.fechas_fisicas = [datetime(2026, 6, 1), datetime(2026, 6, 3)]
    lin.fechas_ffill = [pd.Timestamp('2026-06-02')]
    return lin


def test_to_dataframe_estructura_y_categorias() -> None:
    df = _lineage_excel_tipico().to_dataframe()

    assert list(df.columns) == LINEAGE_SHEET_COLUMNS
    categorias = set(df['Categoría'])
    assert {'CORRIDA', 'ARCHIVO', 'FECHA'} <= categorias
    # 2 archivos + 3 fechas (2 fisicas + 1 ffill)
    assert (df['Categoría'] == 'ARCHIVO').sum() == 2
    assert (df['Categoría'] == 'FECHA').sum() == 3
    # La fila CORRIDA declara fuente solicitada y efectiva
    detalles = ' | '.join(df.loc[df['Categoría'] == 'CORRIDA', 'Detalle'])
    assert 'Fuente solicitada: excel' in detalles
    assert 'Fuente efectiva: excel' in detalles
    # El dia ffill queda declarado como tal
    fila_ffill = df[(df['Categoría'] == 'FECHA') & (df['Fecha'] == '02/06/2026')]
    assert len(fila_ffill) == 1
    assert 'ffill' in fila_ffill.iloc[0]['Detalle']


def test_to_dataframe_fallbacks_y_advertencias() -> None:
    lin = _lineage_excel_tipico()
    lin.fuente_efectiva = 'sheets'
    lin.fallbacks = ['db→sheets: BD inaccesible (timeout)']
    lin.carpeta_mixta = True
    lin.advertencias = ['Cedula X Completa.xlsx: 2 unidades solo en la variante no se agregaron (C1, C2)']

    df = lin.to_dataframe()

    assert (df['Categoría'] == 'FALLBACK').sum() == 1
    # carpeta_mixta genera su propia fila ADVERTENCIA ademas de las explicitas
    assert (df['Categoría'] == 'ADVERTENCIA').sum() == 2


def test_resumen_linea_contenido() -> None:
    resumen = _lineage_excel_tipico().resumen_linea()

    assert 'Fuente efectiva: EXCEL' in resumen
    assert '2 archivos (2 diarios, 0 variantes)' in resumen
    assert 'físico 01/06/2026→03/06/2026 (2 días)' in resumen
    assert '1 días ffill' in resumen
    assert 'FALLBACK' not in resumen


def test_resumen_linea_con_fallback_y_mixta() -> None:
    lin = _lineage_excel_tipico()
    lin.fallbacks = ['db→sheets: BD inaccesible']
    lin.carpeta_mixta = True

    resumen = lin.resumen_linea()

    assert 'CARPETA MIXTA' in resumen
    assert 'FALLBACK: db→sheets: BD inaccesible' in resumen


def test_hoja_fuente_cedulas_se_escribe(tmp_path) -> None:
    """Smoke: el render del linaje se escribe como hoja 'Fuente Cedulas' con
    el nombre canonico registrado en SHEET_NAMES."""
    from kpi_generator.domain.processor import SHEET_NAMES
    from kpi_generator.io.excel import write_workbook

    df = _lineage_excel_tipico().to_dataframe()
    path = write_workbook({SHEET_NAMES['fuente']: df}, str(tmp_path), lambda *a, **k: None)

    assert path is not None
    assert 'Fuente Cedulas' in pd.ExcelFile(path).sheet_names


def test_archivos_descartados_no_cuentan_en_resumen() -> None:
    lin = _lineage_excel_tipico()
    lin.archivos.append(
        ArchivoCedula('Cedula 1 6 2026.xlsx', datetime(2026, 6, 1), 'diario',
                      datetime(2026, 6, 1, 8, 0), 577, rol='descartado',
                      detalle='diario duplicado de la misma fecha (mtime anterior)')
    )

    assert '2 archivos (2 diarios, 0 variantes)' in lin.resumen_linea()
    # pero SI aparece en la hoja, con su rol
    df = lin.to_dataframe()
    descartados = df[(df['Categoría'] == 'ARCHIVO') & (df['Rol'] == 'descartado')]
    assert len(descartados) == 1

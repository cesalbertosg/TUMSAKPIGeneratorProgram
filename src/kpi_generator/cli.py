"""Entry point de línea de comandos (sin GUI).

Uso:
    python -m kpi_generator.cli run --trips <path> --fuel <path> --cedulas <path> [...]
    python -m kpi_generator.cli diff-cedulas --from YYYY-MM-DD --to YYYY-MM-DD --excel-folder <path>
    kpi-run run ...           # si se instaló con `pip install -e .`
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from kpi_generator.config import Config, LogLevel


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kpi-run",
        description="KPI Generator — pipeline de KPIs de flota TUMSA (CLI sin GUI).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Ejecuta el pipeline completo y genera reporte Excel + sync Sheets.")
    run.add_argument("--trips", required=True, type=Path, help="Archivo Excel de viajes (ZVPF).")
    run.add_argument("--fuel", required=True, type=Path, help="Archivo Excel de cargas de combustible.")
    run.add_argument(
        "--cedulas",
        type=Path,
        default=None,
        help="Carpeta con cédulas Excel (requerido si --cedulas-source=excel o como fallback).",
    )
    run.add_argument("--objectives", type=Path, default=None, help="Archivo Excel de objetivos mensuales (opcional).")
    run.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Carpeta destino del Excel (el filename `KPIs_Transport_<timestamp>.xlsx` se agrega automáticamente). "
             f"Default: {Config.OUTPUTS_DIR}",
    )
    run.add_argument(
        "--cedulas-source",
        choices=["db", "excel", "sheets"],
        default=None,
        help=f"Fuente de cédulas. Default desde .env (actual: {Config.CEDULAS_SOURCE}).",
    )
    run.add_argument(
        "--log-level",
        choices=["ERROR", "INFO", "DEBUG"],
        default="INFO",
        help="Nivel de logging (default: INFO).",
    )

    diff = sub.add_parser("diff-cedulas",
                          help="Compara cédulas cargadas desde BD vs Excel para un rango — útil en validación de migración.")
    diff.add_argument("--from", dest="fecha_min", required=True, type=_parse_date,
                      help="Fecha inicio del rango (YYYY-MM-DD).")
    diff.add_argument("--to", dest="fecha_max", required=True, type=_parse_date,
                      help="Fecha fin del rango (YYYY-MM-DD).")
    diff.add_argument("--excel-folder", required=True, type=Path,
                      help="Carpeta con cédulas Excel para comparar contra la BD.")

    return parser


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _default_output_dir() -> Path:
    Config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    return Config.OUTPUTS_DIR


def _cmd_run(args) -> int:
    from kpi_generator.domain.processor import DataProcessor

    log_level = LogLevel[args.log_level]
    processor = DataProcessor(log_callback=print, log_level=log_level)

    output_dir = args.output or _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    cedulas_arg = str(args.cedulas) if args.cedulas else ""

    result = processor.generate_report(
        str(args.trips),
        str(args.fuel),
        cedulas_arg,
        str(output_dir),
        str(args.objectives) if args.objectives else None,
        cedulas_source=args.cedulas_source,
    )

    if result:
        print(f"[OK] Reporte generado: {result}")
        return 0
    print("[ERR] El procesamiento falló — revisa el log.", file=sys.stderr)
    return 1


def _cmd_diff_cedulas(args) -> int:
    """Carga cédulas desde BD y desde Excel, compara y reporta diferencias."""
    from kpi_generator.domain.processor import DataProcessor
    from kpi_generator.io.cedulas_db import load_cedulas_from_db

    print(f"[DIFF] Rango: {args.fecha_min} → {args.fecha_max}")
    print(f"[DIFF] Excel folder: {args.excel_folder}")

    # Carga desde BD
    try:
        df_db, df_audit = load_cedulas_from_db(args.fecha_min, args.fecha_max, log_func=print)
    except Exception as e:
        print(f"[ERR] Carga BD falló: {e}", file=sys.stderr)
        return 1

    # Carga desde Excel (reusa load_daily_cedulas vía un DataProcessor descartable)
    processor = DataProcessor(log_callback=print, log_level=LogLevel.INFO)
    df_excel = processor.load_daily_cedulas(str(args.excel_folder))
    if df_excel is None:
        print("[ERR] Carga Excel falló.", file=sys.stderr)
        return 1

    # Filtrar Excel al mismo rango
    import pandas as pd
    fmin = pd.Timestamp(args.fecha_min)
    fmax = pd.Timestamp(args.fecha_max)
    df_excel = df_excel[(df_excel['Fecha Cedula_dt'] >= fmin) &
                        (df_excel['Fecha Cedula_dt'] <= fmax)].reset_index(drop=True)

    print(f"\n[DIFF] BD:    {len(df_db):>6} filas, {df_db['Unidades'].nunique():>4} unidades")
    print(f"[DIFF] Excel: {len(df_excel):>6} filas, {df_excel['Unidades'].nunique():>4} unidades")

    # Comparar conjunto de (Unidades, Fecha Cedula)
    db_keys = set(zip(df_db['Unidades'].astype(str), df_db['Fecha Cedula']))
    excel_keys = set(zip(df_excel['Unidades'].astype(str), df_excel['Fecha Cedula']))

    solo_db = db_keys - excel_keys
    solo_excel = excel_keys - db_keys
    comunes = db_keys & excel_keys

    print(f"\n[DIFF] Pares (Unidad, Fecha):")
    print(f"  Comunes:    {len(comunes)}")
    print(f"  Solo en BD: {len(solo_db)}")
    print(f"  Solo Excel: {len(solo_excel)}")

    if solo_db:
        print(f"\n[DIFF] Muestra solo en BD (primeros 10):")
        for k in list(solo_db)[:10]:
            print(f"    {k}")
    if solo_excel:
        print(f"\n[DIFF] Muestra solo en Excel (primeros 10):")
        for k in list(solo_excel)[:10]:
            print(f"    {k}")

    # Para los comunes, comparar columnas de negocio
    df_db_m = df_db.set_index(['Unidades', 'Fecha Cedula'])
    df_excel_m = df_excel.set_index(['Unidades', 'Fecha Cedula'])
    cols_negocio = ['Gerencia', 'Operación', 'Tipo de Unidad', 'Circuito', 'Operando']
    df_db_m.index = df_db_m.index.map(lambda x: (str(x[0]), x[1]))
    df_excel_m.index = df_excel_m.index.map(lambda x: (str(x[0]), x[1]))

    diffs_por_col = {}
    for col in cols_negocio:
        if col not in df_db_m.columns or col not in df_excel_m.columns:
            continue
        comunes_idx = df_db_m.index.intersection(df_excel_m.index)
        a = df_db_m.loc[comunes_idx, col].astype(str).str.upper().str.strip()
        b = df_excel_m.loc[comunes_idx, col].astype(str).str.upper().str.strip()
        n_diff = (a != b).sum()
        diffs_por_col[col] = n_diff

    print(f"\n[DIFF] Discrepancias en columnas (sobre {len(comunes)} pares comunes):")
    for col, n in diffs_por_col.items():
        marker = "OK" if n == 0 else "!!"
        print(f"  [{marker}] {col:20s}: {n} diferencias")

    if df_audit is not None and not df_audit.empty:
        rellenadas = (df_audit['Origen'] == 'forward_fill').sum()
        print(f"\n[DIFF] BD requirió forward-fill en {rellenadas} de {len(df_audit)} pares.")

    return 0 if not solo_db and not solo_excel and all(n == 0 for n in diffs_por_col.values()) else 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "diff-cedulas":
        return _cmd_diff_cedulas(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

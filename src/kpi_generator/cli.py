"""Entry point de línea de comandos (sin GUI).

Uso:
    python -m kpi_generator.cli run --trips <path> --fuel <path> --cedulas <path> [--objectives <path>] [--output <path>]
    kpi-run run ...           # si se instaló con `pip install -e .`
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
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
        required=True,
        type=Path,
        help="Carpeta con cédulas diarias (Cedula DDMMYYYY.xlsx) o un solo archivo.",
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
        "--log-level",
        choices=["ERROR", "INFO", "DEBUG"],
        default="INFO",
        help="Nivel de logging (default: INFO).",
    )

    return parser


def _default_output_dir() -> Path:
    Config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    return Config.OUTPUTS_DIR


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        from kpi_generator.domain.processor import DataProcessor

        log_level = LogLevel[args.log_level]
        processor = DataProcessor(log_callback=print, log_level=log_level)

        output_dir = args.output or _default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        result = processor.generate_report(
            str(args.trips),
            str(args.fuel),
            str(args.cedulas),
            str(output_dir),
            str(args.objectives) if args.objectives else None,
        )

        if result:
            print(f"[OK] Reporte generado: {result}")
            return 0
        print("[ERR] El procesamiento falló — revisa el log.", file=sys.stderr)
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

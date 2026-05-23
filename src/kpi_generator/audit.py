"""Auditoría de salud del KPI Generator.

Dos modos:

  - **quick**: smoke tests sin BD ni archivos de muestra (~3s). Verifica imports,
    configuración, conectividad PostgreSQL y credenciales Google Sheets.

  - **full**: ejecuta el pipeline E2E completo contra datos reales y valida la
    estructura del output (hojas, columnas, conteos, consistencia). ~3-5 min.

Cada check devuelve un `CheckResult` con status PASS/WARN/FAIL y mensaje.
El reporte global colapsa a exit code:
  0 = todo PASS
  1 = al menos un WARN, sin FAIL
  2 = al menos un FAIL
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable

from kpi_generator import __version__
from kpi_generator.config import Config


class Status(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    detail: str = ""


@dataclass
class AuditReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: Status, message: str, detail: str = "") -> None:
        self.results.append(CheckResult(name, status, message, detail))

    def has_failures(self) -> bool:
        return any(r.status == Status.FAIL for r in self.results)

    def has_warnings(self) -> bool:
        return any(r.status == Status.WARN for r in self.results)

    def exit_code(self) -> int:
        if self.has_failures():
            return 2
        if self.has_warnings():
            return 1
        return 0

    def summary(self) -> dict[str, int]:
        counts = {s: 0 for s in Status}
        for r in self.results:
            counts[r.status] += 1
        return {s.value: c for s, c in counts.items()}


# ============================================================================
# Quick checks (sin BD, sin red — ~3s)
# ============================================================================

def check_package_version(report: AuditReport) -> None:
    """Versión del paquete instalada == __version__."""
    try:
        from importlib.metadata import version as pkg_version
        installed = pkg_version("kpi-generator")
        if installed == __version__:
            report.add("package.version", Status.PASS,
                       f"kpi-generator instalado en v{installed}")
        else:
            report.add("package.version", Status.WARN,
                       f"Versión instalada ({installed}) difiere de __version__ ({__version__})",
                       "Posiblemente falta `pip install -e .` tras un bump")
    except Exception as e:
        report.add("package.version", Status.FAIL,
                   "No se pudo leer la versión instalada", str(e))


def check_module_imports(report: AuditReport) -> None:
    """Todos los módulos importan sin error."""
    modules = [
        "kpi_generator.config",
        "kpi_generator.domain.processor",
        "kpi_generator.domain.comodato",
        "kpi_generator.domain.change_tracker",
        "kpi_generator.gui.app",
        "kpi_generator.gui.widgets",
        "kpi_generator.io.postgres",
        "kpi_generator.io.cedulas_db",
        "kpi_generator.io.date_range",
        "kpi_generator.cli",
    ]
    failed = []
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception as e:
            failed.append(f"{m}: {e}")
    if not failed:
        report.add("package.imports", Status.PASS,
                   f"{len(modules)} módulos importan correctamente")
    else:
        report.add("package.imports", Status.FAIL,
                   f"{len(failed)}/{len(modules)} módulos fallan",
                   "\n".join(failed))


def check_config_loads(report: AuditReport) -> None:
    """Config carga las variables esperadas desde .env."""
    required = ["CEDULAS_SOURCE", "PG_CEDULA_HOST", "PG_CEDULA_DB", "OUTPUTS_DIR", "DATA_INPUT_DIR"]
    missing = [k for k in required if not hasattr(Config, k)]
    if missing:
        report.add("config.attrs", Status.FAIL,
                   f"Atributos faltantes en Config: {missing}")
        return
    report.add("config.attrs", Status.PASS,
               f"Config expone {len(required)} variables esperadas")

    if Config.CEDULAS_SOURCE not in ("db", "excel", "sheets"):
        report.add("config.source", Status.FAIL,
                   f"CEDULAS_SOURCE inválido: '{Config.CEDULAS_SOURCE}' (esperado db|excel|sheets)")
    else:
        report.add("config.source", Status.PASS,
                   f"CEDULAS_SOURCE = '{Config.CEDULAS_SOURCE}'")


def check_credentials_present(report: AuditReport) -> None:
    """Credenciales esperadas presentes (sin validar contra remote)."""
    if Config.PG_CEDULA_USER and Config.PG_CEDULA_PASSWORD:
        report.add("creds.postgres", Status.PASS,
                   f"PG_CEDULA_USER configurado ({len(Config.PG_CEDULA_USER)} chars)")
    else:
        report.add("creds.postgres", Status.WARN,
                   "Credenciales Postgres no configuradas en .env")

    cred_path = Path(Config.CREDENTIALS_PATH)
    if cred_path.exists():
        size = cred_path.stat().st_size
        report.add("creds.gsheets", Status.PASS,
                   f"google_service_account.json existe ({size} bytes)")
    else:
        report.add("creds.gsheets", Status.WARN,
                   f"google_service_account.json no existe en {cred_path}")


def check_postgres_reachable(report: AuditReport) -> None:
    """BD Postgres responde a un ping."""
    if not (Config.PG_CEDULA_USER and Config.PG_CEDULA_PASSWORD):
        report.add("connectivity.postgres", Status.SKIP,
                   "Sin credenciales — no se puede probar ping")
        return
    try:
        from kpi_generator.io.postgres import ping
        if ping():
            report.add("connectivity.postgres", Status.PASS,
                       f"Postgres {Config.PG_CEDULA_HOST}/{Config.PG_CEDULA_DB} responde")
        else:
            report.add("connectivity.postgres", Status.FAIL,
                       f"Postgres {Config.PG_CEDULA_HOST}/{Config.PG_CEDULA_DB} no responde (¿VPN apagada?)")
    except Exception as e:
        report.add("connectivity.postgres", Status.FAIL,
                   "Error al ejecutar ping", str(e))


def check_gsheets_reachable(report: AuditReport) -> None:
    """Credenciales Google Sheets son válidas (abre el spreadsheet)."""
    cred_path = Path(Config.CREDENTIALS_PATH)
    if not cred_path.exists():
        report.add("connectivity.gsheets", Status.SKIP,
                   "Sin credenciales — no se puede probar")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(Config.CREDENTIALS_PATH, scopes=Config.SHEETS_SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(Config.SHEETS_ID)
        report.add("connectivity.gsheets", Status.PASS,
                   f"Spreadsheet '{sh.title}' accesible ({len(sh.worksheets())} tabs)")
    except Exception as e:
        report.add("connectivity.gsheets", Status.FAIL,
                   "No se pudo abrir el spreadsheet", str(e)[:200])


# ============================================================================
# Full audit (E2E con datos reales — ~3-5 min)
# ============================================================================

EXPECTED_SHEETS = ["Resumen", "Por Equipo", "Viajes", "Resumen de Cambios",
                   "Por Operación", "Objetivos", "Promedio KM por Unidad"]

DEADWEIGHT_COLS_VIAJES = ["llaveremolque", "EqAsignados"]
DEADWEIGHT_COLS_EQUIPO = ["Días Gestoría"]
DEADWEIGHT_COLS_OPCEDULA = ["Dias Operando", "Dias Taller", "Dias Gestoria", "Dias Sin Op"]

CANONICAL_COLS_PROMEDIO = ["Operación Cedula", "Gerencia", "Motrices", "Remolques Únicos", "Promedio Diario KM/U"]


def check_pipeline_runs(report: AuditReport, trips: Path, fuel: Path,
                        objectives: Path, output_dir: Path,
                        log_func: Callable[[str], None] | None = None) -> Path | None:
    """Corre el pipeline E2E y reporta éxito/error. Devuelve path del Excel generado."""
    if not trips.exists() or not fuel.exists():
        report.add("pipeline.run", Status.FAIL,
                   f"Archivos de viaje/combustible no encontrados: {trips.name}, {fuel.name}")
        return None
    try:
        from kpi_generator.config import LogLevel
        from kpi_generator.domain.processor import DataProcessor
        output_dir.mkdir(parents=True, exist_ok=True)
        log_cb = log_func or (lambda *_a, **_k: None)
        processor = DataProcessor(log_callback=log_cb, log_level=LogLevel.ERROR)
        result = processor.generate_report(
            str(trips), str(fuel), "", str(output_dir),
            str(objectives) if objectives.exists() else None,
        )
        if result and Path(result).exists():
            size_kb = Path(result).stat().st_size / 1024
            report.add("pipeline.run", Status.PASS,
                       f"Pipeline E2E generó reporte ({size_kb:.0f} KB)",
                       Path(result).name)
            return Path(result)
        report.add("pipeline.run", Status.FAIL,
                   "Pipeline retornó None — revisar log")
        return None
    except Exception as e:
        report.add("pipeline.run", Status.FAIL,
                   f"Excepción durante pipeline: {type(e).__name__}",
                   str(e)[:300])
        return None


def check_output_structure(report: AuditReport, excel_path: Path) -> None:
    """Valida que el Excel tenga las 7 hojas canónicas (+ Cedulas Rellenadas si db)."""
    import pandas as pd
    try:
        xls = pd.ExcelFile(excel_path)
    except Exception as e:
        report.add("output.open", Status.FAIL, f"No se pudo abrir Excel: {e}")
        return

    missing = [s for s in EXPECTED_SHEETS if s not in xls.sheet_names]
    if missing:
        report.add("output.sheets", Status.FAIL,
                   f"Hojas faltantes: {missing}",
                   f"Encontradas: {xls.sheet_names}")
    else:
        report.add("output.sheets", Status.PASS,
                   f"7 hojas canónicas presentes ({len(xls.sheet_names)} total)")

    # Validar deadweight ausente
    deadweight_findings = []
    for sheet, cols in [("Viajes", DEADWEIGHT_COLS_VIAJES),
                         ("Por Equipo", DEADWEIGHT_COLS_EQUIPO),
                         ("Por Operación", DEADWEIGHT_COLS_OPCEDULA)]:
        if sheet not in xls.sheet_names:
            continue
        df_head = pd.read_excel(excel_path, sheet_name=sheet, nrows=1)
        present = [c for c in cols if c in df_head.columns]
        if present:
            deadweight_findings.append(f"{sheet}: {present}")
    if deadweight_findings:
        report.add("output.deadweight", Status.FAIL,
                   "Columnas deadweight aún presentes",
                   "\n".join(deadweight_findings))
    else:
        report.add("output.deadweight", Status.PASS,
                   "Sin columnas deadweight en hojas")

    # Validar naming canónico en Promedio KM
    if "Promedio KM por Unidad" in xls.sheet_names:
        df = pd.read_excel(excel_path, sheet_name="Promedio KM por Unidad", nrows=1)
        if list(df.columns) == CANONICAL_COLS_PROMEDIO:
            report.add("output.naming", Status.PASS,
                       "Naming canónico aplicado en 'Promedio KM por Unidad'")
        else:
            report.add("output.naming", Status.WARN,
                       "Columnas no coinciden con naming canónico",
                       f"esperado: {CANONICAL_COLS_PROMEDIO}\nactual: {list(df.columns)}")


def check_resumen_consistency(report: AuditReport, excel_path: Path) -> None:
    """El Resumen debe tener N gerencias + TOTAL TUMSA. Sumas deben coincidir."""
    import pandas as pd
    try:
        df = pd.read_excel(excel_path, sheet_name="Resumen")
    except Exception as e:
        report.add("resumen.read", Status.FAIL, f"No se pudo leer Resumen: {e}")
        return

    if len(df) < 2:
        report.add("resumen.shape", Status.FAIL,
                   f"Resumen tiene {len(df)} filas (esperado ≥2: gerencias + TOTAL)")
        return

    last_row = df.iloc[-1]
    if "TOTAL" not in str(last_row["Gerencia"]).upper():
        report.add("resumen.total", Status.FAIL,
                   f"Última fila no es TOTAL: '{last_row['Gerencia']}'")
        return

    # Validar que sumas de gerencias = TOTAL para columnas críticas
    body = df.iloc[:-1]
    mismatches = []
    for col in ["Unidades Activas", "KM Total", "Viajes", "Diesel LTS"]:
        if col not in df.columns:
            continue
        sum_body = body[col].sum()
        total = last_row[col]
        if abs(sum_body - total) > 0.5:
            mismatches.append(f"{col}: sum={sum_body:.1f} vs TOTAL={total:.1f}")

    if mismatches:
        report.add("resumen.totals", Status.FAIL,
                   "Sumas de gerencias no coinciden con TOTAL",
                   "\n".join(mismatches))
    else:
        report.add("resumen.totals", Status.PASS,
                   f"{len(body)} gerencias + TOTAL; sumas consistentes")


def check_data_quality(report: AuditReport, excel_path: Path) -> None:
    """Métricas mínimas razonables — alerta si valores fuera de rango esperado."""
    import pandas as pd
    try:
        df_resumen = pd.read_excel(excel_path, sheet_name="Resumen")
        total = df_resumen.iloc[-1]

        # Sanity: TOTAL TUMSA debe tener >= 100 unidades activas y > 0 KM
        unidades = int(total.get("Unidades Activas", 0))
        km = float(total.get("KM Total", 0))
        viajes = int(total.get("Viajes", 0))

        anomalies = []
        if unidades < 100:
            anomalies.append(f"Unidades Activas={unidades} (esperado ≥100)")
        if km < 100000:
            anomalies.append(f"KM Total={km:.0f} (esperado ≥100k para reporte mensual)")
        if viajes < 1000:
            anomalies.append(f"Viajes={viajes} (esperado ≥1000)")

        if anomalies:
            report.add("data.sanity", Status.WARN,
                       "Valores TOTAL fuera de rango esperado",
                       "\n".join(anomalies))
        else:
            report.add("data.sanity", Status.PASS,
                       f"TOTAL TUMSA: {unidades} unidades · {km:,.0f} KM · {viajes:,} viajes")

        # Cedulas Rellenadas — alerta si >50% son forward_fill
        try:
            df_audit = pd.read_excel(excel_path, sheet_name="Cedulas Rellenadas")
            if not df_audit.empty:
                ffill_pct = (df_audit["Origen"] == "forward_fill").mean() * 100
                if ffill_pct > 50:
                    report.add("data.forward_fill", Status.WARN,
                               f"{ffill_pct:.1f}% de cédulas son forward-fill (esperado <50%)",
                               "BD tiene baja cobertura del rango — revisar despachadores")
                else:
                    report.add("data.forward_fill", Status.PASS,
                               f"{ffill_pct:.1f}% forward-fill ({len(df_audit)} pares totales)")
        except ValueError:
            pass  # Hoja no existe (source != db)

    except Exception as e:
        report.add("data.sanity", Status.FAIL, f"Error analizando datos: {e}")


# ============================================================================
# Runners
# ============================================================================

def run_quick(report: AuditReport | None = None) -> AuditReport:
    """Ejecuta los checks que NO requieren E2E del pipeline. ~3s."""
    report = report or AuditReport()
    check_package_version(report)
    check_module_imports(report)
    check_config_loads(report)
    check_credentials_present(report)
    check_postgres_reachable(report)
    check_gsheets_reachable(report)
    return report


def run_full(trips: Path, fuel: Path, objectives: Path, output_dir: Path,
             report: AuditReport | None = None,
             log_func: Callable[[str], None] | None = None) -> AuditReport:
    """Ejecuta quick + pipeline E2E + validación de output. ~3-5 min."""
    report = report or AuditReport()
    run_quick(report)
    if report.has_failures():
        report.add("pipeline.run", Status.SKIP,
                   "Saltando E2E porque checks quick fallaron")
        return report
    excel = check_pipeline_runs(report, trips, fuel, objectives, output_dir, log_func)
    if excel is not None:
        check_output_structure(report, excel)
        check_resumen_consistency(report, excel)
        check_data_quality(report, excel)
    return report


# ============================================================================
# Renderer
# ============================================================================

_COLORS = {
    Status.PASS: "\x1b[32m",  # verde
    Status.WARN: "\x1b[33m",  # amarillo
    Status.FAIL: "\x1b[31m",  # rojo
    Status.SKIP: "\x1b[90m",  # gris
}
_RESET = "\x1b[0m"


def render(report: AuditReport, use_color: bool = True) -> str:
    """Renderiza el reporte en texto plano (con colores ANSI opcional)."""
    lines = []
    lines.append("=" * 72)
    lines.append("KPI Generator — Auditoría de salud")
    lines.append("=" * 72)
    for r in report.results:
        color = _COLORS[r.status] if use_color else ""
        reset = _RESET if use_color else ""
        lines.append(f"  {color}[{r.status.value:4s}]{reset} {r.name:30s} {r.message}")
        if r.detail:
            for dl in r.detail.splitlines():
                lines.append(f"           {dl}")
    lines.append("-" * 72)
    s = report.summary()
    parts = [f"{s.get('PASS', 0)} pass", f"{s.get('WARN', 0)} warn",
             f"{s.get('FAIL', 0)} fail", f"{s.get('SKIP', 0)} skip"]
    lines.append(f"  Total: {', '.join(parts)}")
    code = report.exit_code()
    verdict = {0: "SANO", 1: "DEGRADADO", 2: "CRITICO"}[code]
    lines.append(f"  Verdicto: {verdict} (exit code {code})")
    lines.append("=" * 72)
    return "\n".join(lines)


def supports_color() -> bool:
    """Detección razonable si la terminal soporta ANSI."""
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()

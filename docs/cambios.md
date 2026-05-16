# Changelog

## 0.2.0 — 2026-05-16 (Fase 1 de migración a Postgres)

Soporte para cargar cédulas desde la BD PostgreSQL `172.17.1.4 / cedula_direccion` del proyecto Cédula DG, manteniendo Excel y Sheets como fuentes alternativas configurables.

### Nuevo

- **`Config.CEDULAS_SOURCE`** ∈ {`db`, `excel`, `sheets`} — selector de fuente
- **`io/postgres.py`** — cliente psycopg2 con `get_connection()` context manager
- **`io/date_range.py`** — `derive_date_range(trips_file)` lee min/max de `Fecha creación` con I/O mínimo
- **`io/cedulas_db.py`** — `load_cedulas_from_db(fecha_min, fecha_max)` replica el contrato de `load_daily_cedulas`
- **CLI**: `--cedulas-source {db,excel,sheets}` en `run`; nuevo subcomando `diff-cedulas` para comparar fuentes
- **GUI**: dropdown "Fuente cédulas" arriba del campo Cédulas; validación condicional (no requiere carpeta si source=db)
- **Hoja extra `Cedulas Rellenadas`** en el Excel final: documenta qué (unidad, día) fue forward-fill vs real
- **Fallback configurable**: `FALLBACK_ON_DB_ERROR=true` + `FALLBACK_CEDULAS_PATH` activa Excel si la BD falla
- **Tests de integración** en `tests/integration/` (skip automático sin VPN)
- **`docs/migracion-cedulas-db.md`** con plan de 3 fases, query SQL y mapeo de columnas

### Cambiado

- `DataProcessor.load_data` ahora delega a `_load_cedulas_by_source` que dispatch a la fuente correcta
- `DataProcessor.generate_report` acepta `cedulas_source` opcional
- `DataProcessor.save_results` acepta `df_cedulas_audit` opcional
- Mapeo confirmado: `Operando` (Excel) ↔ `estatus_2` (BD)

### Estado del plan de migración

- ✅ Fase 1 — Implementación + lectura paralela (default `excel`, sin cambio de comportamiento)
- ⏳ Fase 2 — Validación con VPN activa: correr `diff-cedulas` y `test_pipeline_identidad`
- ⏳ Fase 3 — Switch default a `db` + cleanup

## 0.1.0 — 2026-05-16

Reestructuración profesional del monolito `KPI_Generatorv12.2.py` (2,548 líneas) sin tocar lógica.

### Validación end-to-end

Ejecutado contra datos reales del 16/05/2026 (`05 Mayo/16 Mayo/zmov.XLSX` + `zmva.XLSX`, 14 cédulas, objetivos de mayo):

| Métrica | Valor |
|---|---|
| Cédulas cargadas | 8,538 registros |
| Unidades en cédula | 579 |
| Viajes procesados | 11,774 |
| Comodatos generados | 4,745 |
| Cambios detectados | 32 (16 ingresos / 1 egreso / 15 operacionales) |
| OpCedula operaciones | 64 |
| Output Excel | 3.9 MB, 6 hojas |
| Google Sheets sync | ✅ 6 tabs actualizados |

**Bug encontrado y corregido durante validación:** `cli.py --output` esperaba archivo, pero `save_results` espera carpeta. Ajustado para coincidir con el contrato de la GUI.


### Cambios estructurales

- **Paquete instalable** en `src/kpi_generator/` con `pyproject.toml` (`pip install -e .`)
- **Versión reiniciada a 0.1.0** para iniciar historial limpio en GitHub
- **Separación de responsabilidades**:
  - `config.py` — Config + LogLevel + carga de `.env`
  - `domain/comodato.py` — ComodatoManager
  - `domain/change_tracker.py` — ChangeTracker
  - `domain/processor.py` — DataProcessor (motor principal)
  - `gui/widgets.py` — ScrollableFrame
  - `gui/app.py` — KPIGeneratorGUI
  - `io/sap.py` — extracción SAP (antes `extract_zvpf.py` en raíz)
  - `cli.py` — entry point CLI alterno a la GUI
  - `__main__.py` — `python -m kpi_generator` lanza GUI

### Seguridad

- Credenciales `google_service_account.json` movidas a `secrets/` (gitignored)
- `.env.example` con configuración exportable; secretos vía variable de entorno

### Tooling

- `.gitignore` cubre secretos, data-input, outputs, caches, IDEs
- `requirements.txt` espejo de `pyproject.toml` para entornos legacy
- `scripts/run_gui.bat` y `scripts/run_cli.bat` para lanzamiento rápido
- `tests/` carpeta lista para crecer (sin pruebas aún)

### Referencia

- Monolito original preservado en `_legacy/KPI_Generatorv12.2.py` (no editar — solo consulta)

---

## Histórico (pre-refactor)

- **v12.2** — versión activa al momento del refactor; OpCedula Period-Aware + Google Sheets
- **v12.1n** — preservada en `../Ejemplo Actual/KPI_Generatorv12.1n.py`

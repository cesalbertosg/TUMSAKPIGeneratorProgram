# Changelog

## 0.2.2 — 2026-05-21 (Fase 2 validada con BD real)

Primera ejecución contra Postgres `172.17.1.4 / cedula_direccion` con datos reales reveló
dos bugs críticos en el query SQL y forzó replantear el modelo de equivalencia BD-vs-Excel.

### Bugs corregidos durante validación

- **Query SQL traía 2,590 unidades vs 580 esperadas** — el CTE `ultima_previa` no estaba
  restringido al universo de unidades activas en el rango y arrastraba unidades históricas
  que dejaron de operar hace años. Corregido con un CTE `unidades_activas` que define el
  universo y un INNER JOIN sobre `ultima_previa`.
- **Centinela `'0'` aparecía como unidad legítima** — la BD tiene filas con `unidades='0'`
  (entradas malformadas históricas). Filtrado en `unidades_activas` con
  `TRIM(unidades) NOT IN ('', '0', '-')`.
- **Logs con flecha Unicode `→` rompían stdout en Windows cp1252** — reemplazado por
  `'a'` en mensajes de log (`processor.py:RNG`, `cli.py:diff-cedulas`).

### Cambio de expectativa: BD ≠ Excel bit-a-bit (y es lo correcto)

La validación reveló que el path BD produce reportes **estructuralmente más completos** que
el path Excel, no idénticos. Específicamente:

| Métrica | Excel | BD | Razón |
|---|---|---|---|
| Viajes reales | 7,029 | 7,029 | ✅ Idénticos (mismo `zmov.XLSX`) |
| KM total | igual | igual | ✅ Idéntico |
| Comodatos sintéticos | 7,638 | 4,775 | BD tiene más cédulas reales → menos comodatos |
| Períodos detectados | 2,641 | 2,286 | BD tiene mayor continuidad de datos reales |
| Cambios operacionales | 16 | 53 | BD detecta cambios diarios reales |
| OpCedula operaciones | 64 | 65 | BD descubre 1 operación adicional |
| Unidades únicas | 581 | 581 | ✅ Idénticas |

Las diferencias se explican porque los despachadores editan el Sheet de Drive en días
sin archivo Excel — la BD captura esas ediciones, los Excel no. **La BD es más fiel a
la realidad operativa.**

### Cambiado en tests

- `test_pipeline_db_vs_excel_genera_identico` (asercion `assert_frame_equal exact`) →
  `test_pipeline_db_y_excel_producen_resultados_equivalentes` que valida:
  - **Identidad estricta** en viajes REALES (excluyendo comodatos `>= 2_000_000_000`)
  - **Identidad estricta** en KM total
  - **Overlap ≥95%** en unidades únicas
  - **Overlap ≥90%** en operaciones cédula
  - Reporta diferencias en comodatos/períodos sin fallar

### Validación E2E del 21/05/2026 con BD

```
Rango derivado: 2026-05-01 a 2026-05-15
[DB] Recibidas 8601 filas crudas (565 semillas previas, 8036 dentro de rango)
[DB] Snapshot diario: 8555 filas (2270 rellenadas, 6285 reales)
[FINAL] Estructura final: 2286 registros
[CHG] 53 cambios (16 ingresos, 0 egresos, 37 operacionales)
[OPCED] 65 operaciones
[AUDIT] 2270 días por forward-fill de 8555 totales (26.5%)
[SHEETS] Google Sheets actualizado correctamente
```

### Suite de tests

```
9/9 PASS:
  tests/unit/test_cedulas_db_snapshot.py .... (5)
  tests/integration/test_db_vs_excel.py ..... (3)
  tests/integration/test_pipeline_identidad.py (1)
```

### Estado del plan de migración

- ✅ Fase 1 — Implementación (commit `e9c69be`)
- ✅ Fase 1.5 — Hardening (commit `cf2763f`)
- ✅ Fase 2 — Validación contra BD real (este commit)
- ⏳ Fase 3 — Cambiar `CEDULAS_SOURCE=db` por default

## 0.2.1 — 2026-05-20 (Hardening pre-Fase 2)

Endurece la implementación de v0.2.0 antes de la validación con VPN. Sin cambios funcionales para el usuario; mejora seguridad, performance y testabilidad.

### Cambiado

- **Query SQL usa `psycopg2.sql.Identifier`** para schema/table — elimina riesgo latente de SQL injection si `Config.PG_CEDULA_SCHEMA/TABLE` fueran manipulados
- **`build_daily_snapshot` vectorizado** — reemplaza doble loop Python (`O(N×M)`) por `MultiIndex.from_product` + `reindex` + `groupby.ffill`. En el rango típico de mes (580 unidades × 30 días) reduce ~17k iteraciones a operaciones nativas de pandas
- **`postgres.get_connection` configura `statement_timeout`** (default 60s vía `PG_STATEMENT_TIMEOUT_MS`) — corta queries colgadas en vez de bloquear el proceso indefinidamente
- **`build_daily_snapshot` renombrada sin guion bajo** (de `_build_daily_snapshot`) — ahora es API pública para que los tests unitarios la ejerciten sin pasar por la BD

### Nuevo

- **`tests/unit/test_cedulas_db_snapshot.py`** con 5 casos sintéticos (sin VPN):
  1. Día sin revisión en medio del rango → forward-fill correcto
  2. Unidad que aparece a mitad del rango → no genera filas antes (ingreso)
  3. Múltiples revisiones mismo día → drop_duplicates keep='last'
  4. Solo semilla previa → forward-fill cubre todo el rango
  5. DataFrame vacío → shape correcto sin romper downstream

### Bug fix encontrado durante hardening

- El test #4 detectó que la implementación anterior (también la actual antes de este commit) perdía las semillas `'previa'` al hacer `reindex` con un índice que no incluía sus fechas. **Si la BD tuviera cobertura parcial del rango, los días iniciales hubieran quedado vacíos en producción.** Corregido extendiendo el índice de reindex con las fechas de semillas previas, luego recortando al rango pedido tras el ffill.

### Verificado

- ✅ 5/5 tests unitarios pasan
- ✅ Pipeline E2E con `--cedulas-source excel` sin regresión

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

# Changelog

## 0.5.1 — 2026-06-11 (Cedula: fuente versatil + normalizacion + respaldo local + hoja Inconsistencias)

Bug original resuelto (`KeyError: "['Denominacion'] not in index"` con fuente
Sheets, commit `e2f936b`) mas el trabajo mas amplio que se deriva de ahi.

### Cedula formato "Completa" + normalizacion

- `Config.CEDULA_COLUMN_ALIASES` traduce columnas del Sheet/archivo "Completa"
  (`Unidad`, `ESTATUS`, `Estatus`, `OPERADOR`, `NO OPERADOR`, `OBSERVACIONES`) a
  nombres canonicos (`Unidades`, `Operando`, `Estatus Operador`, `Operador`,
  `No Operador`, `Observaciones`) en `load_daily_cedulas` y
  `load_cedula_from_sheet`.
- `normalize_text` (`domain/equipment.py`, NFKD sin combinantes) quita acentos
  y resuelve `Ñ`/`ñ` en `Gerencia`, `Operacion`, `Tipo de Unidad`, `Circuito`,
  `Operando`, `units_extra` y en `Operacion Cedula`/`Gerencia` del archivo de
  objetivos — el match `Operacion Cedula` cedula↔objetivos ya no se rompe por
  un acento de mas en cualquiera de los dos lados.
- `categoria_status` hace match case-insensitive contra el vocabulario
  canonico (`Puesto a Punto` y `Puesto A Punto` resuelven igual).

### `_apply_cedula_fallbacks` (universal, todas las fuentes)

Nuevo paso en `load_data`, despues de `_load_cedulas_by_source`: normaliza
texto, rellena `Gerencia`/`Operacion`/`Circuito` con
`Config.CEDULA_FIELD_DEFAULTS`, infiere `Tipo de Unidad` faltante (historico
de viajes via `CLAVE_CATEGORIA_A_TIPO_UNIDAD`, o prefijo del numero economico
via `Config.CEDULA_TIPO_UNIDAD_POR_PREFIJO`), y completa `units_extra`
(`Operador`/`No Operador`/`Estatus Operador`/`Observaciones`) con ffill/bfill
por unidad + `"Sin Info"` como ultimo recurso. Documentado en detalle en
[`docs/cedula-fallbacks-y-respaldo.md`](cedula-fallbacks-y-respaldo.md).

### Respaldo local "Completa" + cruce (fuente `sheets`)

- `save_cedula_as_completa` guarda la cedula del dia (`Cedula DDMMYYYY
  Completa.xlsx`) sin sobrescribir archivos existentes (preserva ediciones
  manuales).
- `load_local_cedulas_for_crossfill` + `crossfill_cedulas` completan
  `units_extra`/`units` faltantes desde cedulas "Completa" guardadas
  previamente, sin pisar valores ya presentes.
- **Acotamiento al rango del zmov**: `_load_cedulas_by_source` (rama
  `sheets`) usa `derive_date_range(trips_file)` (primer a ultimo viaje) para
  filtrar la cedula del Sheet antes de escribir/cruzar — nunca se genera
  "Completa" para dias sin viajes todavia. `fill_missing_dates` solo rellena
  huecos dentro de ese rango ya acotado (la "foto" del corte se asume sin
  cambios hacia adelante). `dias_mes`/`dias_corrientes`/`dias_restantes`
  (`PeriodContext`, usados por Tendencia/Objetivos) siguen calculandose por
  separado a partir del mes completo.
- **Sin carpeta de cedulas seleccionada**: la GUI (`validate_inputs`) muestra
  una confirmacion explicando que no habra respaldo ni cruce, dejando decidir
  al usuario; en CLI/headless se registra el mismo aviso como WARN. El uso sin
  carpeta nunca es implicito.

### Hoja "Inconsistencias"

Cada fallback/cruce aplicado (defaults, inferencia de Tipo de Unidad,
ffill/bfill, "Sin Info", cruce con cedula local) se registra y se vuelca a una
hoja `Inconsistencias` en el Excel y el Sheets de salida (omitida si no hubo
inconsistencias).

### Fixes de dtype pandas 3

Columnas creadas o normalizadas en `_apply_cedula_fallbacks` y
`crossfill_cedulas` se fuerzan a `dtype object` antes de asignaciones
parciales (`.loc`/`.at`) — evita `TypeError: Invalid value for dtype 'str'`
bajo `infer_string=True`.

### Tests

135 tests verdes (`pytest -q tests/unit`), incluye nuevos casos para
`normalize_text`/`categoria_status`, `_apply_cedula_fallbacks`,
`save_cedula_as_completa`/`load_local_cedulas_for_crossfill`/
`crossfill_cedulas`, match de objetivos con acentos, y acotamiento de cedula
Sheets al rango del zmov.

## 0.5.0 — 2026-06-04 (Reforma de output: 1 fila por equipo, dias asignado/activo)

Cambio mayor en la semantica de las tres hojas principales: **rompe contratos
con dashboards Looker existentes**. Migracion documentada en `v0.5.0-design.md`.

### Que cambia conceptualmente

- **Por Equipo** ya no es 1 fila por periodo de asignacion estable; ahora es
  **1 fila por equipo unico del periodo** (motrices + arrastres en la misma
  hoja, columna `Tipo Equipo`).
- **Asignacion vigente** = foto del ultimo dia del periodo. Equipos egresados
  o nunca asignados se reportan como `PENDIENTE` / `POR ASIGNAR`.
- **Arrastres** heredan asignacion del motriz dominante (mas co-viajes); su
  status se reconstruye como `Operando = dias con viaje`, `Disponible = resto`.
- **Dos clasificaciones de dias** que conviven:
  - Eje 1: `Dias Asignado + Dias Sin Asignacion = Dias corrientes`.
  - Eje 2: 8 sub-status canonicos (Operando, Disponible, Sin Operador, Taller,
    Gestoria, Descanso, Rescate, Puesto A Punto) + `Dias Otros Status`
    (resiliente: agrupa Activo/Baja/Inhabilitado/Cargada/Renovacion Licencia/
    Operador Incapacitado/Venta y cualquier status BD desconocido).
  - Eje 3: `Dias Activo` = dias con ≥1 viaje no comodato (transversal).
- **Objetivo Total** = Σ `Objetivo KM Diario` por dia asignado, sin importar
  el status (un dia en Taller dentro de VEND CENTRO sigue aportando objetivo).
- **% Operativo** = `Dias Activo / Dias corrientes × 100`.
- **Tendencia KM** = `KM Real + Dias restantes mes × Promedio KM/dia/unidad
  OpCedula × % Operativo / 100`, donde el promedio se calcula sobre TODAS las
  unidades-dia de la OpCedula en el mes actual.

### Que cambia en codigo

Nuevos modulos:

- `domain/period.py` — `PeriodContext.from_trips(df_trips)`: variables
  temporales (`dias_mes`, `dias_corrientes`, `dias_restantes`). Una unica
  fuente para todo el pipeline. Pre-condicion: zmov cubre un solo mes.
- `domain/equipment.py` — `EquipmentAggregator.aggregate()`: 1 fila por
  equipo unico. Catalogos `REMOLQUE_TIPOS`/`DOLLY_TIPOS` para clasificar
  Tipo Equipo desde `Tipo de Unidad` BD. Mapeo `categoria_status()` que
  preserva data nueva en `Dias Otros Status`.
- `domain/opcedula.py` — `OpcedulaAggregator.aggregate()`: 1 fila por
  OpCedula vigente (excluye `POR ASIGNAR*`). `post_calcular_tendencia()`:
  segundo pase que rellena `Tendencia KM/Viajes` en df_equipos y df_opcedula
  usando los promedios de cada OpCedula.

`processor.py` baja de **1688 → 1049 lineas (-639)**. Se borraron 14
funciones legacy del motor viejo: `create_periods`, `create_kpi_summary_optimized`,
`_create_phantom_kpis`, `_get_denominacion_*`, `_add_metrics_optimized`,
`_calculate_compliance_optimized`, `add_trailer_equipment_optimized`,
`_create_trailer_record`, `create_opcedula_summary`, `finalize_output`,
`_add_tendencia_complement_to_trips`, `_denormalize_kpis_to_trips` viejo,
`_build_promedio_km_sheet` viejo.

### Columnas eliminadas (vs v0.4.x)

En Por Equipo:
- `Fecha Inicio`, `Fecha Fin`, `Días Periodo` (ya no hay periodos).
- `Fecha Ultima modif` (duplica info del filename).
- `Denominación del equipo`, `Tipo de equipo` (sustituido por `Tipo Equipo`
  con valores `Motriz`/`Remolque`/`Dolly`).

En Por Operacion:
- `Sin Op` se renombra a `Sin Operador` (alinear con status canonico).
- `Diesel` se renombra a `Diesel LTS` (consistencia).
- Se agregan `Dias unidad asignados`, `Dias unidad activos`,
  `Promedio KM dia unidad`, `Promedio Viajes dia unidad`, `Tendencia Viajes`.

### Fix asociado (incluido en v0.5.0)

- KM Total ahora usa la columna `KM_total` ya calculada por
  `process_trips_optimized` (que aplica el fallback `KMLiqCargadoFinal → Distancia`
  cuando `StatusViaje='A'`, i.e. viaje no liquidado). Antes se sumaba
  `KMLiqCargadoFinal` directo, que valia 0 hasta la liquidacion.
  Impacto E2E (3 dias 01-03/06/2026): KM TOTAL TUMSA 341,426 → 535,001
  (+193,575), Cumplimiento KM TOTAL 71.4% → 111.9%.

### Tests

- 25 tests unit nuevos: PeriodContext (15) + OpcedulaAggregator (10).
- 39 tests para EquipmentAggregator (incluye 24 parametrize de
  clasificacion + mapeo de status, asignacion vigente con egreso/phantom,
  conteo de dias por las 3 clasificaciones, objetivos prorrateados,
  arrastres con motriz dominante).
- Total: 103 tests verdes (39 previos + 64 nuevos).

### Migracion Looker (lado usuario)

Reportes que usen las hojas afectadas deben re-conectar la fuente y
remapear campos. Ver `docs/migracion-looker-v0.5.0.md`.

---

## 0.4.3 — 2026-06-02 (Refactor I/O + tests + tema GUI)

Trabajo de saneamiento sin cambios funcionales. Tres paquetes:

### Paquete 1 — Cobertura unit de logica critica

Red de seguridad antes del refactor de I/O. 26 tests nuevos en `tests/unit/`:

- `test_cedula_filename.py` (7) — parser de filename `Cedula DDMMYYYY.xlsx`: formato
  canonico, tilde, mayusculas, separadores con espacios, fechas invalidas, sin
  patron, extension `.xls` legacy.
- `test_comodato.py` (10) — `ComodatoManager._get_operacion_cedula_comodato` con
  parametrize sobre SPECIAL_CIRCUITS + normalizacion a mayusculas. Integracion
  ligera de `create_comodatos`: dia faltante genera comodato, phantom unit ignorada,
  `En Cedula=False` ignorado.
- `test_change_tracker.py` (9) — `ChangeTracker._detect_unit_changes`: INGRESO al
  aparecer despues de fecha_min, sin-ingreso si arranca en fecha_min, EGRESO al
  desaparecer antes de fecha_max, OPERACIONAL detectado, sin cambios si misma
  OpCedula, circuito DEDICADO usa Tipo de Unidad, propagacion de objetivos,
  combinacion INGRESO+OPERACIONAL+EGRESO en el mismo rango.

39 tests verdes en total (13 previos + 26 nuevos).

### Paquete 2 — Extraer I/O Excel y Google Sheets

`DataProcessor` deja de tocar filesystem y APIs externas. Baja de **1952 a 1688
lineas** (-264). La logica de calculo no se toco.

Nuevos modulos:

- `io/excel.py` (212 lineas):
  - `parse_cedula_filename` — regex DDMMYYYY con `lru_cache`.
  - `fill_missing_dates` — forward-fill por fecha.
  - `load_daily_cedulas` — consolida `Cedula DDMMYYYY.xlsx` de una carpeta.
  - `write_workbook` — ExcelWriter + autoajuste de columnas.
- `io/sheets.py` (178 lineas):
  - `load_cedula_from_sheet` — cedula horizontal Sheets → vertical.
  - `sync_workbook_to_sheets` — sube `dict {tab: df}` con clear+update.

`DataProcessor` preserva la API publica (`load_daily_cedulas`, `load_cedula_from_sheets`,
`upload_to_sheets`, `save_results`); todos delegan a `io.*`. `save_results` conserva
la logica de presentacion (drops Tier 1, naming canonico, Resumen ejecutivo) y
delega la escritura fisica a `io.excel.write_workbook`.

### Paquete 3 — Extraer paleta GUI

`gui/theme.py` independiza la paleta del layout. Soporta switch via env var:

- `DARK_THEME` (paleta actual del KPI Generator).
- `LIGHT_THEME` (placeholder con schema identico, ajustable cuando se decida la
  paleta corporativa).
- `get_theme(name)` con fallback seguro a dark si el nombre no existe.

`Config.GUI_THEME` lee `KPI_GUI_THEME` del env (default `"dark"`). Sin cambios
visuales — la corrida normal se ve identica.

### Validacion

- 39 tests verdes despues de cada paso del refactor.
- Imports verificados: CLI, GUI y `DataProcessor` instancian sin error.
- API publica del processor preservada (mismos metodos).

---

## 0.4.2 — 2026-05-25 (Fix: Cuenta remolques con prorrateo)

### Bug

`Cuenta remolques` calculaba **1 o 2 por viaje** (cuántos slots de remolque llevaba el viaje).
Eso causaba dos problemas críticos en Looker:

1. **Sumas infladas**: `SUM(Cuenta remolques)` agrupado por OpCédula devolvía la suma de slots usados,
   no el número de remolques únicos. En SORIANA VILLA SENCILLO podía dar 280 cuando los remolques
   únicos reales eran 140.
2. **Duplicación cuando el mismo remolque aparece en R1 y R2 del mismo viaje** (caso real:
   T667 con remolque 40331 en ambas columnas) — sumaba 2 por ese remolque que en realidad es 1.

### Solución

Nueva función `_contar_remolques_unicos_prorrateado` que:

1. Aísla `(Operación Cedula, Remolque 1)` y `(Operación Cedula, Remolque 2)` como dos DataFrames
2. Los consolida en columna única `Remolque`
3. `drop_duplicates` por `(OpCédula, Remolque)` — el mismo remolque cuenta una sola vez aunque
   aparezca en R1 y R2 del mismo viaje
4. `groupby(OpCédula).size()` → total único por OpCédula
5. **Prorratea**: cada viaje con remolque registrado recibe `total_unicos / n_viajes_con_remolque`.
   Comodatos y viajes sin remolque reciben 0.

**Garantía:** `SUM(Cuenta remolques)` filtrado por OpCédula == número de remolques únicos
usados en esa OpCédula durante todo el período.

### Tests nuevos

`tests/unit/test_contar_remolques.py` con 8 casos:

- Remolque duplicado en R1 y R2 del mismo viaje cuenta 1
- Mismo remolque en 2 viajes de misma OpCédula cuenta 1 (prorrateo 0.5/0.5)
- Dos remolques distintos cuentan 2
- OpCédulas independientes
- Viajes sin remolque reciben 0 (no contaminan suma)
- Caso de imagen Beto: T667 / SORIANA VILLA con 40331 duplicado
- Caso compuesto multi-OpCédula
- Defensivo: sin columna OpCédula no truena

### Validación E2E (25/05/2026)

```
62 OpCédulas evaluadas — 0 discrepancias
SORIANA VILLA FULL:      SUM=117.00  Unique=117  OK
SORIANA VILLA SENCILLO:  SUM=140.00  Unique=140  OK
SORIANA VILLA PATIO:     SUM=  3.00  Unique=  3  OK
```

Suite: **24/24 pytest verde** (5 audit + 3 db_vs_excel + 1 pipeline + 5 cedulas_db + 8 remolques + 2 audit-full).

### Para iteración futura

El campo `cuenta llaverem` tiene el mismo patrón (conteo único por día+OpCédula vía `.transform('nunique')`)
que también suma mal en Looker. No se incluye en este commit por scope; aplicar la misma lógica
cuando se quiera consolidar.

## 0.4.1 — 2026-05-23 (Auditoría de salud)

Nuevo subsistema de auditoría: smoke tests (~3s) y validación E2E (~3-5min) con
exit code semaforizado (0=SANO, 1=DEGRADADO, 2=CRÍTICO) para schedulers.

### Nuevo

- **`src/kpi_generator/audit.py`** — módulo con clase `AuditReport` y 14 checks:
  - **quick** (sin BD ni archivos): version, imports, config, credentials, conectividad Postgres, conectividad Sheets
  - **full** (E2E): + pipeline run, estructura del output, deadweight ausente, naming canónico, consistencia del Resumen (suma gerencias = TOTAL), data sanity (rangos esperados), forward-fill %
- **CLI**: subcomando `kpi-run audit [--quick|--full] [--trips … --fuel … --objectives …]` con output coloreado (override `--no-color`)
- **Tests** `tests/integration/test_audit.py` — 7 casos:
  - 5 unit-like sobre quick mode (sin VPN)
  - 2 integration sobre full mode (requieren VPN + archivos)

### Cambiado

- Tests actualizados a los nombres de hoja canónicos v0.4.0 (`Viajes`, `Por Equipo`, `Por Operación`)

### Verdicto del audit en este commit

```
$ kpi-run audit --full --trips zmov.XLSX --fuel zmva.XLSX --objectives Obj.xlsx
  15/15 PASS · SANO · exit 0
```

Suite completa: **16/16 pytest verde**.

### Uso

```powershell
# Auditoría rápida (puede correr todo el tiempo)
kpi-run audit --quick

# Auditoría completa (semanal o pre-deploy)
kpi-run audit --full --trips ...\zmov.XLSX --fuel ...\zmva.XLSX --objectives ...\Obj.xlsx

# Como canary en Task Scheduler: exit code != 0 dispara alerta
```

## 0.4.0 — 2026-05-23 (Reestructura de output para Looker)

Refactor del reporte Excel y de los tabs en Google Sheets, motivado por análisis de
redundancias y necesidad de un dashboard ejecutivo. Sin cambios en la lógica de cálculo.

### Nuevo

- **Hoja `Resumen` (primera del Excel y tab nuevo en Sheets)** — agregación por gerencia
  + fila TOTAL TUMSA con 14 columnas: Unidades Activas, Operando, Taller, Gestoría, Sin Op,
  KM Total, Viajes, Diesel LTS, Rendimiento, Objetivo KM/Viajes, Cumplimiento KM %/Viajes %.
  Para Looker scorecards de página principal.

### Cambiado — naming canónico

Las hojas Excel y tabs Sheets se renombran para consistencia:

| Antes (Excel) | Antes (Sheets) | Ahora (ambos) |
|---|---|---|
| `KPIs per Equipment` | `Equipos` | **`Por Equipo`** |
| `Trip Data` | `Viajes` | **`Viajes`** |
| `KPIs OpCedula` | `OpCedula` | **`Por Operación`** |
| `PromedioKMunitOps` | `PromedioKMunitOps` | **`Promedio KM por Unidad`** |
| — | — | **`Resumen`** (nueva) |

Columnas también consistentes:
- `Operación cedula` (Viajes, Por Equipo) → **`Operación Cedula`** (alineado con Por Operación)
- `Operacion_cedula` → **`Operación Cedula`**
- `Promedio_Diario_KM_U` → **`Promedio Diario KM/U`**
- `Remolques_Unicos` → **`Remolques Únicos`**

### Eliminado — columnas deadweight (Tier 1)

| Hoja | Columnas removidas | Razón |
|---|---|---|
| Viajes | `llaveremolque`, `EqAsignados` | Intermediarios de pipeline; no consumidos por Looker |
| Por Equipo | `Días Gestoría` | Constante en 0 |
| Por Operación | `Dias Operando`, `Dias Taller`, `Dias Gestoria`, `Dias Sin Op` | Constantes en 0 |

Reducción: 7 columnas inútiles eliminadas.

### Conservado — denormalización de Viajes intacta

Tras análisis del propósito Looker documentado en `processor.py:1467` y `:1348`, se mantuvo
intacta la estructura de 74 columnas de `Viajes` (campos `*Foto`, métricas denormalizadas
de período/OpCedula). Su propósito es ser fuente única con multi-filtro jerárquico para Looker.

### Acción manual requerida

Tras este commit, en el spreadsheet de Google Sheets quedan tabs huérfanos con nombres viejos:
- `Equipos` (reemplazado por `Por Equipo`)
- `OpCedula` (reemplazado por `Por Operación`)
- `PromedioKMunitOps` (reemplazado por `Promedio KM por Unidad`)

**Borrar manualmente** y actualizar las fuentes de datos en Looker Studio para apuntar a los
nuevos tabs.

### Validación E2E (23/05/2026)

```
Rango: 2026-05-01 a 2026-05-22 (data de hoy)
[RESUMEN] Resumen ejecutivo: 9 gerencias + TOTAL
[OPCED] Hoja Por Operación: 65 operaciones
[SHEETS] Sheets 'Resumen': 10 filas
[SHEETS] Sheets 'Por Equipo': 2774 filas
[SHEETS] Sheets 'Viajes': 17373 filas
[SHEETS] Sheets 'Promedio KM por Unidad': 65 filas

TOTAL TUMSA: 582 unidades · 3,185,505 KM · 9,742 viajes ·
             Rendimiento 2.65 · Cumplimiento KM 93.5% · Cumplimiento Viajes 93.3%
```

### Para iteración futura (no incluido)

- Auditoría Looker vs Viajes para eliminar Tier 2 (columnas calculables en Looker:
  `KM_total`, `Rendimiento`, `Cuenta remolques`, etc.) — requiere acceso al dashboard
- Política de archivado de `Outputs/YYYY-MM/`
- Unificar gerencias mal escritas en BD: `PENDIENTE` y `Pendiente` aparecen como 2 distintas

## 0.3.0 — 2026-05-22 (Fase 3 — promoción de BD a fuente default)

PostgreSQL es ahora la fuente canónica de cédulas. Excel y Sheets se conservan
como fuentes alternativas configurables.

### Cambiado

- `Config.CEDULAS_SOURCE` default: `"excel"` → `"db"`
- `.env.example`: documenta `CEDULAS_SOURCE=db` como recomendación

### Comportamiento

- **Default (sin `--cedulas-source` ni env var)**: lee de Postgres
- **Override por env**: `CEDULAS_SOURCE=excel` o `CEDULAS_SOURCE=sheets` en `.env`
- **Override por flag**: `--cedulas-source {db,excel,sheets}` (gana sobre env)
- **Fallback automático**: si `FALLBACK_ON_DB_ERROR=true` y la BD cae, usa Excel sin abortar
- **Fallback manual**: el operador puede forzar Excel con `--cedulas-source excel` ante VPN caída
- **GUI**: el dropdown ahora arranca preseleccionado en `db`

### Validación E2E (22/05/2026)

```
# Sin flag, sin env override — usa el nuevo default 'db' del Config
kpi-run run --trips zmov.XLSX --fuel zmva.XLSX --objectives "Objetivo.xlsx" --output Outputs

[SRC] Fuente cédulas: PostgreSQL          ← lee del default
[RNG] Rango derivado de viajes: 2026-05-01 a 2026-05-15
[DB] Snapshot diario: 8555 filas (2270 rellenadas, 6285 reales)
[CHG] 53 cambios (16 ingresos, 0 egresos, 37 operacionales)
[OK] Reporte generado: KPIs_Transport_20260522_105607.xlsx
```

Path Excel sigue funcionando con `--cedulas-source excel` explícito (también validado).

### Migración completa

- ✅ Fase 1 (commit `e9c69be`) — Implementación inicial
- ✅ Fase 1.5 (commit `cf2763f`) — Hardening de seguridad y performance
- ✅ Fase 2 (commit `2719f5a`) — Corrección de query y validación contra BD real
- ✅ Fase 3 (este commit) — Promoción de BD a default

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

# Changelog

## 0.6.5 — 2026-07-09 (Gap-filler Drive en modo excel: auto-completar cédulas faltantes)

El modo `excel` ahora es un automatizador completo: si a la carpeta de cédulas
le faltan días del rango de viajes, los descarga del historial de revisiones
del Google Sheet y los guarda como `Cedula DDMMYYYY Completa.xlsx` — **sin
tocar jamás lo físico existente** (editado a mano a demanda de gerentes). En
la siguiente corrida esos archivos ya son físicos; si después se crea un
diario a mano para esa fecha, la fusión v0.6.4 le da la autoridad al diario.
Implementa el plan diferido `docs/plan-relleno-drive-modo-excel.md` con las
decisiones de Beto del 09/07/2026.

### Gap-filler (io/sheets.py + io/excel.py + processor)

- Nuevo `fetch_dates_from_revisions(sheet_id, log, dates, tab_name,
  save_folder, approximate_older, lineage)`: fuente única de la lógica Drive.
  `load_cedulas_for_period` (fuente sheets) se refactorizó para usarlo sin
  cambio de semántica; el modo excel lo consume vía callback armado en el
  processor (`io/excel.py` no importa red).
- `load_daily_cedulas(..., fecha_min, fecha_max, gap_fetcher)`: tras consolidar
  físicos pide al fetcher SOLO las fechas del rango de viajes sin archivo
  (filtro defensivo: aunque el fetcher devolviera de más, lo físico es
  intocable por construcción). Lo que Drive no cubra queda al forward-fill de
  siempre. Best-effort total: sin internet/credenciales degrada con
  advertencia visible (log + hoja Fuente Cedulas + GUI), nunca aborta.
- `approximate_older=False` en modo excel: un día anterior a toda revisión NO
  se aproxima con la revisión más vieja ni genera archivo (un archivo
  aproximado se volvería autoritativo en la siguiente corrida). La fuente
  sheets conserva su aproximación histórica.
- Cédulas "reducidas" (6 columnas, sin operadores) y "completas" (10 columnas)
  conviven e integran; la carpeta diarios+variantes en fechas DISTINTAS ahora
  es "carpeta combinada" (INFO, estado normal post-auto-completado) — el WARN
  de carpeta mixta se reserva para traslape en la misma fecha.

### Fix colateral (bug latente de v0.5.4/v0.6.2)

- `_extract_cedula_vertical_for_date` devolvía **0 registros** para los XLSX
  exportados del historial (`?revision=`): sus encabezados de fecha son celdas
  datetime que pandas rinde `"2026-07-06 00:00:00"`, y el extractor solo
  aceptaba `DD/MM/YYYY` (formato del sheet vivo). Nuevo `_parse_header_date`
  tolera ambos — las revisiones intermedias volvieron a ser utilizables
  (también beneficia a la fuente `sheets`).

### Hallazgo operativo documentado

- Google purga/consolida el historial del Sheet de cédulas en ~una semana
  (el 09/07 la revisión más antigua era del 03/07; las de junio ya no
  existen). El gap-filler sirve para huecos RECIENTES (el mes en curso);
  para meses viejos el guardado manual diario sigue siendo la única fuente.

### Verificación E2E (julio 2026 real, con red)

- Carpeta con diarios 01-05/07 y rango de viajes 01-08/07: descargó y guardó
  06, 07 y 08 (`[COV] 3 de 3`), contenido **idéntico** a las referencias de
  `Cedulas Completas\` (576 unidades, 0 diffs en Gerencia/Operación/Circuito).
- Segunda pasada idempotente: 0 consultas Drive, 0 ffill, sin advertencias.
- Sin revisiones disponibles (junio): degrada a forward-fill con advertencia,
  sin fabricar archivos.
- Suite: 171 tests unit (8 nuevos).

## 0.6.4 — 2026-07-08 (Trazabilidad de cédulas + fusión complementaria: "el físico manda al 100%")

Diagnóstico con el cierre de junio: un reporte generado creyendo estar en modo
`excel` mostró asignaciones del Sheet (C135/C137 el 07/06 con Operación "ZORRO"
cuando la cédula física dice "OFICCE MAX"). La causa NO fue el loader excel —
apuntado a la carpeta correcta respeta el físico al 100% — sino que el generador
podía correr con fuente/carpeta distinta a la creída (el dropdown de la GUI
arrancaba en el default del `.env` cada sesión, fallback silencioso
`db→sheets→excel`, carpeta de descargas "Completa" elegida por error) y el output
no dejaba NINGUNA traza de qué cédulas usó. Plan de origen: `plan.md`; el plan de
relleno Drive quedó diferido en `docs/plan-relleno-drive-modo-excel.md`.

### Trazabilidad — nueva hoja "Fuente Cedulas"

- Nuevo módulo `lineage.py` (`CedulaLineage`/`ArchivoCedula`): cada corrida
  registra fuente solicitada y efectiva, carpeta, archivos cargados (diario vs
  variante, rol en la fusión, filas, fecha de modificación), fechas cubiertas por
  físico/Drive/forward-fill, fallbacks y advertencias.
- Hoja "Fuente Cedulas" al final del Excel de salida (NO se sube a Google Sheets,
  mismo criterio que "Cedulas Rellenadas"); resumen de una línea en el log
  `[SRC]`, al final del CLI y en el diálogo de éxito de la GUI.
- La cadena de fallback `db→sheets→excel` se mantiene no-bloqueante, pero queda
  registrada en el linaje y visible (showwarning en la GUI).

### Fusión complementaria + invariante de unicidad (`io/excel.py`)

- `parse_cedula_filename_ex` clasifica `diario` (nombre canónico) vs `variante`
  (cualquier palabra extra: "Completa", etc.). `parse_cedula_filename` queda como
  wrapper — misma firma para sheets/CLI/GUI/tests.
- `load_daily_cedulas` agrupa por fecha: si conviven diario + variantes de la
  misma fecha se fusionan — **el diario manda campo por campo; la variante solo
  rellena celdas vacías** (p. ej. Operador, que el diario de 6 columnas no trae;
  reutiliza `crossfill_cedulas`). Unidades presentes solo en la variante NO se
  agregan (el diario define el universo del día). Antes, ambos archivos entraban
  al concat → producto cartesiano en el merge con viajes (viajes duplicados).
- Invariante duro post-consolidación: (Unidades, Fecha) único; si se viola →
  falla dura (mejor ningún reporte que uno inflado). Unidad repetida DENTRO de
  un archivo → keep-first + WARN + hoja Inconsistencias.
- WARN de carpeta mixta (diarios + variantes) y de carpeta con SOLO variantes —
  la trampa exacta del incidente de junio.
- `crossfill_cedulas` ahora deduplica el frame local antes del merge (cerraba el
  mismo producto cartesiano por la puerta de la fuente `sheets`).

### Guardrails GUI/CLI

- El dropdown "Fuente cédulas" recuerda la última selección en
  `%APPDATA%\KPI Generator\gui_state.json`; el `.env` solo decide la primera
  sesión (antes arrancaba en `db` cada vez).
- Indicador visual junto al combo: `EXCEL — carpeta física manda` (verde) /
  `SHEETS — asignación desde Drive` (ámbar) / `DB — PostgreSQL` (azul).
- `kpi-run run` imprime la fuente solicitada al inicio y el resumen de linaje al
  final.
- `generate_report` limpia `_inconsistencias` al arrancar: corridas sucesivas en
  la misma sesión de GUI ya no acumulan inconsistencias de corridas previas.

### Verificación E2E (junio 2026: `Cierre\zmov` + carpeta `Cedulas`)

- Regresión pura (30 diarios): **0 discrepancias** vs cédulas físicas (17,310
  pares unidad-día en 4 campos).
- Carpeta mixta (30 diarios + 28 "Completa"): **0 discrepancias** vs físico —
  C135/C137 el 07/06 = OFICCE MAX (el diario ganó), 23,220 viajes (sin
  duplicación) y mismos comodatos como conjunto (unidad, fecha).
- Solo variantes: reproduce el incidente (2 ZORRO) pero ahora señalizado (WARN en
  log + diálogo + hoja "Fuente Cedulas").
- Suite: 163 tests unit (18 nuevos: fusión, unicidad, linaje, clasificación del
  parser). Nuevo `scripts/compare_kpi_reports.py` para comparar reportes.
- Nota: la numeración de viajes sintéticos de comodato (2000000xxx) depende del
  orden de iteración — comparar comodatos entre reportes por (unidad, fecha), no
  por número.

## 0.6.3 — 2026-07-02 (Fix: cédulas "Cedula completa DDMMYYYY.xlsx" ignoradas + robustez CLI/versión)

Al correr el KPI del corte del 1° de julio (fuente `sheets`), `parse_cedula_filename`
(`io/excel.py`) no reconocía el archivo físico `Cedula completa 01072026.xlsx`: los
patrones sólo aceptaban "completa"/sufijos **después** de la fecha
(`Cedula 01062026 Completa.xlsx`), no **antes**. Resultado: la cédula autoritativa
guardada a mano se descartaba (`[PHYS] 0 días`) y `load_cedulas_for_period` la
re-descargaba de Drive API, creando un duplicado con otro nombre.

Fix: se añadió un `infix` opcional (sólo letras) entre "cedula" y la fecha, de modo
que ambas convenciones se reconocen — antes y después de la fecha, incluso multi-palabra
(`Cedula completa para auto 05072026.xlsx`). Cubierto en `tests/unit/test_cedula_filename.py`
(9 casos; suite 145 unit sin regresiones).

Esta versión también incluye dos endurecimientos surgidos del diagnóstico del build
viejo v0.5.1 que corría en escritorio:

- **CLI UTF-8** (`cli.py`): `main()` reconfigura stdout/stderr a UTF-8. Antes, un glifo
  como `→` en un log abortaba el run en consolas Windows (cp1252) con `UnicodeEncodeError`,
  enmascarado como "Error carga archivos". No afectaba a la GUI (widget Tk es Unicode-safe).
- **Versión visible** (`gui/app.py`): título/header/logs leen `kpi_generator.__version__`
  en vez de "v12.2"/"v11" hardcodeado, para que un build desactualizado se detecte de inmediato.

## 0.6.2 — 2026-06-24 (Fix: asignación estática incorrecta con fuente "sheets")

Diagnóstico del reporte de Beto sobre la unidad **C084**: con `--cedulas-source sheets`,
el resultado mostraba "Pendiente"/"Por Asignar" para los 31 días del periodo, cuando
en realidad la unidad estuvo asignada hasta el 18/06 y pasó a "Pendiente" recién el 19/06.

Causa raíz: el loader vigente (`load_cedula_from_sheet`) lee únicamente las columnas
**estáticas** (Gerencia/Operación/Tipo de Unidad/Circuito) del tab "Unidades Motriz",
que reflejan el estado ACTUAL de la unidad — no el histórico. Ese valor vigente se
aplicaba por igual a todas las fechas del rango, borrando cualquier cambio de
asignación ocurrido a mitad de mes.

Fix: se completó `load_cedulas_for_period` (`io/sheets.py`), un loader híbrido que
`processor.py` ya invocaba desde v0.6.0 pero que nunca se había terminado de commitear
(dejaba `main` con un `AttributeError` al usar fuente `sheets`). Estrategia:

1. **Paso 1 (autoritativo)**: lee archivos físicos diarios `Cedula DDMMYYYY.xlsx` en
   la carpeta de cédulas seleccionada — cada fecha conserva su asignación real de ese día.
2. **Paso 2**: para fechas sin archivo físico, recurre a revisiones históricas de
   Google Drive API (`_list_revisions` / `_extract_cedula_vertical_for_date`),
   guardando el resultado como `Cedula {fecha} Completa.xlsx` para reuso.
3. Si la conexión a Sheets/Drive falla y no hay carpeta física, devuelve `None` en
   vez de fabricar un resultado (el pipeline aborta el paso de cédulas explícitamente).

Regresión cubierta en `tests/unit/test_load_cedulas_for_period.py`: reproduce el caso
C084 (asignado días 1-2, "Pendiente" día 3) y verifica que el resultado varía día a
día en vez de heredar un solo valor. Se reescribieron 3 tests en
`test_cedula_sheets_date_range.py` que mockeaban el loader anterior (ya no usado por
`_load_cedulas_by_source`). Suite completa: 143 unit + 11 integration, sin regresiones.

Ver `docs/plan-historico-cedula-sheets.md` para el diagnóstico detallado y las fases
pendientes (tab "Histórico Operaciones", backfill, logger diario) que cubrirían el
caso donde NO hay carpeta física de respaldo.

## 0.6.1 — 2026-06-24 (Fix: confirmación al ignorar carpeta local de cédulas)

Durante pruebas en la computadora de Yaneth, el dropdown "Fuente cédulas" quedó
en `sheets` en vez de `excel` mientras ella sí seleccionaba una carpeta local de
cédulas. En ese modo, la asignación de unidades (Gerencia, Operación, Tipo de
Unidad, Circuito) siempre viene de Google Sheets — la carpeta local solo se usa
para completar Operador/No Operador/Estatus Operador/Observaciones vía
crossfill (`_load_cedulas_by_source`, `domain/processor.py`). El programa no
avisaba de este desajuste; el LEEME-Yaneth.txt ya pide confirmar "excel" en el
dropdown (paso 3) pero dependía de que el usuario lo recordara.

- `validate_inputs()` (`gui/app.py`) ahora bloquea con un diálogo de
  confirmación cuando hay una carpeta de cédulas seleccionada y "Fuente
  cédulas" no es `excel`, explicando que la asignación vendrá de Sheets/BD y
  no del archivo físico. El usuario puede continuar a propósito o regresar a
  corregir el dropdown.
- Alcance: solo GUI (uso interactivo). El CLI (`kpi-run`) no cambia — ahí la
  fuente se pasa explícita por `--cedulas-source`, sin ambigüedad de UI.

## 0.6.0 — 2026-06-20 (Atribución día-por-día en "Por Operación" + fila Pendiente + Motrices Utilizadas)

`OpcedulaAggregator` (`domain/opcedula.py`) atribuía el 100% del KM/Diesel/Viajes
del período a la OpCédula vigente al corte de cada equipo, sin importar bajo qué
OpCédula viajó cada día. Esto inflaba operaciones vigentes con KM de OpCédulas
ya retiradas del catálogo (ej. equipo reasignado a mitad de mes) y excluía por
completo las unidades fantasma (`POR ASIGNAR *`), generando un hueco de ~0.6%
entre las sumas de "Viajes" y "Por Operación".

- Nuevo `EquipmentAggregator.aggregate_detalle_opcedula()` (`domain/equipment.py`):
  agrupa `df_trips_validos` (excluye comodatos) por `(Equipo Motriz, Operación
  cedula)` día-por-día y reutiliza `_metricas_operativas()` por subgrupo — sin
  reimplementar la derivación de columnas.
- `OpcedulaAggregator.__init__` acepta `df_detalle_opcedula` (opcional,
  backward-compatible: si es `None` cae al comportamiento legacy por suma desde
  `df_equipos`). `_fila_opcedula()` separa identidad/status/días (desde la
  asignación vigente) de KM/Diesel/Viajes (desde el detalle día-por-día).
- Toda OpCédula histórica que ningún equipo tiene como vigente al corte
  (huérfana, igual criterio que ya existía para `POR ASIGNAR *`) se redirige a
  una fila consolidada `Gerencia = 'Pendiente'` — ya no se diluye en la vigente
  de quien sea el titular actual del equipo.
- Nueva columna `Motrices Utilizadas`: unidades distintas con ≥1 viaje real
  atribuido a esa OpCédula (día-por-día), junto a `Motrices Titulares` (vigente
  al corte, sin cambios). La divergencia entre ambas expone unidades tituladas
  sin actividad real en el período. Documentada desde el legacy
  (`docs/diccionario-viajes.md` columna #61) pero eliminada en v0.5.0 — ver nota
  en `docs/migracion-looker-v0.5.0.md`.
- Homologado el casing `'PENDIENTE'` → `'Pendiente'` en `equipment.py`,
  `change_tracker.py` y `processor.py` (Looker Studio separaba la dimensión
  Gerencia en dos valores cuando coexistían ambos casings).
- `_denormalize_kpis_to_trips_v050` (`domain/processor.py`) sanea las mismas
  claves huérfanas antes del merge OpCédula→Viajes, así los KM de días con
  OpCédula retirada matchean la fila `Pendiente` en vez de quedar sin join.

Verificado contra datos reales de producción (20/06/2026, fuente BD, 579
unidades / 63 operaciones): el hueco Viajes-vs-Por Operación bajó de ~0.6% a
**0.000%** exacto en KM, Diesel y Viajes.

Alcance explícito (sin cambios): Objetivos/Cumplimiento %/Tendencia siguen
sobre la asignación vigente; "Por Equipo" no cambia (1 fila por equipo, sin
partición por OpCédula histórica); remolques/arrastres siguen sin OpCédula
propia y excluidos de "Por Operación".

### Tests

5 tests nuevos (`test_opcedula.py` ×3, `test_equipment.py` ×2) cubren split
día-por-día con OpCédula huérfana, con dos OpCédulas reales repartidas, y el
agrupamiento de `aggregate_detalle_opcedula()`. 6 assertions de casing
actualizadas en `test_equipment.py`, `test_change_tracker.py`,
`test_assign_cedula_info.py`. Suite completa: 152 unit + integration en verde
(`pytest tests/`).

De paso se corrigieron 2 referencias a columnas obsoletas en
`tests/integration/test_pipeline_identidad.py`, preexistentes desde la
migración de nombres de hoja v0.4.0 (`'Operación Cedula'` → `'Operacion
Cedula'`, `'Unidades'` → `'Equipo Motriz'`) — nunca coincidían con el esquema
real, no relacionado con el cambio de este release.

## 0.5.4 — 2026-06-16 (Historial Drive API para columnas estáticas en fuente Sheets)

`load_cedula_from_sheet` (`io/sheets.py`) leía Gerencia/Operación/Circuito/Tipo
de Unidad del estado actual del sheet — todas las fechas del mes heredaban el
valor vigente al momento de la corrida, causando 0 cambios operacionales
detectados por `ChangeTracker` cuando la fuente de cédulas era `sheets`.

Ahora, antes del forward-fill, se consulta el historial de revisiones de Drive
API v3 (`_list_revisions` + `_fetch_revision_raw`). Para cada fecha presente en
el DataFrame se selecciona la revisión más reciente con `modifiedTime <=
23:59:59 UTC de ese día` y se parcha el bloque Gerencia/Operación/Circuito/Tipo
con los valores de ese snapshot (`_patch_static_from_revisions`).

- Nuevo parámetro `use_revision_history: bool = True` en
  `load_cedula_from_sheet` (backward-compatible).
- Se reutilizan `live_all_rows` para la revisión más reciente (evita descarga
  duplicada).
- Se agrupa por `revision_id` para minimizar llamadas HTTP.
- `_fetch_revision_raw` suprime `UserWarning` de openpyxl al parsear el XLSX
  exportado (columna `Num_Viaje` contiene números de orden SAP ~10^9 que
  openpyxl marca erróneamente como fechas seriales inválidas).
- Se declaran `google-api-python-client>=2.100` y `requests>=2.31` como
  dependencias explícitas en `pyproject.toml`.

Limitación conocida: Drive API conserva solo ~14 revisiones del período actual
(09/06–16/06 en junio 2026). Fechas anteriores a la revisión más antigua
usan esa revisión como aproximación.

## 0.5.3 — 2026-06-13 (Alias de cedula: UNIDAD/ESTATUS2)

Verificacion con datos reales de junio 2026 (fuente `excel`, carpeta "Cedulas
completas") encontro headers inconsistentes entre archivos `Cedula DDMMYYYY
Completa.xlsx`: algunos usan `Unidad`/`ESTATUS`, otros `Unidad`/`ESTATUS2`, y
uno `UNIDAD`/`ESTATUS2`. `ESTATUS2` trae el mismo vocabulario que `ESTATUS`
(Operando/Disponible/Taller/Sin Asignacion/...).

`Config.CEDULA_COLUMN_ALIASES` (`config.py`) ahora tambien mapea
`UNIDAD -> Unidades` y `ESTATUS2 -> Operando`.

## 0.5.2 — 2026-06-13 (Fantasma del dia: Pendiente/POR ASIGNAR en huecos puntuales de cedula)

`_assign_cedula_info_optimized` (`domain/processor.py`) agrupaba dos casos
distintos de "sin match en cedula" bajo el mismo tratamiento:

- **Desfase temporal de captura**: la cedula no tiene NINGUNA fila para la
  fecha D (para ninguna unidad) -> se mantiene la asignacion vigente de
  `unit_mapping` (sin cambios).
- **Fantasma del dia**: la cedula SI tiene filas para D (de otras unidades),
  pero esta unidad en particular no aparece ese dia -> antes heredaba su
  asignacion vigente de OTRO dia; ahora se marca `Gerencia=PENDIENTE`,
  `Operacion cedula=POR ASIGNAR {TIPO}`, `Operando=SIN ASIGNACION`, igual que
  una unidad sin cedula (`add_phantom_units_from_trips`). `Tipo de Unidad` se
  toma de `unit_mapping` (la unidad si esta en cedula otros dias).

`ChangeTracker` (INGRESO/EGRESO en "Resumen de Cambios") y
`EquipmentAggregator` (hoja "Por Equipo") ya manejaban correctamente estos
huecos por unidad -- sin cambios.

### Tests

137 tests verdes (`pytest -q tests/unit`), incluye 2 casos nuevos en
`test_assign_cedula_info.py` (fantasma del dia vs. desfase temporal).

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

# Plan v0.6.4 — Trazabilidad de cédulas + fusión complementaria (blindar "físico manda al 100%")

> Documento de trabajo. Beto corrige/anota aquí antes de implementar.
> Estado: **IMPLEMENTADO — v0.6.4 (2026-07-08)** con las decisiones recomendadas
> (a)-(e) tal como están marcadas en la sección 7. Verificación E2E: sección 5
> ejecutada con junio real — regresión pura y carpeta mixta con 0 discrepancias
> vs físico; solo-variantes reproduce el incidente pero señalizado. 163 tests
> unit en verde. Detalle en `docs/cambios.md` (0.6.4). Instalador: pendiente de
> reconstrucción cuando Beto lo pida.
> Sustituye al plan de relleno Drive, que queda **diferido** en
> `docs/plan-relleno-drive-modo-excel.md` (fase futura, con sus decisiones conservadas).

## 0. Objetivo en una línea

Garantizar que en modo `excel` las cédulas físicas diarias **manden al 100%**, y que
**toda corrida deje traza verificable** de qué fuente, carpeta y archivos de cédula usó.

## 1. Evidencia — diagnóstico junio 2026 (08/07/2026)

Comparación read-only por (unidad, fecha) de los reportes del 01/07 contra los 30 archivos
físicos de `...\2026\Q2\KPIs\06 Junio\Cedulas\`:

| Reporte | Origen | Resultado vs físico |
|---|---|---|
| `Dinamico\KPIs_Transport_20260701_101205.xlsx` | variante legacy (4 hojas) | **100.0% fiel**: 17,310 pares unidad-día, 0 discrepancias en Gerencia/Operación/Circuito/Operando |
| `Cedulas\KPIs_Transport_20260701_164424.xlsx` | paquete v0.6.x (8 hojas) | 2 discrepancias: **C135/C137 el 07/06 con Operación `ZORRO` (Sheet) en vez de `OFICCE MAX` (físico)** |

El caso ZORRO: `Cedula 07062026.xlsx` es copia del 06/06 (domingo, práctica normal), pero
el Sheet fue editado ese domingo y la revisión Drive capturó `ZORRO`. El run de las 16:44
reflejó el Sheet, no el físico. Tres evidencias demuestran que ese run **cargó datos con
esquema "Completa"** (carpeta `Cedulas completas\` o revisiones Drive), no `Cedulas\`:

1. Sus Inconsistencias tienen 12,004 entradas de Observaciones y 8,256 ffill/bfill de
   operadores — los diarios físicos (6 columnas) **no traen** esas columnas; las Completa sí.
2. Coincide **100%** con las Completa en Gerencia/Operación (incluido `ZORRO` el 07/06).
3. Sus únicos huecos de Circuito (T777/T793 → default `TERCERO`) son exactamente los que
   las Completa traen vacíos; los diarios físicos los traen llenos.

## 2. Causa raíz (qué es y qué NO es el bug)

**NO es** la lógica de carga del modo excel: apuntada a `Cedulas\`, respeta el físico.
**ES** que el generador puede correr con fuente/carpeta distinta a la que el usuario cree,
sin dejar rastro:

- El dropdown de la GUI arranca en el valor del `.env` (`CEDULAS_SOURCE=db`) **cada
  sesión** (`gui/app.py:41-44`) — hay que cambiarlo a `excel` manualmente cada vez.
- El diálogo de v0.6.1 (`gui/app.py:514-525`) se esquiva con un clic y solo cubre GUI.
- Cadena de fallback **silenciosa** `db→sheets→excel` (`processor.py:248-261`): con BD
  caída y `FALLBACK_ON_DB_ERROR=true`, la asignación sale de Sheets sin aviso visible.
- Una carpeta equivocada (p. ej. `Cedulas completas\`) se procesa sin advertencia.
- **El output no registra qué fuente/carpeta/archivos se usaron** — imposible auditar
  después (este diagnóstico tuvo que inferirlo por evidencia circunstancial).

Riesgo latente adicional en el loader (`io/excel.py:146-171`): dos archivos de la misma
fecha (posible desde v0.6.3, que parsea `Cedula completa DDMMYYYY.xlsx`) entran ambos a
`pd.concat` **sin regla** → producto cartesiano en el merge con viajes
(`processor.py:584-651`), viajes duplicados, y `ChangeTracker` con `iloc[0]/iloc[-1]`
indefinido (`change_tracker.py:63-67`).

## 3. Diseño

### W1 — Trazabilidad (pieza central): módulo `lineage.py` + hoja "Fuente Cedulas"

Nuevo módulo top-level `src/kpi_generator/lineage.py` (junto a `config.py`/`audit.py`,
importable desde `io/` y `domain/` sin ciclos):

```python
@dataclass
class ArchivoCedula:
    nombre: str; fecha: date; variante: str   # 'diario' | 'variante'
    mtime: datetime; filas: int
    rol: str                                   # 'unico' | 'base' | 'complemento' | 'descartado'
    detalle: str = ''

@dataclass
class CedulaLineage:
    fuente_solicitada: str; fuente_efectiva: str = ''; carpeta: str | None = None
    archivos: list[ArchivoCedula]; fechas_fisicas: list[date]
    fechas_ffill: list[date]; fechas_drive: list[date]
    fallbacks: list[str]; advertencias: list[str]
    carpeta_mixta: bool = False
    fusion_fills: list[tuple]                  # (unidad, fecha, campo, valor) → Inconsistencias

    def to_dataframe(self) -> pd.DataFrame     # hoja "Fuente Cedulas"
    def resumen_linea(self) -> str             # 1 línea para log / diálogo GUI
```

Plumbing (diff mínimo, retorno `(df, df_audit)` intacto):
- `load_data` (`processor.py:141-149`) crea `lineage` y lo pasa como **parámetro
  acumulador** a `_load_cedulas_by_source(..., lineage)`; la recursión del fallback
  escribe sobre el mismo objeto (`lineage.fallbacks.append("db→sheets: BD inaccesible…")`).
- Cada rama setea `fuente_efectiva`, carpeta y fechas; rama `db` deriva `fechas_ffill` de
  `df_audit['Origen']=='forward_fill'`; `load_cedulas_for_period` recibe
  `lineage=None` keyword (tests existentes intactos) y registra físico/Drive/gaps junto a
  sus logs `[PHYS]/[COV]` (`sheets.py:554-559`, `649-654`).
- `data['cedula_lineage']` + `self.last_lineage` (para GUI/CLI post-run).
- `lineage.fusion_fills` → `_registrar_inconsistencia(motivo='Completado por fusión con
  variante Completa (mismo día)')` → hoja Inconsistencias existente.

Hoja de salida (patrón de "Inconsistencias"/"Cedulas Rellenadas" — la escritura vive en
`processor.py:save_results:1015-1089`, no hay módulo `reports/` real):
- `SHEET_NAMES` (`processor.py:37-47`): `'fuente': 'Fuente Cedulas'`.
- `save_results(..., df_fuente_cedulas=None)` la agrega al final del workbook.
- Tabla plana con bloques: `CORRIDA` (fuente solicitada/efectiva, carpeta, versión,
  timestamp), `FALLBACK`/`ADVERTENCIA`, `ARCHIVO` (uno por archivo: nombre, fecha,
  variante, rol, filas, mtime), `FECHA` (origen por día: fisico/fusion/ffill/drive).
- **No** se sube a Google Sheets (mismo criterio que "Cedulas Rellenadas"). Decisión (e).
- Log `[SRC]` al final de la carga, GUI y CLI:
  `Fuente efectiva: EXCEL | Carpeta: …\Cedulas | 30 archivos (30 diarios, 0 variantes) | físico 01/06→30/06 | 0 ffill`.

### W2 — Hardening del loader excel (`io/excel.py`)

1. **`parse_cedula_filename_ex(filename) -> ParsedCedula(fecha, variante) | None`**:
   clasifica `'diario'` (nombre canónico `Cedula DDMMYYYY.xlsx` sin palabras extra) vs
   `'variante'` (cualquier palabra extra: "Completa", "Drive", etc.). La firma actual de
   `parse_cedula_filename` **no cambia** (queda como wrapper; la usan `sheets.py:534`,
   `excel.py:198/236`, GUI `cache_clear()` y tests).
2. **Fusión complementaria** en `load_daily_cedulas(folder, log, *, lineage=None)`:
   agrupar por fecha; con >1 archivo, `_fusionar_cedulas_mismo_dia`: **base = el diario**
   (conflicto de campo → gana base); variantes solo **rellenan celdas vacías**,
   reutilizando `crossfill_cedulas` (`excel.py:273-327`, ya implementa "rellenar sin pisar"
   sobre `units[1:] + units_extra` y devuelve el log de fills). Casos borde: 2+ diarios
   misma fecha → WARN + base = mtime más reciente; solo variantes → base = mtime más
   reciente. Unidades presentes solo en la variante: **no se agregan** (decisión (a)).
3. **Invariante de unicidad** (Unidades normalizada, Fecha) tras el concat y antes de
   `fill_missing_dates`: duplicados **entre archivos** que sobrevivan a la fusión →
   log ERROR + `return None` (falla dura: peor un reporte con cartesiano que ninguno).
   Duplicado **intra-archivo** (unidad repetida en el físico) → keep-first + WARN +
   Inconsistencias (decisión (b)).
4. **Detección de carpeta sospechosa**: diarios+variantes mezclados → WARN "carpeta mixta"
   en log y lineage; **cero diarios y solo variantes** → WARN explícito "la carpeta
   contiene SOLO variantes 'Completa' — verifica que sea la de cédulas físicas" (la trampa
   exacta del incidente de junio).
5. **Colateral en `crossfill_cedulas`**: `drop_duplicates` del `df_local_subset` antes del
   merge (`excel.py:292`) — cierra el mismo cartesiano por la puerta del modo sheets.

`fill_missing_dates` (`excel.py:72-107`) **sin cambios** (rango y semántica actuales).
`_apply_cedula_fallbacks` (`processor.py:273-397`) **INTACTA**.

### W3 — Guardrails de fuente efectiva

- **GUI**: el dropdown recuerda la **última selección** en
  `%APPDATA%\KPI Generator\gui_state.json` (best-effort; el `.env` solo decide la primera
  sesión) — decisión (c). Indicador visual junto al combo:
  `EXCEL — carpeta física manda` (verde) / `SHEETS — asignación desde Drive` (ámbar) /
  `DB — PostgreSQL` (azul). El diálogo de éxito (`processing_complete`) muestra
  `resumen_linea()`; si hubo fallback, carpeta mixta o solo-variantes →
  `showwarning` previo imposible de no ver. Diálogos v0.6.1 se conservan.
- **CLI** (`cli.py:_cmd_run`): imprime fuente solicitada al inicio y `resumen_linea()` al
  final.
- **Fallback `db→sheets→excel`**: se mantiene no-bloqueante (ya es opt-in vía
  `FALLBACK_ON_DB_ERROR`), pero queda **ruidoso y trazado** (lineage + hoja + diálogo) —
  decisión (d).

## 4. Tests (W4)

Extender `tests/unit/test_load_daily_cedulas.py` (mismo estilo: `tmp_path`, `_NOLOG`) +
nuevo `tests/unit/test_cedula_lineage.py`:

1. **Caso ZORRO** (fusión): diario 07/06 `OFICCE MAX`+Operador vacío + Completa 07/06
   `ZORRO`+Operador `Juan` → 1 fila, Operación=`OFICCE MAX`, Operador=`Juan`, fill en lineage.
2. Unicidad post-concat; duplicado intra-archivo → keep-first + WARN.
3. Unidad solo-en-variante no se agrega (parametrizado por decisión (a)).
4. Carpeta mixta y carpeta solo-variantes → flags/WARN correctos.
5. Linaje: días 1 y 3 físicos → `fechas_ffill==[2]`, archivos con variante/rol/filas.
6. **Regresión solo-diarios**: resultado idéntico al comportamiento actual; llamada sin
   kwarg `lineage` sigue funcionando (contrato de `cli diff-cedulas`).
7. `parse_cedula_filename_ex` (extiende `test_cedula_filename.py` sin tocar los 9 casos).
8. `crossfill_cedulas` con local duplicado no multiplica filas.
9. `to_dataframe()` + smoke de la hoja "Fuente Cedulas" en `save_results`.

`python -m pytest tests/unit -q` — cero regresiones sobre los 145 actuales.

## 5. Verificación end-to-end con junio (W5)

Formalizar el script del diagnóstico como `scripts/compare_kpi_reports.py`
(`--ref/--new/--sheet/--keys/--cols`, exit code estilo `diff-cedulas`). Corridas
controladas (`--cedulas-source excel`, sin upload, output temporal):

1. **Regresión pura**: `Cedulas\` → 0 diffs vs `Dinamico\KPIs_Transport_20260701_101205.xlsx`
   en Viajes/Por Equipo; hoja "Fuente Cedulas" con 30 diarios / 0 variantes.
2. **Carpeta mixta controlada** (temp con diarios + Completas solapadas, incl. 07/06):
   C135/C137 = `OFICCE MAX`; conteo de filas de Viajes idéntico al run 1 (sin cartesiano);
   comodatos y Resumen de Cambios idénticos; variantes marcadas `complemento` + WARN.
3. **Solo variantes** (apuntando a `Cedulas completas\`): reproduce el incidente pero
   ahora **señalizado** (WARN en log, diálogo y hoja).
4. **Fallback visible** (opcional, `FALLBACK_ON_DB_ERROR=true` + host inválido): hoja
   declara `solicitada: db / efectiva: sheets|excel`.
5. **GUI smoke**: arranca en la última fuente usada, indicador correcto, diálogo con linaje.

## 6. Implicaciones aguas abajo (resueltas por el invariante, sin tocar esos módulos)

- Merge viajes-cédula (`processor.py:584-651`): unicidad ⇒ sin producto cartesiano.
- `ChangeTracker` (`change_tracker.py:63-67`): orden determinista por fecha única.
- `ComodatoManager` (`comodato.py:28-107`): con "no agregar solo-variante" el universo de
  unidades no cambia ⇒ mismos comodatos que la referencia.
- Atribución OpCedula día-por-día y `create_unit_mapping`: un registro por unidad-día.
- Cambio de comportamiento **visible**: carpetas mixtas que hoy duplican viajes en
  silencio pasarán a fusionarse (números pueden BAJAR respecto a corridas contaminadas —
  eso es la corrección, y la hoja "Fuente Cedulas" lo documenta).

## 7. Decisiones a confirmar (Beto marca aquí)

- **(a) Unidad presente SOLO en la variante del mismo día:**
  - [x] NO agregar — el diario define el universo del día (*recomendado*; comodatos/cambios idénticos a la referencia)
  - [ ] Agregarla como fila extra (riesgo: una descarga vieja re-mete unidades borradas a propósito)
  - Nota de Beto: __________________________________________
- **(b) Duplicado intra-archivo (unidad repetida dentro del diario físico):**
  - [x] keep-first + WARN + Inconsistencias (*recomendado*; no bloquea producción por un typo)
  - [ ] Falla dura también
  - Nota de Beto: __________________________________________
- **(c) Arranque del dropdown de fuente en GUI:**
  - [x] Recordar última selección en `gui_state.json`; `.env` solo la primera vez (*recomendado*)
  - [ ] Mantener `.env` como arranque y solo resaltar visualmente
  - Nota de Beto: __________________________________________
- **(d) Fallback `db→sheets→excel`:**
  - [x] Mantener no-bloqueante pero ruidoso (hoja + diálogo + log) (*recomendado*)
  - [ ] Pedir confirmación previa en GUI cuando la fuente sea `db`
  - Nota de Beto: __________________________________________
- **(e) Hoja "Fuente Cedulas" en Google Sheets:**
  - [x] NO subir — solo Excel local, como "Cedulas Rellenadas" (*recomendado*)
  - [ ] Subir también como tab
  - Nota de Beto: __________________________________________

## 8. Fases de implementación

1. **F1**: `lineage.py` + `parse_cedula_filename_ex` (+ test 7).
2. **F2**: fusión complementaria + invariante de unicidad + carpeta mixta + dedup en
   `crossfill_cedulas` (+ tests 1-6, 8).
3. **F3**: plumbing de lineage en processor/sheets + hoja "Fuente Cedulas" (+ test 9).
4. **F4**: guardrails GUI/CLI (estado del dropdown, indicador, diálogos, prints).
5. **F5**: verificación E2E con junio (sección 5).
6. **F6**: bump `0.6.4` (pyproject + `__init__`), entrada en `docs/cambios.md`, sección de
   fusión/linaje en `docs/cedula-fallbacks-y-respaldo.md`, reconstrucción del installer —
   **solo tras aprobación de Beto**.

# Plan — Histórico de operaciones para cédula desde Google Sheets

Estado: **parcialmente implementado (2026-06-24)**. `load_cedulas_for_period`
(`io/sheets.py`) ya cubre el caso práctico más urgente — prioriza archivos
físicos diarios por fecha y usa Drive API solo para huecos, evitando que el
valor vigente del sheet se aplique a fechas pasadas — sin la infraestructura
de las Fases 1-3 (tab "Histórico Operaciones", backfill, logger diario). Útil
mientras la carpeta física de respaldo cubra el rango; sin esa carpeta, sigue
limitado a la cobertura de revisiones Drive (~14, ventana corta). Las
preguntas de la sección 6 siguen abiertas para decidir si vale la pena
construir las fases restantes.

---

## 1. Diagnóstico: por qué `--cedulas-source sheets` no detecta cambios

El tab "Unidades Motriz" de "Cédula Dirección General" tiene **54 columnas**:

| Rango | Tipo | Contenido |
|---|---|---|
| [00-13] | Estático (estado actual) | Unidad, Gerencia, Operación, Circuito, Tipo de Unidad, ESTATUS, OBSERVACIONES, Denominación, etc. |
| [14-50] | Fecha (DD/MM/YYYY) | ESTATUS del día: Operando / Disponible / Sin Operador / Taller / ... |
| [51-53] | Contadores | Taller, Gestoría, Sin operador (totales del período) |

Las **columnas de fecha** registran el ESTATUS diario de cada unidad. Las columnas
de **Gerencia / Operación / Circuito** son ESTADO ACTUAL — se actualizan cuando
alguien cambia la operación de una unidad, pero no guardan el histórico de lo que
era antes.

Resultado medido: `load_cedula_from_sheet` lee la fila de C135 y produce
**ZORRO TORTHON** para los 31 días de junio — incluso del 03 al 11/06, cuando la
operación real era OFICCE MAX TORTHON (confirmado por los archivos Excel diarios).
Todos los registros del mes heredan el valor actual de la columna `Operación`, no el
valor real de cada día.

**Consecuencia directa:** `ChangeTracker.track_operation_changes` sobre datos de
fuente Sheets detecta **0 cambios operacionales** (Operación idéntica para todos
los días → no hay diff que comparar). El tab "Cambios" en Google Sheets queda
vacío en cada corrida con fuente `sheets`, y por eso se "congela" en el snapshot
de la última corrida que sí tuvo filas.

---

## 2. ¿Por qué el historial de versiones Drive API no es suficiente solo?

El enfoque de historial de versiones tiene una limitación crítica en este caso:

```
Total revisiones disponibles vía Drive API: 14
Rango: 2026-06-09 17:27 — 2026-06-16 15:58
Creadas por: miriamcespedes
```

Google Drive comprime revisiones antiguas automáticamente para ahorrar almacenamiento.
Los auto-guardados de las primeras semanas del mes (junio 1-8) ya no existen como
revisiones discretas — quedaron fusionados en una sola. Solo hay 14 snapshots, todos
del 09/06 en adelante, con una frecuencia irregular de 1-3 por día de trabajo.

Usar Drive API de forma exclusiva:
- Cubre **junio 9-16** con resolución de ~horas (suficiente para detectar cambios diarios)
- No cubre **junio 1-8** (ninguna revisión disponible)
- Descargar cada revisión = exportar el archivo completo (~1-2 MB) via HTTP → lento para
  14 llamadas y peor si el número crece

Por eso se necesita un enfoque híbrido que combine todas las fuentes disponibles.

---

## 3. Propuesta: tab "Histórico Operaciones" + loader multi-fuente

### Arquitectura objetivo

```
Cédula Dirección General (Google Sheets)
├── Unidades Motriz      ← estado actual (sin cambios)
├── Operación DG         ← sin cambios
└── Histórico Operaciones  ← NUEVO: log vertical diario, una fila por unidad+día
```

Formato del tab "Histórico Operaciones":

| Fecha | Unidades | Gerencia | Operación | Circuito | Tipo de Unidad | Operando | Origen |
|---|---|---|---|---|---|---|---|
| 01/06/2026 | C135 | Veronica Barragan | ZORRO | DEDICADO | TORTHON | Sin Operador | excel |
| 02/06/2026 | C135 | Veronica Barragan | ZORRO | DEDICADO | TORTHON | Sin Operador | excel |
| 03/06/2026 | C135 | Veronica Barragan | OFICCE MAX | DEDICADO | TORTHON | Taller | excel |
| ... | | | | | | | |
| 16/06/2026 | C135 | Veronica Barragan | ZORRO | DEDICADO | TORTHON | Sin Operador | sheets_live |

La columna `Origen` marca de dónde vino el dato: `excel` / `sheets_rev_<id>` / `sheets_live`.

### Fases de implementación

#### Fase 0 — `snapshot_cedula_to_historico(date, source_label)` (función base)

Nueva función en `io/sheets.py` que:
1. Lee "Unidades Motriz" (estado actual).
2. Lee la columna-fecha DD/MM/YYYY correspondiente a `date` (ESTATUS del día).
3. Combina: fila estática (Gerencia, Operación, Circuito, Tipo de Unidad) + ESTATUS del día.
4. Upsert en "Histórico Operaciones": si ya existe la fila `(Fecha, Unidad)` la actualiza,
   si no la crea. Esto permite re-ejecutar sin duplicar.

Esto es la unidad de trabajo central que usan todas las fases siguientes.

#### Fase 1 — Backfill desde archivos Excel (junio 1-13)

Script de backfill `backfill_historico_from_excel.py` (ejecutar una sola vez):

- Para cada archivo `Cedula DDMMYYYY Completa.xlsx` en `data-input/cedulas/` o la
  ruta configurable:
  - Parsea la fecha del nombre del archivo.
  - Lee el DataFrame (Gerencia, Operación, Circuito, Tipo de Unidad, ESTATUS = `Operando`).
  - Llama a `upsert_historico_rows(date, df_dia, origen='excel')`.

Cubre junio 1-13 con datos de alta fidelidad (Miriam generó esos archivos).

#### Fase 2 — Backfill desde revisiones Drive API (junio 9-16)

Script de backfill `backfill_historico_from_revisions.py` (ejecutar una sola vez):

- Lista las 14 revisiones disponibles.
- Para cada fecha en el rango 09/06-16/06:
  - Encuentra la revisión más cercana a las 23:59 de ese día.
  - Si es la misma revisión que el día anterior → `fill_missing_dates` rellenará el hueco.
  - Si es revisión diferente → descarga el export XLSX de esa revisión via Drive API
    `files.export(fileId, revisionId, mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')`.
  - Parsea la hoja "Unidades Motriz" del export → extrae Gerencia/Operación/Circuito del día.
  - Llama a `upsert_historico_rows(date, df_dia, origen='sheets_rev_<id>')`.

Esto permite **validar** los datos de Excel (Fase 1) contra revisiones reales para
las fechas solapadas (09-13/06), y rellenar junio 9-16 con cobertura real donde
sea distinto al Excel.

Nota de scope: se necesita `drive` scope para `files.export` —
`https://www.googleapis.com/auth/drive.readonly`. Ya está en `Config.SHEETS_SCOPES`.

#### Fase 3 — Logger diario al final del día

Script `append_cedula_snapshot.py` (o workflow n8n / Task Scheduler):

```python
from kpi_generator.io.sheets import snapshot_cedula_to_historico
from datetime import date
snapshot_cedula_to_historico(date.today(), origin='sheets_live')
```

Ejecutar cada día hábil a las ~18:00 (cuando el equipo de Miriam ya actualizó la
columna del día). Opciones de scheduling:

- **Task Scheduler Windows**: `registrar_tarea_cedula.ps1` similar al de TUMSA Monitoreo X.
- **n8n**: nodo Schedule trigger (18:00 L-V) → nodo Python/HTTP → `append_cedula_snapshot`.

Esto garantiza cobertura exacta de un dato por día a partir de hoy en adelante.

#### Fase 4 — Actualizar `load_cedula_from_sheet`

Nuevo parámetro: `tab_name='Histórico Operaciones'` (o constante en Config).

Cuando se especifica la tab histórica, el loader:
1. Lee el tab vertical directamente (formato ya compatible con el pipeline:
   una fila por Unidad+Fecha con Gerencia/Operación/Circuito/ESTATUS).
2. Parsea `Fecha Cedula_dt` desde la columna `Fecha`.
3. Aplica `fill_missing_dates` para huecos (igual que hoy).
4. Devuelve df en el mismo formato que el loader actual → **cero cambios en el
   resto del pipeline**.

El selector `--cedulas-source sheets` (o env `CEDULAS_SOURCE=sheets`) pasaría a
usar el tab histórico. El tab "Unidades Motriz" seguiría siendo leído SOLO por
el paso de unidades fantasma (`add_phantom_units_from_trips`) donde necesitamos
la lista de unidades activas, no el histórico de operaciones.

Alternativa más conservadora: agregar `--cedulas-source sheets_historico` como
opción separada en el CLI, manteniendo `sheets` con el comportamiento actual hasta
que "Histórico Operaciones" tenga suficiente cobertura para ser la fuente por
defecto.

---

## 4. Impacto en el pipeline tras implementar las 4 fases

| Escenario | Antes | Después |
|---|---|---|
| `--cedulas-source sheets`, junio 1-8 | Operación estática incorrecta | Datos de Excel (Fase 1) |
| `--cedulas-source sheets`, junio 9-16 | Operación estática incorrecta | Datos de Excel + revisión Drive (Fases 1-2) |
| `--cedulas-source sheets`, junio 17+ | Operación estática (correcta solo si no hubo cambios) | Datos del logger diario (Fase 3) |
| `ChangeTracker` con fuente Sheets | 0 cambios detectados siempre | Cambios reales detectados ✓ |
| Tab "Cambios" en Google Sheets | Congelado en 15/05/2026 | Actualizado en cada corrida ✓ |

---

## 5. Dependencias y riesgos

| Item | Detalle |
|---|---|
| Scope Drive ya configurado | `Config.SHEETS_SCOPES` tiene `drive` — sin cambios necesarios |
| Write a cédula Sheets | La service account ya tiene acceso a "Cédula DG" (lee OK con `gspread`). Verificar que también tiene **write** (la service account debe estar como Editor en el spreadsheet) |
| Revisiones solo desde 09/06 | Junio 1-8 depende exclusivamente de los archivos Excel diarios |
| "Histórico Operaciones" tab nuevo | Al crear el tab, Looker Studio debe ignorarlo (no está como fuente) — sin impacto |
| Huecos por días sin logger | Si el logger falla un día, `fill_missing_dates` rellenará con el día anterior — mismo comportamiento que hoy para cédulas sin archivo |

---

## 6. Preguntas para Beto antes de implementar

1. ¿Dónde viven los archivos Excel de cédula cuando el pipeline corre en la máquina
   de Yanet? (La ruta `data-input/cedulas/` debe ser configurable via `.env` o
   argumento CLI para el backfill.)
2. ¿La service account tiene permiso de **escritura** en "Cédula Dirección General"?
   (Solo se ha verificado lectura hasta ahora.)
3. ¿Prefiere `--cedulas-source sheets_historico` como fuente separada (conservador)
   o migrar directamente `sheets` al tab histórico?
4. Para el logger diario: ¿Task Scheduler en la máquina de Miriam/Yanet, o n8n cloud?

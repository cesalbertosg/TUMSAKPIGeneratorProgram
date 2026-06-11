# Cédula: reglas universales, respaldo local y cruce (fuente "sheets")

Documenta la lógica agregada en el plan "Cédula: fuente versátil + normalización
+ respaldo local + hoja de inconsistencias" (commit `32a7b4d`).

## Alcance — qué aplica a qué fuente

El pipeline de cédulas tiene dos capas independientes:

| Capa | Aplica a | Dónde vive |
|---|---|---|
| **Normalización y fallbacks** (`_apply_cedula_fallbacks`) | **TODAS** las fuentes (`db`, `excel`, `sheets`) | `domain/processor.py`, llamado desde `load_data` justo después de `_load_cedulas_by_source` |
| **Respaldo local "Completa" + cruce** | Solo fuente `sheets` (con carpeta de cédulas seleccionada) | `_load_cedulas_by_source`, rama `source == "sheets"` |

La sección "Reglas universales" describe la primera capa; "Flujo específico de
fuente sheets" describe la segunda.

## Fuente de verdad

La fuente de verdad operativa es el Google Sheet **"Cédula Dirección General"**
(`Config.CEDULA_SHEET_ID`). Ahí los gerentes de operaciones vierten el
conocimiento de asignaciones, estatus diario y observaciones del equipo
motriz — el Sheet incluye columnas `OPERADOR`, `NO OPERADOR`, `ESTATUS`
(status corto, distinto de `Operando`) y `OBSERVACIONES`. Es susceptible a
errores de captura, pero es lo más cercano a la verdad que existe.

Diariamente, Beto descarga manualmente las celdas correspondientes a Excel y
las guarda como `Cedula DDMMYYYY Completa.xlsx`. Históricamente, a esos
archivos les retiraba las columnas de Operador/No Operador/Estatus
Operador/Observaciones para que los pudiera leer la versión antigua del
programa — por eso los archivos "Completa" guardados en el pasado **no**
traen esas columnas, aunque el Sheet sí.

`Config.CEDULA_COLUMN_ALIASES` traduce los nombres de columna de ambas
fuentes a los nombres canónicos del pipeline:

| Columna origen | Columna canónica |
|---|---|
| `Unidad` | `Unidades` |
| `ESTATUS` | `Operando` |
| `Estatus` | `Estatus Operador` |
| `OPERADOR` | `Operador` |
| `NO OPERADOR` | `No Operador` |
| `OBSERVACIONES` | `Observaciones` |

La "versatilidad" pedida cubre dos objetivos:

1. Que el programa no se rompa si el Sheet agrega columnas nuevas — columnas
   no listadas en `units`/`units_extra` (vía sus alias) simplemente se
   ignoran.
2. Que los archivos "Completa" históricos (sin Operador/No
   Operador/Estatus Operador/Observaciones) sigan siendo útiles vía el cruce
   (`crossfill_cedulas`) con cédulas más recientes que sí traigan esa info.

## Reglas universales: `_apply_cedula_fallbacks` (Cambio 5)

Se ejecuta en `load_data` para **toda** fuente de cédulas (`db`, `excel`,
`sheets`), después de `_load_cedulas_by_source` y antes de cualquier cálculo
de KPI. Cuatro pasos, en orden, cada ajuste registrado en la hoja
"Inconsistencias":

### 1. Normalización de texto (acentos, Ñ, espacios)

`normalize_text` (`domain/equipment.py`) aplica NFKD + descarta caracteres
combinantes: quita acentos (á→a, é→e, ...) y resuelve Ñ→N / ñ→n, sin tocar
mayúsculas/minúsculas. Se aplica con `.strip()` previo a `Gerencia`,
`Operación`, `Tipo de Unidad`, `Circuito`, `Operando` y a cualquier columna
`units_extra` presente.

Por qué importa: `Operación Cedula` (calculado en `_get_operacion_cedula`
como `Operación + Circuito` o `Operación + Tipo de Unidad`, ambos `.upper()`)
se usa para emparejar contra `Operación Cedula` del archivo de objetivos. Un
acento de más en cualquiera de los dos lados rompía el match silenciosamente
(el objetivo caía a 0). Ahora ambos lados se normalizan igual — el archivo de
objetivos también se normaliza en `load_data` (`Operación Cedula` y
`Gerencia`, ~línea 159-164).

`Operando` no recibe default propio: una cadena vacía se preserva tal cual —
`categoria_status` la trata como `'Otros Status'`.

### 2. Defaults para Gerencia/Operación/Circuito

Si vienen vacíos (NaN tras el paso 1), se rellenan con
`Config.CEDULA_FIELD_DEFAULTS`:

| Campo | Default |
|---|---|
| `Gerencia` | `Pendiente` |
| `Operación` | `SIN ASIGNAR` |
| `Circuito` | `TERCERO` |

Cada fill se registra con motivo "Faltante en cédula".

### 3. Tipo de Unidad faltante

Si `Tipo de Unidad` viene vacío:

- **Con histórico de viajes**: se toma la `ClaveCategoria` del viaje más
  reciente de esa unidad (`Equipo Motriz`, ordenado por `Fecha creación`) y
  se mapea vía `CLAVE_CATEGORIA_A_TIPO_UNIDAD` (`domain/equipment.py`).
  Motivo registrado: "Tipo de Unidad inferido de histórico de viajes".
- **Sin viajes**: se infiere por el prefijo del número económico
  (regex `^([A-Z])\d`) vía `Config.CEDULA_TIPO_UNIDAD_POR_PREFIJO`:

  | Prefijo | Tipo de Unidad |
  |---|---|
  | `L` | `CAMIONETA` |
  | `C` | `TORTHON` |
  | `T` | `SENCILLO` |
  | otro | `DESCONOCIDO` |

  Motivo registrado: "Tipo de Unidad inferido de prefijo de número
  económico".

### 4. units_extra: ffill/bfill + "Sin Info"

Si la cédula trae **al menos una** columna de `Config.COLUMNS["units_extra"]`
(`Operador`, `No Operador`, `Estatus Operador`, `Observaciones`):

- Se asegura que existan las 4 columnas (las ausentes se crean vacías).
- Por `Unidades`, ordenado por `Fecha Cedula_dt`, se aplica `ffill()` seguido
  de `bfill()` — un valor capturado un día se propaga a los días vecinos sin
  captura propia.
- Lo que siga vacío después de ffill/bfill → `"Sin Info"`.

Cada celda rellenada por ffill/bfill se registra con motivo "Completado por
ffill/bfill"; cada celda que cae a "Sin Info" se registra con motivo "Sin
información disponible".

**Si NINGUNA columna `units_extra` está presente** (fuentes `db`/`excel`
clásicas sin esa info), este paso se omite por completo: no se crean columnas
`units_extra` artificiales ni se generan inconsistencias por este motivo.

## Flujo específico de fuente "sheets": respaldo local + cruce (Cambio 2-3)

Solo cuando `cedulas_source == "sheets"` **y** se proporciona
`cedulas_folder`:

```
load_cedula_from_sheet(sheet_id)
   │  snapshot completo del Sheet: todas las columnas-fecha DD/MM/YYYY
   │  presentes, forward-fill de 'Operando' por unidad +
   │  fill_missing_dates sobre el rango propio del Sheet
   ▼
df (Sheet)
   │
   ▼
derive_date_range(trips_file)
   │  primer y ultimo viaje del zmov seleccionado → (fecha_min, fecha_max)
   ▼
df = df[(Fecha Cedula_dt >= fecha_min) & (Fecha Cedula_dt <= fecha_max)]
   │  descarta dias del Sheet fuera del periodo del zmov (p.ej. el resto
   │  del mes en curso sin viajes todavia)
   ▼
fill_missing_dates(df)
   │  idempotente: solo rellena huecos dentro del rango ya acotado —
   │  la "foto" del corte se asume sin cambios hacia adelante, pero nunca
   │  se genera un dia fuera de [fecha_min, fecha_max]
   ▼
df (acotado al rango del zmov)
   │
   ▼
¿cedulas_folder?
   │
   ├─ NO → log WARN "Sin carpeta de cédulas seleccionada: no se genera
   │        respaldo local 'Completa' ni se completa Operador/No Operador/
   │        Estatus Operador/Observaciones..." — df se usa tal cual, sin
   │        abortar el pipeline
   │
   └─ SI ▼
      save_cedula_as_completa(df, cedulas_folder)
         │  por cada Fecha Cedula_dt única en df (ya acotado al zmov):
         │    si NO existe ya un archivo "Cedula DDMMYYYY*.xlsx" para esa fecha
         │      → escribe "Cedula DDMMYYYY Completa.xlsx"
         │    si ya existe (cualquier sufijo reconocido por parse_cedula_filename)
         │      → no toca el archivo (preserva ediciones manuales)
         ▼
      load_local_cedulas_for_crossfill(cedulas_folder)
         │  lee todos los "Cedula DDMMYYYY*.xlsx" de la carpeta (best-effort,
         │  aplica los mismos CEDULA_COLUMN_ALIASES), devuelve df_local
         │  (vacío si la carpeta no existe o no hay archivos válidos — no aborta)
         ▼
      crossfill_cedulas(df, df_local)
         │  merge por (Unidades, Fecha Cedula_dt)
         │  para cada columna de units[1:] + units_extra presente en ambos:
         │    si df (Sheet) viene vacío/NaN y df_local trae valor → lo toma de
         │    df_local. Nunca pisa un valor ya presente en df.
         │  cada fill → inconsistencia "Completado por cruce con cédula local
         │  guardada"
         ▼
df final → _apply_cedula_fallbacks (capa universal, sección anterior)
```

Notas:

- El acotamiento a `[fecha_min, fecha_max]` (Cambio "Pendiente 2", ya
  implementado) corre **siempre** para la fuente `sheets`, con o sin carpeta
  de cédulas — `save_cedula_as_completa` jamás escribe un archivo "Completa"
  para un día sin viajes todavía en el zmov. Si `derive_date_range` falla
  (zmov sin fechas válidas), se loggea WARN y `df` se usa sin acotar.
- `dias_mes`/`dias_corrientes`/`dias_restantes` (`PeriodContext.from_trips`,
  usados por Tendencia/Objetivos) se calculan por separado a partir del mes
  del periodo del zmov — **no** dependen del rango acotado de la cédula. El
  acotamiento solo afecta qué días de cédula se guardan/usan como "foto";
  los totales mensuales para proyecciones siguen cubriendo el mes completo.
- `save_cedula_as_completa` corre **antes** de
  `load_local_cedulas_for_crossfill`, así que en una carpeta vacía el primer
  run escribe el snapshot actual del Sheet (ya acotado) como "Completa" y
  luego se lee de vuelta — el cruce es esencialmente un no-op la primera vez
  (`df` y `df_local` coinciden). El cruce cobra valor en runs posteriores, o
  cuando la carpeta ya tenía archivos "Completa" históricos (sin
  `units_extra`, por el motivo descrito en "Fuente de verdad").
- `save_cedula_as_completa` **nunca sobrescribe ni borra** archivos
  existentes — preserva ediciones manuales de Beto.
- Si no se selecciona carpeta de cédulas, la GUI (`validate_inputs`) muestra
  un diálogo de confirmación que explica el efecto (sin respaldo local, sin
  cruce) y permite al usuario decidir si continuar o regresar a seleccionar
  una carpeta — el uso sin carpeta nunca es implícito.

## Hoja "Inconsistencias" (Cambio 6)

Todas las decisiones anteriores (defaults, inferencias, ffill/bfill, "Sin
Info", cruces) se acumulan en `self._inconsistencias` y se exportan como
hoja `Inconsistencias` (Excel) / tab `Inconsistencias` (Sheets), columnas
`Unidad, Fecha, Campo, Valor Original, Valor Aplicado, Motivo`. Si no hubo
inconsistencias en la corrida, la hoja/tab simplemente no se crea
(`write_workbook`/`sync_workbook_to_sheets` omiten DataFrames vacíos).

## Decisiones cerradas (antes "Pendientes")

### 1. Carpeta de cédulas no seleccionada — confirmación explícita en GUI

Beto rechazó la idea de un default implícito (`Path(output_path) /
"Cedulas"`): *"no dejes implicito el uso"*. En su lugar, si la fuente es
`sheets` y no se seleccionó carpeta de cédulas:

- La GUI (`validate_inputs`, `gui/app.py`) muestra un `askyesno` que explica
  el efecto (sin respaldo "Completa", sin cruce de
  Operador/No Operador/Estatus Operador/Observaciones) y deja decidir al
  usuario si continúa sin carpeta o regresa a seleccionar una.
- Independientemente de la GUI, `_load_cedulas_by_source` registra un WARN
  ("Sin carpeta de cédulas seleccionada: ...") — visible también en CLI/
  headless, donde no hay diálogo posible.

### 2. Acotar el rango de fechas al de `zmov`

Implementado: `_load_cedulas_by_source` (rama `sheets`) usa
`derive_date_range(trips_file)` para acotar `df` (Sheet) a `Fecha Cedula_dt`
∈ `[fecha_min, fecha_max]` (primer a último viaje del zmov) **antes** de
`save_cedula_as_completa`, y luego aplica `fill_missing_dates` (idempotente,
solo rellena huecos dentro de ese rango ya acotado). Así:

- `save_cedula_as_completa` nunca escribe "Completa" para un día sin viajes
  todavía — *"LAS CEDULAS que escribe 'save_cedula_as_completa' SOLO DEBEN
  SER LAS QUE PERTENEZCAN AL RANGO DE FECHAS QUE ABARCA EL ZMOV
  SELECCIONADO"* (Beto).
- La última cédula/"foto" corresponde a la fecha del último viaje, no al
  último día del mes — pero esa "foto" se asume sin cambios desde el corte
  hacia adelante para fines de proyección (lógica de Tendencia, sin cambios:
  no genera asignaciones futuras, solo proyecta con el último estado
  conocido).
- `dias_mes`/`dias_corrientes`/`dias_restantes` (`PeriodContext.from_trips`)
  siguen calculándose por separado a partir del mes del periodo — no se ven
  afectados por este acotamiento.
- Si `derive_date_range` falla (`DateRangeError`), se loggea WARN y `df` se
  usa sin acotar (no aborta el pipeline).
- El programa nunca borra archivos de cédula existentes; solo escribe los
  días del rango que falten.

### 3. Carpeta de respaldo vacía / sin archivos previos

Cubierto sin cambios: si `load_local_cedulas_for_crossfill` devuelve vacío,
`crossfill_cedulas` no se ejecuta y `df` (snapshot del Sheet, ya acotado y
forward-fillado) se usa tal cual para todo el período acotado — equivalente
a "tratar la cédula de Sheets actual como si no hubiera sufrido cambios todo
el periodo". No aplica a la fuente `db`.

# Plan — Completar fechas faltantes en modo Excel desde el historial de revisiones (Drive)

> **IMPLEMENTADO — v0.6.5 (2026-07-09)**, sobre la base v0.6.4 (lineage + fusión).
> Deltas respecto a las decisiones originales, confirmados por Beto el 09/07/2026:
> - **(b)** los días descargados se guardan como `Cedula DDMMYYYY Completa.xlsx`
>   (instrucción explícita de Beto; el nombre "(Drive)" quedó descartado) — así la
>   fusión v0.6.4 les da rol de variante y un diario a mano posterior les gana.
> - Días anteriores a toda revisión: **NO se aproximan** con la revisión más vieja
>   (`approximate_older=False` en excel; la fuente sheets conserva la aproximación) —
>   quedan al forward-fill con advertencia, sin fabricar archivo.
> - Fix colateral: `_extract_cedula_vertical_for_date` ahora tolera encabezados de
>   fecha datetime de los XLSX de revisión (antes las revisiones intermedias
>   extraían 0 registros).
> - Hallazgo: Google consolida el historial en ~1 semana → el gap-filler es para
>   huecos recientes; meses viejos dependen del guardado manual.
> Detalle en `docs/cambios.md` (0.6.5) y `docs/cedula-fallbacks-y-respaldo.md`.

> Documento histórico del diseño. Decisiones originales abajo (las marcadas [x]
> se implementaron salvo los deltas anotados arriba).

## 0. Objetivo en una línea

En **modo `excel`**, cuando faltan cédulas diarias en la carpeta pero existen en el
historial de revisiones del Sheet (Drive API), descargar **solo las que faltan** para
completar el rango — **sin alterar** la lógica actual del modo excel.

## 1. Entendimiento (qué cambia y qué NO)

- **Los archivos físicos mandan al 100%.** La lógica de `load_daily_cedulas` NO se
  sustituye. Una fecha con archivo físico jamás se toca, sobrescribe ni "corrige" con Drive.
- **Único agregado:** para fechas **sin archivo físico** dentro del rango, intentar bajar
  ese día del historial de revisiones. Si Drive la tiene → se usa; si no → queda el
  comportamiento actual (forward-fill).
- **NO se adopta la semántica de modo `sheets`** (donde el Sheet vivo dicta la asignación
  de todas las fechas y lo local solo crossfillea operadores). Aquí lo físico es
  autoritativo y Drive es únicamente relleno de huecos.

## 2. Estado actual del modo Excel (flujo real)

`src/kpi_generator/domain/processor.py:268-271` (rama excel):
```python
self.log("Fuente cédulas: Excel local", code="SRC")
df = self.load_daily_cedulas(cedulas_folder)
return df, pd.DataFrame()
```
`src/kpi_generator/io/excel.py:110` (`load_daily_cedulas`):
1. Lee `*.xlsx`, parsea fecha con `parse_cedula_filename`.
2. **Si un solo nombre no parsea → `return None` (falla dura)** (`excel.py:136`).
3. Consolida los días físicos.
4. **`fill_missing_dates` hace forward-fill interno** (`excel.py:172`), y solo sobre el
   `[min, max]` de **los archivos físicos**, no del rango de viajes.

Hechos clave:
- El modo excel **hoy no conoce el rango de viajes** (a diferencia de `sheets`/`db`, que
  hacen `derive_date_range(trips_file)`).
- El forward-fill ocurre **dentro** de `load_daily_cedulas`; el relleno Drive debe
  insertarse **antes** de ese paso.

## 3. Piezas reutilizables que YA existen (no reinventar)

En `src/kpi_generator/io/sheets.py` el modo `sheets` ya hace "físico → Drive → ffill":
- `_list_revisions(sheet_id, creds)` — `sheets.py:47`
- `_revision_for_date(revisions, d)` — `sheets.py:76`
- `_fetch_revision_raw(...)` — `sheets.py:94`
- `_extract_cedula_vertical_for_date(all_rows, d)` — `sheets.py:259`
- Bucle agrupar-por-revisión + extraer + guardar — `sheets.py:596-644` (dentro de
  `load_cedulas_for_period`)

## 4. Diseño propuesto (mínimo, con lo físico intacto)

**4.1. Extraer helper reutilizable en `sheets.py`:**
```
fetch_dates_from_revisions(sheet_id, tab_name, dates, log, save_folder=None)
    -> dict[date, pd.DataFrame]
```
Encapsula: credenciales → conectar → `_list_revisions` → agrupar `dates` por revisión →
`_fetch_revision_raw` + `_extract_cedula_vertical_for_date` → renombrar `Unidad→Unidades`,
setear `Fecha Cedula/_dt`. **Best-effort**: sin credenciales / offline / Drive caído
devuelve `{}` sin lanzar. `load_cedulas_for_period` se refactoriza para usarlo también
(una sola fuente de verdad para la lógica Drive).

**4.2. Enganche en excel preservando `load_daily_cedulas`** (parámetros keyword-only con
default = comportamiento actual):
```
load_daily_cedulas(folder, log, *, fecha_min=None, fecha_max=None, gap_fetcher=None)
```
- Carga físicos (igual que hoy).
- Si `gap_fetcher` y rango presentes: `faltantes = [d in rango] - fechas_físicas`;
  `drive = gap_fetcher(faltantes)`; concatena **solo** los días que Drive sí trajo.
- **Después** corre `fill_missing_dates` para lo que ni físico ni Drive cubrieron.
- Con `gap_fetcher=None` → idéntico a hoy. Otros callers (`cli diff-cedulas`) no cambian.

**4.3. En el processor (rama excel):**
```python
fecha_min, fecha_max = derive_date_range(trips_file)          # NUEVO en excel
gap_fetcher = lambda faltantes: sheets_io.fetch_dates_from_revisions(
    cedulas_sheet_id or Config.CEDULA_SHEET_ID, tab, faltantes, self.log, save_folder=...)
df = self.load_daily_cedulas(cedulas_folder,
                             fecha_min=fecha_min, fecha_max=fecha_max,
                             gap_fetcher=gap_fetcher)
```
`io/excel.py` **no importa Drive**: recibe un callback; toda la red vive en `sheets.py`.
La lógica física queda intacta.

## 5. Implicaciones (con ejemplos concretos)

1. **Un día de Drive trae asignación pero NO operadores.** "Unidades Motriz" tiene
   Gerencia/Operación/Tipo/Circuito/Operando pero no Operador/No Operador/Estatus
   Operador/Observaciones (`units_extra`) — por eso en `sheets` esos campos vienen de lo
   local (changelog 0.6.1). Ej.: falta `Cedula 15062026.xlsx`; Drive baja la asignación
   correcta del 15/06 pero Operador queda vacío → cae a "Sin Info" (ver #2). Aun así es
   mejor que el forward-fill actual, que copiaba la asignación completa de otro día.
2. **`_apply_cedula_fallbacks` hace ffill/bfill de `units_extra` por unidad.** Un
   día-Drive con Operador vacío podría heredar el de un día físico vecino de la misma
   unidad (T317 con "Juan" el 14 y 16 físicos → el 15-Drive quedaría "Juan"). Decisión (d).
3. **Lo físico nunca se pisa.** Drive solo toca fechas con cero archivo físico. Cumple
   "obedece por completo las cédulas físicas".
4. **Revisión elegida = la más reciente ≤ fin del día D.** Si D es anterior a toda
   revisión, usa la más antigua como aproximación. Periodos muy viejos (fuera del
   historial) saldrían de la revisión más antigua, que puede no reflejar la asignación
   real de entonces. Caveat a documentar.
5. **Comodatos (aguas abajo).** `ComodatoManager` genera comodatos para días que la unidad
   está en cédula pero sin viajes. Más días-cédula → potencialmente **más comodatos**
   (p. ej. junio 29-30 que hoy no existen). Cambia números del reporte.
6. **Detector de cambios.** Más días de cédula → potencialmente más "cambios de
   asignación" en la Hoja Resumen de Cambios.
7. **Dependencia de red/credenciales (nueva en excel).** Hoy excel es 100% offline. Con
   esto, **cuando hay huecos**, intenta Drive (requiere `secrets/google_service_account.json`
   + internet). Sin creds / offline / Drive caído → degrada limpio a forward-fill, sin
   abortar.
8. **Huecos de borde (leading) sin cobertura Drive siguen sin rellenarse.**
   `fill_missing_dates` solo hace forward. Si el rango empieza el 1/06, el primer físico es
   el 3/06 y Drive no tiene revisión ≤ 2/06 → 1-2/06 quedan ausentes (igual que hoy).
   Ver decisión (e).
9. **Persistir lo descargado.** `sheets` guarda `Cedula DDMMYYYY Completa.xlsx`. Si en
   excel también guardamos: la carpeta se auto-cura, pero (a) escribe en tu carpeta,
   (b) el archivo tendría asignación sin operadores, (c) la próxima corrida lo cuenta como
   físico autoritativo (sin operadores). Ver decisión (b).
10. **Falla dura por nombre inválido persiste.** `load_daily_cedulas` sigue devolviendo
    `None` si un `*.xlsx` de la carpeta no parsea. El relleno Drive no lo cambia.
11. **Superficie GUI/CLI.** En excel hoy no se pide `sheet_id`/tab; usaría
    `Config.CEDULA_SHEET_ID` + tab por defecto ("Unidades Motriz"). Ver decisión (c).

## 6. Decisiones a confirmar (Beto marca aquí)

- **(a) ¿"Faltante" respecto a qué rango?**
  - [x] Rango de viajes `derive_date_range(zmov)` — *recomendado*
  - [ ] Solo huecos interiores del `[min,max]` de los físicos
  - Nota de Beto: __________________________________________
- **(b) ¿Guardar los días bajados en la carpeta?**
  - [x] Sí, best-effort, con nombre distinto (p. ej. `Cedula DDMMYYYY (Drive).xlsx`)
  - [ ] Sí, con el mismo nombre que usa `sheets` (`... Completa.xlsx`)
  - [ ] No guardar (solo en memoria)
  - Nota de Beto: __________________________________________
- **(c) ¿Opt-in o siempre?**
  - [x] Siempre best-effort (si hay creds+internet), con log `[REV]/[COV]`
  - [ ] Opt-in con checkbox/flag nuevo en GUI/CLI
  - Nota de Beto: __________________________________________
- **(d) Operador en día-Drive:**
  - [x] Dejarlo "Sin Info" explícito (trazable)
  - [ ] Permitir ffill/bfill desde días vecinos (continuidad)
  - Nota de Beto: __________________________________________
- **(e) ¿Extender forward-fill hasta `fecha_max`?**
  - [x] No cambiarlo ahora (comportamiento actual del fill)
  - [ ] Sí, rellenar hasta `fecha_max` aunque el último día sea anterior
  - Nota de Beto: __________________________________________

## 7. Pasos de implementación

1. `sheets.py`: extraer `fetch_dates_from_revisions(...)` desde `load_cedulas_for_period`;
   refactorizar este último para usarlo (sin cambiar su salida).
2. `io/excel.py`: `load_daily_cedulas(..., *, fecha_min, fecha_max, gap_fetcher)` —
   insertar relleno entre consolidación y `fill_missing_dates`. Default sin cambios.
3. `processor.py`: rama excel deriva rango, arma `gap_fetcher`, loguea `[RNG]/[REV]/[COV]`.
   Actualizar el wrapper `load_daily_cedulas` (`processor.py:99`).
4. Tests (`tests/unit/test_load_daily_cedulas.py`, ya existe):
   - Huecos rellenados por Drive mockeado.
   - Día físico nunca pisado por Drive.
   - Offline / sin-creds → degrada a forward-fill sin crash.
   - Esquema sin operadores → "Sin Info" (según decisión d).
5. Changelog + bump + reconstrucción del instalador — **solo tras aprobación**.

## 8. Verificación end-to-end

- Correr un mes con huecos reales (p. ej. junio con 2-3 días borrados de la carpeta) en
  `--cedulas-source excel`; confirmar en el log `[REV]/[COV]` que baja solo los faltantes,
  que los días físicos conservan su Operador, y que el Excel se genera con la cobertura
  esperada.
- `python -m pytest tests/unit -q` sin regresiones.

## 9. Alternativa de menor alcance (si prefieres validar antes)

Hacer solo pasos 1-2 (refactor + enganche) y validar con una corrida real de un mes con
huecos **antes** de tocar GUI/instalador. Bump e instalador quedan para después.

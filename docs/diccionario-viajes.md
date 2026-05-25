# Diccionario de datos — Hoja `Viajes`

> Versión: KPI Generator v0.4.2 · Generado contra `KPIs_Transport_20260525_*.xlsx`
> **74 columnas · ~18,000 filas/mes** (incluye viajes reales + comodatos sintéticos)

## Convenciones de naming

| Sufijo | Significado | Ejemplo |
|---|---|---|
| (sin sufijo) | Valor del registro original o del día del viaje | `Operación Cedula` = la del día del viaje |
| `Foto` | Snapshot de la **asignación vigente HOY** (último día de cédula). Permite filtrar viajes históricos por la asignación actual | `OpCedula Foto` |
| `OpCed` | Métrica agregada a nivel **Operación Cédula** durante todo el período, denormalizada a cada viaje | `Tendencia KM OpCed` |
| `Total` | Acumulado del período de análisis | `Objetivo KM Total` |
| `_date`, `_dt` | Versión datetime sin formato string | `Fecha creación_date` |

## Tipos de fila

| Tipo | `Número de Viaje` | `Centro` | Identificación |
|---|---|---|---|
| Viaje real (de SAP ZVPF) | < 2,000,000,000 | código numérico (3004, etc.) | Datos del ZVPF |
| Comodato sintético | ≥ 2,000,000,000 | `COMODATO` | Generado por `ComodatoManager` para días sin viaje cuando la unidad está en cédula |

---

## Bloque 1 — Crudas del viaje (SAP ZVPF, intactas)

| # | Columna | Tipo | Propósito | Origen | Notas |
|---|---|---|---|---|---|
| 1 | `Número de Viaje` | int64 | ID único del viaje | SAP ZVPF / `ComodatoManager` (≥2e9) | PK de la fila |
| 2 | `Fecha creación` | str `DD/MM/YYYY` | Fecha del viaje formateada | SAP / `ComodatoManager` | Para display y Looker |
| 3 | `Centro` | str | Centro logístico | SAP ZVPF / `COMODATO` | Filtro Looker |
| 4 | `Tipo De Operación` | str | Naturaleza del movimiento | SAP ZVPF / `COMODATO` | |
| 5 | `KMLiqCargadoFinal` | int64 | KM con carga liquidados | SAP ZVPF | 0 en comodatos |
| 6 | `KMLiqVacioFinal` | int64 | KM en vacío liquidados | SAP ZVPF | 0 en comodatos |
| 7 | `Ruta` | str | Código de ruta | SAP ZVPF / `COMODATO` | Filtro Looker |
| 8 | `Denominación` | str | Descripción del viaje | SAP ZVPF / `COMODATO` | |
| 9 | `Alias Origen` | str | Centro de origen | SAP ZVPF / `COMODATO` | Filtro geográfico |
| 10 | `Alias Destino` | str | Centro de destino | SAP ZVPF / `COMODATO` | Filtro geográfico |
| 11 | `ClaveCategoria` | str | Clasificación del viaje | SAP ZVPF / `COM` para comodatos | Filtro multi-nivel |
| 12 | `Distancia` | float64 | Distancia del viaje (km) | SAP ZVPF | 0 en comodatos |
| 13 | `StatusViaje` | str (`A`, `B`, `X`) | Estatus de cierre | SAP ZVPF / `X` para comodatos | |
| 14 | `Equipo Motriz` | str | Número económico del tractor | SAP / cédula | **Dimensión clave** |
| 15 | `Equipo Remolque 1` | str | Primer remolque | SAP ZVPF | 56% NaN (no todo viaje tiene 2 remolques) |
| 16 | `Equipo Dolly` | str | Dolly de enganche | SAP ZVPF | 90% NaN (solo configs full) |
| 17 | `Equipo Remolque 2` | str | Segundo remolque | SAP ZVPF | 90% NaN (solo configs full) |
| 18 | `Fecha creación_date` | datetime64 | Fecha sin formato (para cálculos) | derivada de `Fecha creación` | Para joins temporales |

## Bloque 2 — Métricas crudas calculadas por viaje

Calculadas en `DataProcessor.process_trips_optimized`.

| # | Columna | Tipo | Fórmula | Propósito Looker |
|---|---|---|---|---|
| 19 | `KM_cargado` | float64 | `KMLiqCargadoFinal` (copia limpia) | SUM agregable |
| 20 | `KM_vacio` | int64 | `KMLiqVacioFinal` | SUM agregable |
| 21 | `KM_total` | float64 | `KM_cargado + KM_vacio` | SUM agregable; **derivada — calculable en Looker** |
| 22 | `Diesel_LTS` | float64 | Cantidad Litros del archivo de combustible (merge por Número de Viaje) | SUM agregable |
| 23 | `Rendimiento` | float64 | `KM_total / Diesel_LTS` por viaje | **NO sumar** — usar AVG; 0 si Diesel = 0 |

## Bloque 3 — Categorización por cédula del día

Resultado del merge con `df_cedulas` filtrado por fecha del viaje. Define la **jerarquía operativa** del día del viaje.

| # | Columna | Tipo | Definición | Notas |
|---|---|---|---|---|
| 24 | `Gerencia` | str | Gerencia responsable ese día | `Pendiente` si phantom unit (sin cédula) |
| 25 | `Operación` | str | Operación logística (STB, SOR, etc.) | |
| 26 | `Tipo de Unidad` | str | SENCILLO, FULL, TORTHON, etc. | Inferido por prefijo si vacío |
| 27 | `Circuito` | str | Circuito/ruta operativa | `POR ASIGNAR` si no aplica |
| 28 | `Operando` | str | Estatus operativo (Operando, Taller, Gestoría, Sin Op, …) | Viene de `estatus_2` de la BD |
| 29 | `Operación Cedula` | str | **Clave operativa derivada**: `Operación + Circuito` o `Operación + Tipo Unidad` si circuito especial | **Dimensión principal de agregación** |

## Bloque 4 — Métricas auxiliares y de cálculo

Calculadas en `_add_trip_extra_columns`.

| # | Columna | Tipo | Fórmula | Notas Looker |
|---|---|---|---|---|
| 30 | `Viajes_count` | int64 (0/1) | `1` si viaje real, `0` si comodato | **SUM = total viajes reales** del slice |
| 31 | `Objetivo KM Viaje` | float64 | `Objetivo KM Diario / Eq x dia x op` por unidad | Para SUM por slice |
| 32 | `Objetivo Viajes Viaje` | float64 | `Objetivo Viajes Diario / Eq x dia x op` por unidad | Idem |
| 33 | `Complemento KM Objetivo` | float64 | KM objetivo proyectado en días restantes / n_rows de la unidad | Para SUM = tendencia restante |
| 34 | `Complemento Viajes Objetivo` | float64 | Idem para viajes | |
| 35 | `Objetivo KM Total` | float64 | `Objetivo KM Viaje + Complemento KM Objetivo` | **SUM = objetivo total del mes para el slice** |
| 36 | `Objetivo Viajes Total` | float64 | Idem para viajes | |
| 37 | `Eq x dia x op` | int64 | `COUNT DISTINCT(Equipo Motriz)` por (fecha, OpCédula) | Denominador del prorrateo de objetivos |
| 38 | `Promedio KM x Unidad dia` | float64 | `KM_total / Eq x dia x op` | Para AVG ponderado |
| 39 | `CedulaActual` | str | OpCédula vigente HOY (último día) para el equipo motriz | Filtro "ver histórico del equipo X según su asignación actual" |
| 40 | `Cuenta remolques` | float64 | **Remolques únicos por OpCédula, prorrateado entre viajes con remolque registrado** (v0.4.2) | **SUM filtrado por OpCédula = # remolques únicos**. Comodatos = 0 |
| 41 | `cuenta llaverem` | int64 | `nunique(R1+R2 concatenados)` por (fecha, OpCédula) | ⚠️ Mismo bug que tenía `Cuenta remolques` — `SUM` no da conteo único. Pendiente fix en próxima iteración |
| 42 | `Cuentaeqasig` | int64 | `nunique(Equipo Motriz)` por fecha (global) | Tamaño de la flota activa ese día |

## Bloque 5 — Snapshot "Foto" (asignación vigente HOY)

Calculados en `_add_trip_extra_columns` desde el último día de cédula. **Iguales para todas las filas de la misma unidad**, sin importar la fecha histórica del viaje.

Propósito Looker: filtrar "viajes históricos del Q1 de equipos que HOY pertenecen a Gerencia X" sin tener que mantener cédulas históricas como dimensión en Looker.

| # | Columna | Tipo | Definición |
|---|---|---|---|
| 43 | `Gerencia Foto` | str | Gerencia actual del equipo motriz |
| 44 | `Operación Foto` | str | Operación actual |
| 45 | `Tipo Unidad Foto` | str | Tipo de unidad actual |
| 46 | `Circuito Foto` | str | Circuito actual |
| 47 | `Operando Foto` | str | Estatus operativo actual |
| 48 | `OpCedula Foto` | str | OpCédula vigente HOY |
| 49 | `Eq en Cédula` | int64 (0/1) | `1` si el equipo motriz está en cédula HOY, `0` si es phantom | **SUM por slice = unidades activas** |

## Bloque 6 — Tendencias (proyección al cierre de mes)

Calculadas en `_add_tendencia_complement_to_trips`. Distribuyen el proyectado de días restantes equitativamente entre las filas de la unidad para que **SUM en Looker = total real + proyección**.

| # | Columna | Tipo | Fórmula | Notas |
|---|---|---|---|---|
| 50 | `Complemento Tendencia KM` | float64 | `proyección_KM_días_restantes / n_filas_unidad` | SUM agregable |
| 51 | `Tendencia KM Total` | float64 | `KM_total + Complemento Tendencia KM` | **SUM = KM esperado al cierre de mes** |
| 52 | `Complemento Tendencia Viajes` | float64 | Análogo para viajes | |
| 53 | `Tendencia Viajes Total` | float64 | `Viajes_count + Complemento Tendencia Viajes` | **SUM = viajes esperados al cierre** |

## Bloque 7 — KPIs de período denormalizados (nivel unidad)

Denormalizados en `_denormalize_kpis_to_trips` desde la hoja `Por Equipo`. **Repetidos en cada fila de la misma unidad** — en Looker usar `MAX()` para gráficas agregadas, no `SUM`.

| # | Columna | Tipo | Significado |
|---|---|---|---|
| 54 | `% Operativo` | int64 | % de días operando en el período (de la unidad) |
| 55 | `Tendencia KM` | float64 | Proyección KM al cierre (idéntico a `Tendencia KM Total` para la unidad) |
| 56 | `KM/h` | float64 | Velocidad promedio del período |
| 57 | `Densidad Viaje` | float64 | KM promedio por viaje |
| 58 | `Cump. KM periodo` | float64 | % cumplimiento KM del período (unidad) |
| 59 | `Cump. Viaje periodo` | float64 | % cumplimiento viajes del período (unidad) |

## Bloque 8 — KPIs OpCédula denormalizados

Denormalizados desde la hoja `Por Operación`. **Repetidos en todas las filas de la misma OpCédula** — usar `MAX()` o `MIN()` en Looker, NO `SUM`.

| # | Columna | Tipo | Significado | Looker |
|---|---|---|---|---|
| 60 | `Motrices Titulares` | int64 | Unidades asignadas hoy a la OpCédula | MAX por OpCédula |
| 61 | `Motrices Utilizadas` | int64 | Unidades distintas que hicieron al menos 1 viaje en el período | MAX |
| 62 | `KM/U Titular` | float64 | KM_total OpCédula / Motrices Titulares | MAX |
| 63 | `KM/U Real` | float64 | KM_total OpCédula / Motrices Utilizadas | MAX |
| 64 | `Tendencia KM OpCed` | float64 | Proyección KM al cierre para la OpCédula | MAX |
| 65 | `Tendencia KM/U OpCed` | float64 | Proyección KM/Unidad al cierre | MAX |
| 66 | `Tendencia Viajes OpCed` | float64 | Proyección viajes al cierre | MAX |
| 67 | `V/U` | float64 | Viajes / Unidad de la OpCédula | MAX |
| 68 | `Objetivo KM OpCed` | float64 | Meta mensual de KM de la OpCédula | MAX |
| 69 | `Objetivo Viajes OpCed` | int64 | Meta mensual de viajes de la OpCédula | MAX |
| 70 | `Objetivo KM/U` | float64 | Meta de KM por unidad | MAX |
| 71 | `Objetivo V/U` | float64 | Meta de viajes por unidad | MAX |
| 72 | `Cumplimiento KM % OpCed` | float64 | % avance de KM vs meta | MAX |
| 73 | `Cumplimiento Viajes % OpCed` | float64 | % avance de viajes vs meta | MAX |
| 74 | `Rendimiento OpCed` | float64 | KM/lt promedio de la OpCédula | MAX |

---

## Cómo agregar correctamente en Looker

| Patrón | Columnas | Agregación |
|---|---|---|
| Métricas crudas sumables | `KM_cargado`, `KM_vacio`, `KM_total`, `Diesel_LTS`, `Viajes_count`, `Distancia` | **SUM** |
| Objetivos y tendencias prorrateadas | `Objetivo KM Total`, `Tendencia KM Total`, `Cuenta remolques`, `Complemento *` | **SUM** (correcto por diseño) |
| KPIs ya agregados (período / OpCédula) | `% Operativo`, `Cumplimiento KM % OpCed`, `KM/U Titular`, `Motrices Titulares`, `Tendencia * OpCed` | **MAX** o **MIN** — NO SUM |
| Snapshots de asignación actual | `Gerencia Foto`, `OpCedula Foto`, `Eq en Cédula` | Filtro / dimensión / `SUM(Eq en Cédula)` |
| Ratios por viaje | `Rendimiento`, `KM/h`, `Densidad Viaje` | **AVG** — NO SUM |

## Limitaciones conocidas

- **`cuenta llaverem`** (col 41): tiene el bug que `Cuenta remolques` ya corrigió. `SUM` no da el conteo único correcto — usar `MAX` mientras se aplica el mismo patrón de prorrateo en una iteración futura.
- **Columnas derivadas calculables en Looker** (`KM_total`, `Rendimiento`, `Densidad Viaje`): se mantienen por simplicidad de consumo aunque sean redundantes. Pendiente auditoría Looker para decidir si eliminar.
- **Phantom units**: equipos motriz en viajes que NO están en cédula reciben `Gerencia = 'Pendiente'`, `Operación Cedula = 'POR ASIGNAR <Tipo>'`. Aparecen en el reporte pero su clasificación es aproximada (basada en `ClaveCategoria`).
- **Comodatos** (`Número de Viaje >= 2,000,000,000`): son filas sintéticas para días sin viaje con cédula registrada. KM = Viajes = 0. **NO sumar `Viajes_count` sobre comodatos** — están en 0 por diseño, pero el filtro `Centro != 'COMODATO'` los excluye explícitamente si Looker lo requiere.

## Mantenimiento

Cuando se agregue o renombre una columna en `Trip Data`, **actualizar este archivo** y bumpear la versión en el encabezado.

# Diccionario de datos — Hoja `Viajes`

> Versión: KPI Generator v0.4.2 · Generado contra `KPIs_Transport_20260525_*.xlsx`
> **74 columnas · ~18,000 filas/mes** (incluye viajes reales + comodatos sintéticos)
>
> 📖 **Para uso pretendido en visualizaciones**, ver [`uso-looker.md`](uso-looker.md)
> (catálogo del dashboard actual + 8 recetas propuestas + anti-patrones).

## Convenciones de naming

| Sufijo | Significado | Ejemplo |
|---|---|---|
| (sin sufijo) | Valor del registro original o del día del viaje | `Operación Cedula` = la del día del viaje |
| `Foto` | Snapshot de la **asignación vigente HOY** (último día de cédula). Permite filtrar viajes históricos por la asignación actual | `OpCedula Foto` |
| `OpCed` | Métrica agregada a nivel **Operación Cédula** durante todo el período, denormalizada a cada viaje | `Tendencia KM OpCed` |
| `Total` | Acumulado del período de análisis | `Objetivo KM Total` |
| `_date`, `_dt` | Versión datetime sin formato string | `Fecha creación_date` |

## Rol Looker (set cerrado)

Cada columna de las tablas siguientes tiene un campo **Rol Looker** que indica cómo se pretende usar en visualizaciones:

| Rol | Significado | Ejemplos |
|---|---|---|
| **Dim** | Dimensión categórica para agrupar/desglosar | `Gerencia`, `Operación Cedula`, `Equipo Motriz` |
| **Métrica** | Valor numérico agregable con SUM/MAX/AVG | `KM_total`, `Cuenta remolques`, `Cumplimiento KM % OpCed` |
| **Filtro** | Control de selección (slicer) | `Centro`, `Tipo De Operación`, `ClaveCategoria` |
| **Snapshot** | Reflejo de asignación HOY (no histórica); úsalo como dimensión para filtrar histórico | `Gerencia Foto`, `OpCedula Foto` |
| **Calculada** | Existe pero Looker puede recalcularla (`KM_total = KM_cargado + KM_vacio`) | `KM_total`, `Rendimiento`, `Densidad Viaje` |
| **Auxiliar** | No se consume directamente en visualizaciones; sirve a cálculos internos | `Fecha creación_date`, `CedulaActual`, `Eq x dia x op` |

## Tipos de fila

| Tipo | `Número de Viaje` | `Centro` | Identificación |
|---|---|---|---|
| Viaje real (de SAP ZVPF) | < 2,000,000,000 | código numérico (3004, etc.) | Datos del ZVPF |
| Comodato sintético | ≥ 2,000,000,000 | `COMODATO` | Generado por `ComodatoManager` para días sin viaje cuando la unidad está en cédula |

---

## Bloque 1 — Crudas del viaje (SAP ZVPF, intactas)

| # | Columna | Tipo | Rol Looker | Propósito | Notas |
|---|---|---|---|---|---|
| 1 | `Número de Viaje` | int64 | Dim | ID único del viaje (PK de la fila) | Distingue viajes reales (<2e9) de comodatos |
| 2 | `Fecha creación` | str `DD/MM/YYYY` | Dim | Fecha del viaje formateada | Para display; usa `Fecha creación_date` para time series |
| 3 | `Centro` | str | Filtro | Centro logístico | Filtrar `Centro != "COMODATO"` para excluir sintéticos |
| 4 | `Tipo De Operación` | str | Filtro | Naturaleza del movimiento | |
| 5 | `KMLiqCargadoFinal` | int64 | Métrica (SUM) | KM con carga liquidados | 0 en comodatos |
| 6 | `KMLiqVacioFinal` | int64 | Métrica (SUM) | KM en vacío liquidados | 0 en comodatos |
| 7 | `Ruta` | str | Filtro / Dim | Código de ruta | |
| 8 | `Denominación` | str | Dim | Descripción del viaje | Texto libre, mejor para tablas que charts |
| 9 | `Alias Origen` | str | Filtro / Dim | Centro de origen | Filtro geográfico |
| 10 | `Alias Destino` | str | Filtro / Dim | Centro de destino | Filtro geográfico |
| 11 | `ClaveCategoria` | str | Filtro / Dim | Clasificación del viaje | Multi-nivel; usado para phantom units |
| 12 | `Distancia` | float64 | Métrica (SUM) | Distancia del viaje (km) | 0 en comodatos |
| 13 | `StatusViaje` | str (`A`, `B`, `X`) | Filtro | Estatus de cierre | `X` para comodatos |
| 14 | `Equipo Motriz` | str | Dim | Número económico del tractor | **Dimensión clave** para drill-down |
| 15 | `Equipo Remolque 1` | str | Auxiliar | Primer remolque | 56% NaN; usar `Cuenta remolques` para conteos |
| 16 | `Equipo Dolly` | str | Auxiliar | Dolly de enganche | 90% NaN |
| 17 | `Equipo Remolque 2` | str | Auxiliar | Segundo remolque | 90% NaN |
| 18 | `Fecha creación_date` | datetime64 | Dim (time series) | Fecha sin formato | **Usar para gráficos de tendencia** (Looker reconoce como Date) |

## Bloque 2 — Métricas crudas calculadas por viaje

Calculadas en `DataProcessor.process_trips_optimized`.

| # | Columna | Tipo | Rol Looker | Fórmula | Notas |
|---|---|---|---|---|---|
| 19 | `KM_cargado` | float64 | Métrica (SUM) | Copia limpia de `KMLiqCargadoFinal` | Suma agregable |
| 20 | `KM_vacio` | int64 | Métrica (SUM) | `KMLiqVacioFinal` | Suma agregable |
| 21 | `KM_total` | float64 | Calculada | `KM_cargado + KM_vacio` | Disponible para `SUM` directa o calcular en Looker |
| 22 | `Diesel_LTS` | float64 | Métrica (SUM) | Cantidad Litros del archivo de combustible | Merge por Número de Viaje |
| 23 | `Rendimiento` | float64 | Calculada (AVG) | `KM_total / Diesel_LTS` por viaje | **NO SUM**; usa AVG ponderado en Looker |

## Bloque 3 — Categorización por cédula del día

Resultado del merge con `df_cedulas` filtrado por fecha del viaje. Define la **jerarquía operativa** del día del viaje.

| # | Columna | Tipo | Rol Looker | Definición | Notas |
|---|---|---|---|---|---|
| 24 | `Gerencia` | str | Dim | Gerencia responsable ese día | `Pendiente` si phantom unit |
| 25 | `Operación` | str | Dim | Operación logística (STB, SOR, …) | |
| 26 | `Tipo de Unidad` | str | Filtro / Dim | SENCILLO, FULL, TORTHON, etc. | Inferido por prefijo si vacío |
| 27 | `Circuito` | str | Filtro / Dim | Circuito/ruta operativa | `POR ASIGNAR` si no aplica |
| 28 | `Operando` | str | Filtro / Dim | Estatus operativo del día | Viene de `estatus_2` de la BD |
| 29 | `Operación Cedula` | str | Dim | **Clave operativa derivada** del día | **Dimensión principal de agregación** |

## Bloque 4 — Métricas auxiliares y de cálculo

Calculadas en `_add_trip_extra_columns`.

| # | Columna | Tipo | Rol Looker | Fórmula | Notas |
|---|---|---|---|---|---|
| 30 | `Viajes_count` | int64 (0/1) | Métrica (SUM) | `1` si viaje real, `0` si comodato | **SUM = total viajes reales** del slice |
| 31 | `Objetivo KM Viaje` | float64 | Métrica (SUM) | `Objetivo KM Diario / Eq x dia x op` | Para SUM por slice |
| 32 | `Objetivo Viajes Viaje` | float64 | Métrica (SUM) | Idem para viajes | |
| 33 | `Complemento KM Objetivo` | float64 | Métrica (SUM) | KM objetivo de días restantes / n_rows | |
| 34 | `Complemento Viajes Objetivo` | float64 | Métrica (SUM) | Idem para viajes | |
| 35 | `Objetivo KM Total` | float64 | Métrica (SUM) | `Objetivo KM Viaje + Complemento KM` | **SUM = objetivo total del mes** |
| 36 | `Objetivo Viajes Total` | float64 | Métrica (SUM) | Idem para viajes | |
| 37 | `Eq x dia x op` | int64 | Auxiliar | `COUNT DISTINCT(Equipo Motriz)` por (fecha, OpCédula) | Denominador interno; no usar como métrica |
| 38 | `Promedio KM x Unidad dia` | float64 | Métrica (AVG/SUM) | `KM_total / Eq x dia x op` | |
| 39 | `CedulaActual` | str | Auxiliar | OpCédula vigente HOY del equipo motriz | Usar `OpCedula Foto` (es lo mismo, mejor naming) |
| 40 | `Cuenta remolques` | float64 | Métrica (SUM) | Remolques únicos por OpCédula prorrateado | **SUM filtrado por OpCédula = # remolques únicos** (v0.4.2) |
| 41 | `cuenta llaverem` | int64 | Métrica (MAX) | `nunique(R1+R2)` por (fecha, OpCédula) | ⚠️ Bug pendiente: NO usar SUM (usar MAX hasta el fix) |
| 42 | `Cuentaeqasig` | int64 | Métrica (MAX) | `nunique(Equipo Motriz)` por fecha (global) | Tamaño de flota activa ese día |

## Bloque 5 — Snapshot "Foto" (asignación vigente HOY)

Calculados en `_add_trip_extra_columns` desde el último día de cédula. **Iguales para todas las filas de la misma unidad**, sin importar la fecha histórica del viaje.

Propósito: filtrar "viajes históricos del Q1 de equipos que HOY pertenecen a Gerencia X" sin mantener cédulas históricas como dimensión en Looker.

| # | Columna | Tipo | Rol Looker | Definición |
|---|---|---|---|---|
| 43 | `Gerencia Foto` | str | Snapshot / Filtro | Gerencia actual del equipo motriz |
| 44 | `Operación Foto` | str | Snapshot / Filtro | Operación actual |
| 45 | `Tipo Unidad Foto` | str | Snapshot / Filtro | Tipo de unidad actual |
| 46 | `Circuito Foto` | str | Snapshot / Filtro | Circuito actual |
| 47 | `Operando Foto` | str | Snapshot / Filtro | Estatus operativo actual |
| 48 | `OpCedula Foto` | str | Snapshot / Filtro | OpCédula vigente HOY |
| 49 | `Eq en Cédula` | int64 (0/1) | Métrica (SUM) | `1` si el equipo motriz está en cédula HOY | **SUM por slice = unidades activas hoy** |

## Bloque 6 — Tendencias (proyección al cierre de mes)

Calculadas en `_add_tendencia_complement_to_trips`. Distribuyen el proyectado de días restantes equitativamente entre las filas de la unidad para que **SUM en Looker = total real + proyección**.

| # | Columna | Tipo | Rol Looker | Fórmula | Notas |
|---|---|---|---|---|---|
| 50 | `Complemento Tendencia KM` | float64 | Métrica (SUM) | `proyección_KM_días_restantes / n_filas_unidad` | SUM agregable |
| 51 | `Tendencia KM Total` | float64 | Métrica (SUM) | `KM_total + Complemento Tendencia KM` | **SUM = KM esperado al cierre de mes** |
| 52 | `Complemento Tendencia Viajes` | float64 | Métrica (SUM) | Análogo para viajes | |
| 53 | `Tendencia Viajes Total` | float64 | Métrica (SUM) | `Viajes_count + Complemento Tendencia Viajes` | **SUM = viajes esperados al cierre** |

## Bloque 7 — KPIs de período denormalizados (nivel unidad)

Denormalizados en `_denormalize_kpis_to_trips` desde la hoja `Por Equipo`. **Repetidos en cada fila de la misma unidad** — en Looker usar `MAX()` para gráficas agregadas, no `SUM`.

| # | Columna | Tipo | Rol Looker | Significado |
|---|---|---|---|---|
| 54 | `% Operativo` | int64 | Métrica (MAX/AVG) | % de días operando en el período (de la unidad) |
| 55 | `Tendencia KM` | float64 | Métrica (MAX) | Proyección KM al cierre (idéntico a `Tendencia KM Total` para la unidad) |
| 56 | `KM/h` | float64 | Métrica (AVG) | Velocidad promedio del período |
| 57 | `Densidad Viaje` | float64 | Calculada (AVG) | KM promedio por viaje |
| 58 | `Cump. KM periodo` | float64 | Métrica (MAX) | % cumplimiento KM del período (unidad) |
| 59 | `Cump. Viaje periodo` | float64 | Métrica (MAX) | % cumplimiento viajes del período (unidad) |

## Bloque 8 — KPIs OpCédula denormalizados

Denormalizados desde la hoja `Por Operación`. **Repetidos en todas las filas de la misma OpCédula** — usar `MAX()` o `MIN()` en Looker, NO `SUM`.

| # | Columna | Tipo | Rol Looker | Significado |
|---|---|---|---|---|
| 60 | `Motrices Titulares` | int64 | Métrica (MAX) | Unidades asignadas hoy a la OpCédula |
| 61 | `Motrices Utilizadas` | int64 | Métrica (MAX) | Unidades distintas que hicieron al menos 1 viaje |
| 62 | `KM/U Titular` | float64 | Métrica (MAX) | `KM_total OpCédula / Motrices Titulares` |
| 63 | `KM/U Real` | float64 | Métrica (MAX) | `KM_total OpCédula / Motrices Utilizadas` |
| 64 | `Tendencia KM OpCed` | float64 | Métrica (MAX) | Proyección KM al cierre para la OpCédula |
| 65 | `Tendencia KM/U OpCed` | float64 | Métrica (MAX) | Proyección KM/Unidad al cierre |
| 66 | `Tendencia Viajes OpCed` | float64 | Métrica (MAX) | Proyección viajes al cierre |
| 67 | `V/U` | float64 | Métrica (MAX) | Viajes / Unidad de la OpCédula |
| 68 | `Objetivo KM OpCed` | float64 | Métrica (MAX) | Meta mensual de KM |
| 69 | `Objetivo Viajes OpCed` | int64 | Métrica (MAX) | Meta mensual de viajes |
| 70 | `Objetivo KM/U` | float64 | Métrica (MAX) | Meta de KM por unidad |
| 71 | `Objetivo V/U` | float64 | Métrica (MAX) | Meta de viajes por unidad |
| 72 | `Cumplimiento KM % OpCed` | float64 | Métrica (MAX) | % avance de KM vs meta |
| 73 | `Cumplimiento Viajes % OpCed` | float64 | Métrica (MAX) | % avance de viajes vs meta |
| 74 | `Rendimiento OpCed` | float64 | Métrica (MAX) | KM/lt promedio de la OpCédula |

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

Cuando se agregue o renombre una columna en `Trip Data`, **actualizar este archivo** (incluyendo el campo `Rol Looker`) y bumpear la versión en el encabezado. Si la columna abre o cierra una visualización, actualizar también [`uso-looker.md`](uso-looker.md).

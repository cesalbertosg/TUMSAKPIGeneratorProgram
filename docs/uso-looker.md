# Playbook de Looker Studio — Hoja `Viajes`

> Companion del [`diccionario-viajes.md`](diccionario-viajes.md).
> Documenta el dashboard actual + propone visualizaciones nuevas + lista anti-patrones.

## Convenciones

- **Hoja fuente**: nombre del tab de Google Sheets que alimenta la visualización (`Viajes`, `Resumen`, `Por Equipo`, `Por Operación`, `Promedio KM por Unidad`, `Objetivos`).
- **Rol Looker** de cada columna: `Dim` (dimensión), `Métrica` (numérico agregable), `Filtro` (control), `Snapshot` (asignación HOY), `Calculada` (derivada en Looker), `Auxiliar` (interno del pipeline).
- **Fórmulas calculadas**: se definen en Looker como "Campos calculados" en el editor de fuentes de datos.

---

## Sección A — Catálogo del dashboard actual

> 📝 **Template pendiente de poblar.** Compártete capturas o URL del Looker actual y completo esta sección con las visualizaciones existentes.

### Cómo poblar esta sección

Por cada visualización del dashboard, llenar una fila:

| Página | Visualización | Tipo Looker | Hoja fuente | Dimensiones | Métricas | Filtros | Observaciones |
|---|---|---|---|---|---|---|---|
| _(p. ej. "Resumen")_ | _(p. ej. "KM totales del mes")_ | Scorecard | Viajes | — | `SUM(KM_total)` | Centro != COMODATO | — |
| ... | ... | ... | ... | ... | ... | ... | ... |

### Inventario rápido (placeholder)

| # | Visualización | Estado documentación |
|---|---|---|
| 1 | _pendiente_ | ⏳ |
| 2 | _pendiente_ | ⏳ |

**Nota:** mientras tanto, usar la Sección B como referencia de buenas prácticas y comparar contra lo que hay hoy para identificar visualizaciones redundantes o que tengan agregaciones incorrectas.

---

## Sección B — Visualizaciones propuestas

8 recetas listas para construir, ordenadas de mayor a menor visibilidad ejecutiva. Cada receta incluye: tipo, hoja, dimensiones, métricas, filtros, fórmulas calculadas y limitaciones.

### B1. Scorecard ejecutivo TUMSA

Tiles de KPI principales para la primera vista del dashboard.

| Campo | Configuración |
|---|---|
| **Tipo** | Scorecard (1 por KPI, 6 tiles en fila) |
| **Hoja fuente** | `Resumen` |
| **Dimensiones** | _Ninguna_ (filtrar fila `TOTAL TUMSA`) |
| **Métricas** | `SUM(KM Total)`, `SUM(Viajes)`, `SUM(Unidades Activas)`, `SUM(Operando)`, `MAX(Cumplimiento KM %)`, `MAX(Cumplimiento Viajes %)` |
| **Filtros** | `Gerencia = "TOTAL TUMSA"` |
| **Fórmula opcional** | `Diferencia KM = SUM(KM Total) - SUM(Objetivo KM)` para mostrar variación |
| **Limitaciones** | Si no se filtra `TOTAL TUMSA`, los scorecards SUM duplican (gerencias + total). |

```
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│  KM TOTAL    │   VIAJES     │  UNIDADES    │  OPERANDO    │  CUMPL. KM   │ CUMPL. VIAJ. │
│  3.4M        │   10,345     │   582        │   450 (77%)  │   91.6%      │   90.7%      │
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

### B2. Ranking de cumplimiento por gerencia

| Campo | Configuración |
|---|---|
| **Tipo** | Bar chart horizontal ordenado descendente |
| **Hoja fuente** | `Resumen` |
| **Dimensiones** | `Gerencia` (excluir `TOTAL TUMSA` en filtro) |
| **Métricas** | `MAX(Cumplimiento KM %)`, `MAX(Cumplimiento Viajes %)` (doble barra agrupada) |
| **Filtros** | `Gerencia != "TOTAL TUMSA"`, `Unidades Activas > 0` |
| **Color condicional** | ≥100% verde, 80-99% ámbar, <80% rojo |
| **Limitaciones** | Gerencias administrativas (Escuela RH, Taller, Pendiente) aparecen con 0% — considerar excluirlas del ranking |

### B3. Time series KM vs Objetivo diario

| Campo | Configuración |
|---|---|
| **Tipo** | Line chart con dos series + área de objetivo |
| **Hoja fuente** | `Viajes` |
| **Dimensión X** | `Fecha creación` (granularidad: día) |
| **Métricas** | `SUM(KM_total)` (línea sólida), `SUM(Objetivo KM Viaje)` (línea punteada) |
| **Filtros** | Selector de `Gerencia Foto` (snapshot), selector de `Operación Cedula` |
| **Fórmula calculada** | `Variación = SUM(KM_total) - SUM(Objetivo KM Viaje)` |
| **Limitaciones** | Incluir comodatos NO afecta porque tienen KM=0; objetivos viven en filas de viajes reales |

### B4. Treemap KM por Operación Cédula

| Campo | Configuración |
|---|---|
| **Tipo** | Treemap (rectángulos proporcionales) |
| **Hoja fuente** | `Viajes` |
| **Dimensiones** | `Operación Cedula` |
| **Métrica área** | `SUM(KM_total)` |
| **Métrica color** | `MAX(Cumplimiento KM % OpCed)` (color por gradient verde-rojo) |
| **Filtros** | `Gerencia` (multi-select), `Centro != "COMODATO"` |
| **Limitaciones** | Filtrar comodatos para que el tamaño refleje viajes reales |

### B5. Tabla detallada por equipo motriz

| Campo | Configuración |
|---|---|
| **Tipo** | Tabla con paginación + búsqueda |
| **Hoja fuente** | `Por Equipo` (no `Viajes` — está pre-agregada por unidad) |
| **Dimensiones** | `Unidades`, `Gerencia`, `Operación Cedula`, `Tipo de Unidad` |
| **Métricas** | `KM Total`, `Viajes`, `Diesel LTS`, `Rendimiento`, `% Operativo`, `Cump. KM periodo`, `Tendencia KM` |
| **Filtros** | `Gerencia`, `Operación Cedula`, `Tipo de Unidad`, slider de `% Operativo` |
| **Ordenamiento default** | `Cump. KM periodo` descendente |
| **Limitaciones** | `Por Equipo` ya filtra a 1 fila por unidad-período; no requiere agregación adicional |

### B6. Heatmap estatus operativo por día

| Campo | Configuración |
|---|---|
| **Tipo** | Pivot table con formato condicional |
| **Hoja fuente** | `Viajes` (o ideal: `Cedulas Rellenadas` si se sube a Sheets) |
| **Dimensión X** | `Fecha creación` (día) |
| **Dimensión Y** | `Equipo Motriz` |
| **Métrica color** | `MAX(Operando)` mapeado a colores (Operando=verde, Taller=rojo, Gestoría=amarillo, Sin Op=gris) |
| **Filtros** | `Gerencia Foto` (snapshot), `Tipo Unidad Foto` |
| **Limitaciones** | Funciona mejor con ≤100 unidades visibles; usar paginación para flota completa |

### B7. Bullet chart cumplimiento por OpCédula

| Campo | Configuración |
|---|---|
| **Tipo** | Bullet chart (barra de objetivo + actual + tendencia) |
| **Hoja fuente** | `Por Operación` |
| **Dimensiones** | `Operación Cedula` |
| **Métricas** | `KM Total` (actual), `Objetivo KM` (target), `Tendencia KM` (proyección al cierre) |
| **Filtros** | `Gerencia` (multi-select) |
| **Color** | Verde si tendencia ≥ objetivo, rojo si < 80% del objetivo |
| **Limitaciones** | Asume que `Objetivo KM > 0`; OpCédulas administrativas pueden tener 0 y no aparecer |

### B8. Lista de unidades subutilizadas

Detecta equipos con cédula asignada que están aportando poco.

| Campo | Configuración |
|---|---|
| **Tipo** | Tabla con ordenamiento |
| **Hoja fuente** | `Por Equipo` |
| **Dimensiones** | `Unidades`, `Gerencia`, `Operación Cedula`, `Estatus` |
| **Métricas** | `KM Total`, `Viajes`, `% Operativo`, `Días Operando`, `Días Taller` |
| **Filtros fijos** | `% Operativo < 50`, `Días Periodo >= 7` (excluye unidades nuevas) |
| **Filtros interactivos** | `Gerencia` |
| **Ordenamiento** | `KM Total` ascendente (peor primero) |
| **Limitaciones** | El umbral de 50% es arbitrario; ajustar según política operativa |

---

## Sección C — Anti-patrones

Errores que Looker NO impide pero rompen los KPIs. Validar contra esta lista antes de aceptar un dashboard nuevo.

| ❌ Anti-patrón | Por qué está mal | ✅ Correcto |
|---|---|---|
| `SUM(% Operativo)` por equipo agrupado | `% Operativo` ya es un porcentaje pre-agregado (`Días Operando / Días Periodo`); sumarlo da valores >100% sin sentido | Usar `AVG(% Operativo)` o filtrar al nivel correcto y usar `MAX` |
| `SUM(cuenta llaverem)` | Bug conocido (v0.4.2): el campo se calcula por fila con `.transform('nunique')`, sumarlo da el conteo único multiplicado por las filas | Usar `MAX(cuenta llaverem)` filtrando por (fecha, OpCédula) hasta que se aplique el patrón de prorrateo |
| `AVG(Viajes_count)` incluyendo comodatos | Los comodatos tienen `Viajes_count=0` y diluyen el promedio | Filtrar `Centro != "COMODATO"` o usar `SUM` (que da el total real) |
| Mezclar `Operación Cedula` y `OpCedula Foto` en la misma agregación | Una es histórica (cambia por fila), la otra es snapshot HOY (constante por unidad); mezclarlas duplica filas o asigna mal categorías | Decidir el scope antes: histórico → `Operación Cedula`, "estado actual" → `OpCedula Foto` |
| `SUM(Cumplimiento KM % OpCed)` por OpCédula | Es un porcentaje ya calculado a nivel OpCédula y denormalizado a cada fila; sumarlo lo multiplica por el número de viajes | Usar `MAX` o `MIN` (todos los valores son iguales para misma OpCédula) |
| `SUM(KM_total)` incluyendo `Tendencia KM Total` | Ambas miden lo mismo pero `Tendencia` incluye proyección futura; sumarlos da el doble | Elegir uno según contexto: real vs proyectado al cierre |
| Filtrar por `Equipo Remolque 1` ignorando R2 | Un mismo remolque puede aparecer en R1 y R2 del mismo viaje; filtrar solo por R1 pierde datos | Usar `Cuenta remolques > 0` o filtrar contra ambas columnas |
| Agrupar por `Gerencia` sin excluir `TOTAL TUMSA` (en hoja Resumen) | Doble conteo: la fila TOTAL contiene la suma de las demás | `Gerencia != "TOTAL TUMSA"` en el filtro de la visualización |

---

## Recursos relacionados

- [`diccionario-viajes.md`](diccionario-viajes.md) — definición técnica de cada columna (74 campos)
- [`arquitectura.md`](arquitectura.md) — cómo se construye la hoja Viajes
- [`cambios.md`](cambios.md) — changelog (ver v0.4.2 para el fix de `Cuenta remolques`)

## Mantenimiento

- Cuando se agregue una columna nueva al pipeline, registrar su rol Looker en el diccionario + agregarla aquí si abre una visualización nueva
- Cuando Beto comparta capturas del dashboard, llenar Sección A
- Al detectar un anti-patrón nuevo en producción, sumarlo a Sección C

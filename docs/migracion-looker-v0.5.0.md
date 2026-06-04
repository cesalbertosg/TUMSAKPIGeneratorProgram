# Migracion Looker — v0.4.x → v0.5.0

**Aplica a**: dashboards Looker Studio que consumen el sheet `KPI KM Auto`
(`1sv8P004Ej85D_GF4YwEmoBO1XqWR1KYdGOSb1FJWM8Y`).

**Esfuerzo estimado**: 1-2 horas por dashboard activo.

**Cuando hacerla**: despues del primer push v0.5.0 a `main` (cuando la
corrida automatica del Task Scheduler ya escriba el nuevo schema). Hasta
entonces los dashboards viejos siguen viendo data v0.4.3.

---

## Resumen del cambio

| Hoja Sheets | Filas antes | Filas ahora | Cambio principal |
|---|---|---|---|
| `Por Equipo` | 1 por periodo de asignacion | 1 por equipo unico | Schema nuevo, motrices + arrastres juntos |
| `Por Operación` | 1 por OpCedula | 1 por OpCedula vigente | Columnas reorganizadas |
| `Resumen` | 1 por gerencia + TOTAL | 1 por gerencia + TOTAL | Columnas reorganizadas |
| `Promedio KM por Unidad` | 1 por OpCedula | 1 por OpCedula | Schema simplificado |
| `Viajes` | 1 por viaje + columnas KPI | 1 por viaje + columnas KPI | Renames de columnas denormalizadas |
| `Objetivos` | sin cambio | sin cambio | — |
| `Cambios` | sin cambio | sin cambio | — |

---

## Hoja `Por Equipo`

### Columnas eliminadas

| Columna vieja | Por que se elimina | Reemplazo |
|---|---|---|
| `Fecha Inicio`, `Fecha Fin` | Ya no hay periodos | — (cada equipo es 1 fila) |
| `Días Periodo` | Idem | `Dias Asignado + Dias Sin Asignacion` |
| `Fecha Ultima modif` | Duplicaba filename | — (usar el filename) |
| `Denominación del equipo` | Sustituido por valor canonico | `Tipo Equipo` (Motriz/Remolque/Dolly) |
| `Tipo de equipo` | Idem | `Tipo Equipo` |
| `Días Operando` con tilde | Naming canonico | `Dias Operando` sin tilde |
| `Días Disponible`, `Días Taller`, `Días Gestoría` | Naming canonico | `Dias Disponible`, `Dias Taller`, `Dias Gestoria` (sin tilde) |
| `KMLiqCargadoFinal`, `KMLiqVacioFinal` | Eran 0 hasta liquidacion | `KM Cargado`, `KM Vacio` (suman correctamente desde el primer dia) |
| `Cump. KM periodo`, `Cump. Viaje periodo` | Renombradas | `Cump KM %`, `Cump Viajes %` |
| `Obj KM Diario`, `Obj Viajes Diario` | Ya no se publican | — (el objetivo total ya esta prorrateado en `Objetivo KM Total`) |

### Columnas nuevas

| Columna | Que es |
|---|---|
| `Tipo Equipo` | `Motriz` \| `Remolque` \| `Dolly` |
| `Dias Asignado` | Dias con asignacion a una OpCedula valida |
| `Dias Sin Asignacion` | Dias `POR ASIGNAR` o sin cedula |
| `Dias Sin Operador`, `Dias Descanso`, `Dias Rescate`, `Dias Puesto A Punto` | Sub-status canonicos que no se exponian antes |
| `Dias Otros Status` | Resiliente: agrupa Activo/Baja/Inhabilitado/etc. y futuros desconocidos |
| `Dias Activo` | Dias con ≥1 viaje no comodato (transversal) |

### Columnas renombradas (mismo significado)

| Vieja | Nueva |
|---|---|
| `Unidades` | `Equipo Motriz` (incluye arrastres pese al nombre legacy) |
| `Operación cedula` | `Operacion Cedula` (sin tilde, C mayuscula) |
| `Operación` | `Operacion` |

### Pasos en Looker

1. Recurso → Administrar fuentes de datos → para la fuente apuntada a `Por Equipo`, click **Editar conexion** → **Reconectar**.
2. Looker detectara los renames como **campo nuevo + viejo desaparecido**.
3. Para cada campo en uso:
   - Sustituir `Unidades` por `Equipo Motriz` en tablas/dimensiones.
   - Sustituir `Operación cedula` por `Operacion Cedula`.
   - Sustituir `KMLiqCargadoFinal` + `KMLiqVacioFinal` por `KM Cargado` + `KM Vacio` (o usar `KM Total` directo).
   - Sustituir `Cump. KM periodo` por `Cump KM %`.
4. Para tablas o tarjetas que agregaran por `Unidades` (porque habia varias filas por equipo): **eliminar la agregacion** — ahora hay 1 fila por equipo y la suma es directa.
5. Si tenias filtros por `Tipo de equipo = EQUIPO MOTRIZ`, sustituir por `Tipo Equipo = Motriz`.

---

## Hoja `Por Operación`

### Cambios de columnas

| Vieja | Nueva | Comentario |
|---|---|---|
| `Operación Cedula` | `Operacion Cedula` | Sin tilde |
| `Operación` | `Operacion` | Sin tilde |
| `Sin Op` | `Sin Operador` | Nombre canonico |
| `Diesel` | `Diesel LTS` | Consistencia con Equipos |
| `KM/U Titular`, `KM/U Real` | — | Reemplazo: `Promedio KM dia unidad` |
| `V/U`, `Tendencia V/U`, `Tendencia KM/U` | — | Eliminadas (se pueden derivar dividiendo) |
| `Objetivo KM/U`, `Objetivo V/U` | — | Eliminadas |
| `Dias Operando`, `Dias Taller`, `Dias Gestoria`, `Dias Sin Op` | — | Reemplazadas por `Dias unidad asignados/activos` |
| `Motrices Utilizadas` | — | Eliminada (ahora `Motrices Titulares` es lo unico relevante al corte) |
| `Remolques`, `Dollys` | — | Reemplazadas por contadores en `Promedio KM por Unidad` |

### Columnas nuevas

- `Disponible`, `Sin Operador`, `Taller`, `Gestoria`, `Descanso`, `Rescate`, `Puesto A Punto`, `Otros Status`: COUNT de motrices titulares con esa Estatus vigente.
- `Dias unidad asignados`, `Dias unidad activos`: sumas sobre los titulares.
- `% Operativo`: `Dias unidad activos / (titulares × dias corrientes) × 100`.
- `Promedio KM dia unidad`, `Promedio Viajes dia unidad`: insumo para `Tendencia`.
- `Densidad Viaje`: `KM Total / Viajes`.

---

## Hoja `Resumen`

Mismas filas (1 por gerencia + TOTAL TUMSA), columnas reorganizadas.

### Cambios

| Vieja | Nueva | Comentario |
|---|---|---|
| `Sin Op` | `Sin Operador` | Naming canonico |
| `Gestoría` con tilde | `Gestoria` sin tilde | Naming canonico |
| — | `Disponible`, `Descanso`, `Rescate`, `Puesto A Punto`, `Otros Status` | Status que no se exponian |
| — | `Dias unidad asignados`, `Dias unidad activos`, `% Operativo` | Nuevos KPIs ponderados por gerencia |

---

## Hoja `Promedio KM por Unidad`

### Cambios

| Vieja | Nueva |
|---|---|
| `Operación Cedula` | `Operacion Cedula` |
| `Remolques Únicos` | `Remolques Unicos` (sin tilde) |
| `Promedio Diario KM/U` | **mismo nombre, mismo significado** |
| `Motrices` | **mismo nombre, mismo significado** |

---

## Hoja `Viajes`

### Columnas KPI denormalizadas (cambian de nombre)

| Vieja | Nueva |
|---|---|
| `Tendencia KM` (de equipo) | `Tendencia KM Equipo` |
| `Tendencia KM OpCed` | (igual) |
| `Cump. KM periodo`, `Cump. Viaje periodo` | `Cump KM Equipo %`, `Cump Viajes Equipo %` |
| `Cumplimiento KM % OpCed`, `Cumplimiento Viajes % OpCed` | `Cumplimiento KM OpCed %`, `Cumplimiento Viajes OpCed %` |
| `KM/h` | — (eliminada: el tiempo no esta en este reporte) |
| `Densidad Viaje` (de periodo) | `Densidad Viaje` (de equipo) — mismo nombre, recalculo |
| `Motrices Utilizadas`, `KM/U Titular`, `KM/U Real`, `Tendencia KM/U OpCed`, `V/U`, `Objetivo KM/U`, `Objetivo V/U`, `Rendimiento OpCed` | — (eliminadas) |

### Columnas nuevas en Viajes

- `Promedio KM dia unidad` (de la OpCedula)
- `Tendencia Viajes Equipo`, `Tendencia Viajes OpCed`

---

## Checklist por dashboard

Para cada reporte Looker que use estas hojas:

- [ ] Abrir el reporte y exportar la lista de campos en uso (Recurso → Administrar fuentes).
- [ ] Reconectar cada fuente afectada.
- [ ] Sustituir campos eliminados por su reemplazo segun las tablas.
- [ ] Eliminar agregaciones manuales en `Por Equipo` (ahora 1 fila por equipo).
- [ ] Cambiar filtros `Tipo de equipo = EQUIPO MOTRIZ` por `Tipo Equipo = Motriz`.
- [ ] Si tenias scorecards de `Operando`, `Taller`, `Gestoría`, ahora hay 9 status canonicos disponibles + `Otros Status`.
- [ ] Refrescar y comparar contra la corrida anterior — los cumplimientos van a subir por el fix KM_total/Distancia (esto es CORRECTO, ver `cambios.md`).
- [ ] Documentar el resultado en el README del dashboard si corresponde.

## Plan B: rollback temporal

Si un dashboard critico no puede migrarse a tiempo, se puede revertir el
sheet a la version v0.4.3 hasta cierre del dia laboral:

1. `git checkout v0.4.3` en el repo del KPI Generator.
2. Ejecutar `kpi-run run ...` con los argumentos habituales.
3. Esto sobreescribe el sheet con el schema viejo.
4. `git checkout main` para volver a v0.5.0.

Esta opcion es transitoria — el plan es migrar todos los dashboards a la
nueva semantica en una semana.

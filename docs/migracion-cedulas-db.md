# Migración de fuente de cédulas — Excel → PostgreSQL

## Resumen

El KPI Generator soporta tres fuentes para cédulas motrices, seleccionables vía `CEDULAS_SOURCE` (env) o `--cedulas-source` (CLI) o dropdown (GUI):

| Source | Origen | Cuándo usarla |
|---|---|---|
| `excel` | Carpeta local con `Cedula DDMMYYYY.xlsx` | Default histórico. Fallback si BD inaccesible |
| `sheets` | Google Sheets directamente | Solo legacy / debugging del Sheet de cédulas |
| `db` | PostgreSQL `172.17.1.4 / cedula_direccion.cedula_unidades` | **Objetivo de la migración** |

## Por qué Postgres

- Fuente única de verdad — el proyecto Cédula DG ya hace ETL desde el Google Sheet con 3 cortes diarios
- Elimina dependencia del filesystem (archivos sueltos) y de credenciales Google en el KPI Generator
- Permite consultas históricas con cualquier rango sin tener que mantener todos los Excel
- El rango temporal se deriva del archivo de viajes (`zmov.XLSX`), no de los nombres de archivo de cédulas

## Arquitectura

```
zmov.XLSX
   │  derive_date_range() lee solo columna "Fecha creación"
   ▼
[fecha_min, fecha_max]
   │
   ▼
load_cedulas_from_db(fecha_min, fecha_max)
   │
   │  SQL: CTE 'ultima_previa' (semilla anterior al rango)
   │       + CTE 'dentro_rango' (DISTINCT ON unidades, fecha::date)
   │
   ▼
DataFrame crudo (1 fila por unidad por día con registro real)
   │
   │  _build_daily_snapshot:
   │   - para cada (unidad, día) en rango_completo
   │   - si hay registro real ese día → use it (origen='real')
   │   - sino → último registro previo conocido (origen='forward_fill')
   │
   ▼
(df_cedulas, df_audit)
   │
   ▼
DataProcessor (pipeline intacto)
   │
   ▼
Excel + hoja "Cedulas Rellenadas" (cuando hubo forward-fill)
```

## Query SQL canónica

```sql
WITH ultima_previa AS (
  SELECT DISTINCT ON (unidades)
    unidades, gerencia, operacion, tipo_unidad, circuito,
    estatus_2,
    fecha::date AS fecha_dia,
    'previa'    AS origen
  FROM public.cedula_unidades
  WHERE fecha::date < %(fecha_min)s
  ORDER BY unidades, fecha::timestamp DESC
),
dentro_rango AS (
  SELECT DISTINCT ON (unidades, fecha::date)
    unidades, gerencia, operacion, tipo_unidad, circuito,
    estatus_2,
    fecha::date AS fecha_dia,
    'rango'     AS origen
  FROM public.cedula_unidades
  WHERE fecha::date BETWEEN %(fecha_min)s AND %(fecha_max)s
  ORDER BY unidades, fecha::date, fecha::timestamp DESC
)
SELECT * FROM ultima_previa
UNION ALL
SELECT * FROM dentro_rango
ORDER BY unidades, fecha_dia;
```

## Mapeo de columnas

| Excel (contrato del pipeline) | Postgres `cedula_unidades` | Notas |
|---|---|---|
| `Unidades` | `unidades` | trivial |
| `Gerencia` | `gerencia` | trivial (centinela `"Pendiente"` ya normalizado) |
| `Operación` | `operacion` | trivial (centinela `"POR ASIGNAR"`) |
| `Tipo de Unidad` | `tipo_unidad` | trivial (inferencia por prefijo ya hecha en BD) |
| `Circuito` | `circuito` | trivial (centinela `"POR ASIGNAR"`) |
| `Operando` | **`estatus_2`** | confirmado (no `estatus`) |
| `Fecha Cedula` (sintética) | `fecha::date` formateada `DD/MM/YYYY` | |
| `Fecha Cedula_dt` (sintética) | `fecha::date` → `pd.to_datetime` | |

## Forward-fill y auditoría

Si el rango de viajes excede la cobertura BD, o si hay días dentro del rango sin revisión en Drive, se replica el último estado conocido de cada unidad.

La hoja extra `Cedulas Rellenadas` del Excel final documenta cada par (unidad, día) con:

| Unidades | Fecha Cedula | Origen | Fecha Cedula Origen |
|---|---|---|---|
| T101 | 05/05/2026 | real | 05/05/2026 |
| T101 | 06/05/2026 | forward_fill | 05/05/2026 |
| T101 | 07/05/2026 | real | 07/05/2026 |

## Plan de migración (3 fases)

### Fase 1 — Lectura paralela (1-2 días)
- ✅ `io/postgres.py`, `io/date_range.py`, `io/cedulas_db.py` creados
- ✅ `Config.CEDULAS_SOURCE` agregado, default `excel`
- ✅ CLI `--cedulas-source` y subcomando `diff-cedulas`
- ✅ GUI con dropdown
- ⏳ Validar con VPN activa: `kpi-run diff-cedulas --from 2026-05-01 --to 2026-05-16 --excel-folder ...`

### Fase 2 — Validación (1-2 semanas)
- Correr `kpi-run run --cedulas-source db ...` contra los últimos 5 días procesados
- Comparar Excel salida hoja-a-hoja vs `source=excel` (test `tests/integration/test_pipeline_identidad.py`)
- 5 ejecuciones consecutivas con diff=0 ⇒ promover

### Fase 3 — Switch + cleanup
- Cambiar `CEDULAS_SOURCE=db` por default en `.env`
- Conservar `load_daily_cedulas` y `load_cedula_from_sheets` como fallback
- Documentar v0.2.0 en `docs/cambios.md`

## Configuración (`.env`)

```bash
CEDULAS_SOURCE=db

FALLBACK_ON_DB_ERROR=false
FALLBACK_CEDULAS_PATH=data-input/Cedulas

PG_CEDULA_HOST=172.17.1.4
PG_CEDULA_PORT=5432
PG_CEDULA_DB=cedula_direccion
PG_CEDULA_USER=<user>
PG_CEDULA_PASSWORD=<pwd>
PG_CEDULA_SCHEMA=public
PG_CEDULA_TABLE=cedula_unidades
PG_CONNECT_TIMEOUT=10
```

## Garantías de integridad

| Riesgo | Mitigación |
|---|---|
| Múltiples revisiones del mismo día | `DISTINCT ON` con `ORDER BY fecha DESC` |
| Días sin revisión dentro del rango | Forward-fill explícito |
| Cobertura BD insuficiente al inicio | CTE `ultima_previa` trae semilla anterior |
| Mapeo `Operando` ambiguo | Confirmado: `estatus_2` |
| Caída de VPN/BD | `FALLBACK_ON_DB_ERROR=true` activa fallback a Excel |
| Cambios futuros del esquema BD | Test `test_pipeline_identidad.py` corre como canary |

# Arquitectura

> Conversión pendiente desde `../Ejemplo Actual/Sistema_KPI_Generator_Documentacion_Tecnica.docx`.

## Visión general

El KPI Generator es un pipeline que consume cuatro fuentes Excel (viajes SAP, combustible, cédulas diarias y objetivos) y produce un reporte de KPIs de flota con 4 hojas, sincronizando además con Google Sheets.

## Capas

```
┌─────────────────────────────────────────────────────────┐
│  GUI Tkinter  (kpi_generator.gui)                        │
│  └─ KPIGeneratorGUI — orquesta la interacción del usuario│
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  CLI argparse (kpi_generator.cli)                        │
│  └─ Punto de entrada alterno para scheduler              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Domain (kpi_generator.domain)                           │
│  ├─ DataProcessor  — pipeline principal                  │
│  ├─ ChangeTracker  — ingresos/egresos/cambios            │
│  └─ ComodatoManager — días sin viajes                    │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Config (kpi_generator.config)                           │
│  └─ Variables sensibles desde .env + defaults            │
└─────────────────────────────────────────────────────────┘
```

## Flujo de datos

1. **Carga** — DataProcessor lee viajes (ZVPF), combustible, cédulas diarias y objetivos
2. **Mapeo** — `create_unit_mapping` consolida la asignación equipo → operación
3. **Comodatos** — ComodatoManager genera registros sintéticos para días con cédula pero sin viaje
4. **Cambios** — ChangeTracker detecta ingresos, egresos y cambios operacionales por unidad
5. **Cálculo** — `_add_metrics_optimized` calcula 32 métricas por operación-cédula
6. **Cumplimiento** — `_calculate_compliance_optimized` cruza vs objetivos mensuales
7. **Salida** — `save_results` genera Excel + push a Google Sheets

## Capa I/O: Postgres Cédula DG (v0.2.0)

A partir de v0.2.0 el sistema soporta cargar cédulas desde PostgreSQL como fuente alternativa:

```
src/kpi_generator/io/
├── postgres.py     Cliente psycopg2 + context manager get_connection()
├── date_range.py   Deriva [fecha_min, fecha_max] de zmov.XLSX (lee solo columna 'Fecha creación')
└── cedulas_db.py   Query SQL + forward-fill + mapeo al contrato Excel
```

Control de fuente vía `Config.CEDULAS_SOURCE` ∈ {db, excel, sheets}. El default actual es `excel` para preservar compatibilidad durante la migración (Fase 1 del plan). Ver [`migracion-cedulas-db.md`](migracion-cedulas-db.md) para el detalle completo.

## Notas para iteraciones futuras

- Los métodos `load_*` (especialmente carga de viajes y combustible) siguen dentro de `DataProcessor`. Migrar `process_trips_optimized` requeriría refactor profundo del pipeline.
- La GUI mezcla layout, theming y orquestación. Separar `theme.py` cuando se quiera cambiar paleta.
- Tests de integración requieren VPN + credenciales; los unitarios siguen pendientes.

---

**Fuente canónica del original:** `../Ejemplo Actual/Sistema_KPI_Generator_Documentacion_Tecnica.docx`

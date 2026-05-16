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

## Notas para iteraciones futuras

- Los métodos `load_*` viven hoy dentro de `DataProcessor`. Idealmente se extraen a `kpi_generator.io.excel` y `kpi_generator.io.sheets` en una segunda pasada.
- La GUI mezcla layout, theming y orquestación. Separar `theme.py` cuando se quiera cambiar paleta.
- Sin tests por ahora — la carpeta `tests/` está lista para crecer.

---

**Fuente canónica del original:** `../Ejemplo Actual/Sistema_KPI_Generator_Documentacion_Tecnica.docx`

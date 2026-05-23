# KPI Generator

Generador automatizado de KPIs de flota para **TUMSA** (Transportistas Unidos de Morelos, S.A. de C.V.).
Procesa viajes (SAP ZVPF), combustible, cédulas diarias de operación y objetivos mensuales; calcula 32 métricas por operación-cédula con detección de cambios de equipo; exporta a Excel y sincroniza con Google Sheets.

## Requisitos

- **Python 3.12+** (canónico en producción: `C:\Users\Data Analyst\AppData\Local\Programs\Python\Python314\python.exe`)
- Windows (la GUI Tkinter y la extracción SAP son Windows-only)
- Credenciales de Google service account con acceso a las Sheets destino

## Instalación

```powershell
# 1. Clonar (cuando esté en GitHub)
git clone https://github.com/tumsa/kpi-generator.git
cd kpi-generator

# 2. Crear venv e instalar en modo editable
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev,sap]

# 3. Configurar secretos
copy .env.example .env
# Editar .env con tus IDs de Sheets
# Colocar google_service_account.json en secrets/

# 4. Verificar
python -m kpi_generator --help
```

## Uso

### GUI (recomendado para uso interactivo)

```powershell
python -m kpi_generator
# o
.\scripts\run_gui.bat
```

### CLI (para scheduler / automatización)

```powershell
# Default desde v0.3.0: lee cédulas desde PostgreSQL automáticamente
python -m kpi_generator.cli run --trips zmov.XLSX --fuel zmva.XLSX --objectives "Objetivo.xlsx" --output Outputs

# Fallback manual a Excel si la BD está caída
python -m kpi_generator.cli run --cedulas-source excel --cedulas <carpeta> ...
```

## Fuente de cédulas

A partir de v0.3.0 las cédulas se cargan desde PostgreSQL (`172.17.1.4 / cedula_direccion`)
por default. El rango se deriva automáticamente del archivo de viajes (`zmov.XLSX`).

Configurable vía `CEDULAS_SOURCE` en `.env`:
- `db` (default) — PostgreSQL
- `excel` — carpeta local de archivos `Cedula DDMMYYYY.xlsx`
- `sheets` — Google Sheets directo (legacy)

Si la BD cae, activar `FALLBACK_ON_DB_ERROR=true` para que use Excel automáticamente,
o forzar `--cedulas-source excel` en la línea de comandos. Ver `docs/migracion-cedulas-db.md`.

### Extracción SAP (independiente)

```powershell
python -m kpi_generator.io.sap
```

## Estructura

```
KPI Generator Program/
├── src/kpi_generator/      Código fuente (paquete instalable)
│   ├── config.py           Configuración centralizada
│   ├── cli.py              Entry point CLI
│   ├── io/                 Carga Excel, Google Sheets, SAP
│   ├── domain/             Lógica de negocio (KPIs, comodatos, cambios)
│   ├── reports/            Escritura de reportes Excel
│   └── gui/                Interfaz Tkinter
├── secrets/                Credenciales (gitignored)
├── data-input/             Archivos Excel fuente (gitignored)
├── Outputs/                Reportes generados (gitignored)
├── docs/                   Documentación técnica
├── scripts/                Lanzadores .bat
├── tests/                  Suite de pruebas (a futuro)
└── _legacy/                Versiones antiguas del monolito (referencia)
```

## Fuentes de datos esperadas

| Archivo | Contenido | Origen |
|---|---|---|
| `Viajes_de_<mes>.xlsx` | Viajes SAP ZVPF | `extract_zvpf.py` o exportación manual |
| `Diesel_<mes>.XLSX` | Cargas de combustible | SAP manual |
| `Cedula DDMMYYYY.xlsx` | Asignación diaria de equipo | Operaciones |
| `Objetivo de KM <mes>.xlsx` | Metas mensuales KM/viajes | Dirección |

## Salidas

Archivo Excel `KPIs_Transport_YYYYMMDD_HHMMSS.xlsx` con 8 hojas (v0.4.0):

1. **Resumen** — vista ejecutiva: 1 fila por gerencia + TOTAL TUMSA (Looker scorecards)
2. **Por Equipo** — 41 métricas por unidad-período (drill-down)
3. **Viajes** — datos crudos + denormalización para multi-filtros Looker (74 cols)
4. **Resumen de Cambios** — ingresos / egresos / cambios operacionales por unidad
5. **Por Operación** — KPIs agregados por Operación Cédula (29 cols)
6. **Objetivos** — metas mensuales por operación cédula
7. **Promedio KM por Unidad** — benchmark de productividad
8. **Cedulas Rellenadas** — auditoría de forward-fill (solo cuando `CEDULAS_SOURCE=db`)

Adicionalmente sincroniza con Google Sheets configurado en `.env` (7 tabs, espejo de las hojas
1-7 del Excel).

## Documentación

- [`docs/arquitectura.md`](docs/arquitectura.md) — visión técnica del sistema
- [`docs/flujo-logico.md`](docs/flujo-logico.md) — flujo lógico KPI y objetivos
- [`docs/cambios.md`](docs/cambios.md) — changelog

## Contexto

Este proyecto vive en el ecosistema más amplio de TUMSA descrito en
`C:\Users\Data Analyst\Desktop\Alberto\ContextoMaestro\proyectos-activos\kpi-generator.md`.

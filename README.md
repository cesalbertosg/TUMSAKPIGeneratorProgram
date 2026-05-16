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
python -m kpi_generator.cli run --mes abril --anio 2026
# o
.\scripts\run_cli.bat
```

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

Archivo Excel `KPIs_Transport_YYYYMMDD_HHMMSS.xlsx` con 4 hojas:
1. **KPIs por equipo** — 32 métricas por unidad-período
2. **Viajes procesados** — datos limpios consolidados
3. **Cambios operacionales** — ingresos / egresos / movimientos de equipo
4. **Resumen por operación-cédula**

Adicionalmente sincroniza con las Google Sheets configuradas en `.env`.

## Documentación

- [`docs/arquitectura.md`](docs/arquitectura.md) — visión técnica del sistema
- [`docs/flujo-logico.md`](docs/flujo-logico.md) — flujo lógico KPI y objetivos
- [`docs/cambios.md`](docs/cambios.md) — changelog

## Contexto

Este proyecto vive en el ecosistema más amplio de TUMSA descrito en
`C:\Users\Data Analyst\Desktop\Alberto\ContextoMaestro\proyectos-activos\kpi-generator.md`.

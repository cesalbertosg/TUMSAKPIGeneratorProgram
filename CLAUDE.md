# CLAUDE.md — KPI Generator (TUMSA)

## Principio rector — NO NEGOCIABLE

**TODOS LOS CÁLCULOS SE ASIGNAN DIARIAMENTE.** (Beto, 2026-09-29)

La captura diaria de la cédula, los comodatos, los complementos y la hoja `Viajes` existen
para contar día por día: es su razón de ser. Cada unidad-día aporta sus KM, viajes, diesel,
días y objetivo a la Operación Cédula que tenía **ese día**, nunca a la vigente al corte.

- Lo único que se toma "al corte" son fotos explícitas de asignación vigente
  (`Motrices Titulares`, `Estatus`, asignación de `Por Equipo`) y la proyección a futuro
  (complemento de objetivo y potencial de tendencia sobre los días restantes del mes).
- Toda suma de `Por Operación` / `Resumen` debe cuadrar con `Por Equipo` y `Viajes`.
- **Motrices Titulares** = la foto: la última asignación de cada unidad.
  **Motrices Utilizadas** = todas las que alguna vez viajaron asignadas ahí, aunque ya no
  sean parte de esa operación. Los viajes se asignan uno por uno, día por día.
- No preguntar si un cálculo nuevo va "por día" o "por vigente": va por día.

Registrado también en `Desktop\Alberto\MD\ContextoMaestro\decisiones.md` y
`ContextoMaestro\proyectos-activos\kpi-generator.md`.

## Contexto

- Changelog al día: `docs/cambios.md` (el MD de ContextoMaestro puede ir atrasado).
- Semántica de hojas: `docs/v0.5.0-design.md`.

## Desarrollo

- Tests: `.\.venv\Scripts\python.exe -m pytest tests/unit -q`
- Corridas de prueba siempre con `kpi-run run ... --no-upload-sheets`: el Sheet de
  producción alimenta Looker.

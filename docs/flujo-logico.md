# Flujo lógico: objetivos y viajes

> Conversión pendiente desde `../Ejemplo Actual/Flujo_Logico_Objetivos_Viajes_Resumen.docx`.

## Reglas de operación-cédula

```
si circuito ∈ {DEDICADO, POR ASIGNAR, SPRINTER, TERCERO, VENTA}
    operacion_cedula = f"{operacion} {tipo_unidad}"
sino
    operacion_cedula = f"{operacion} {circuito}"
```

Definido en `Config.SPECIAL_CIRCUITS` y aplicado tanto en `ChangeTracker._get_operacion_cedula` como en `ComodatoManager._get_operacion_cedula_comodato`.

## Detección de ingresos / egresos

- **Ingreso**: la unidad aparece en cédula después de `fecha_min_global`
- **Egreso**: la unidad deja de aparecer antes de `fecha_max_global` (fecha de egreso = día siguiente a la última aparición)
- **Cambio operacional**: la `operacion_cedula` calculada cambia entre dos cédulas consecutivas

## Objetivos

Lectura desde `Objetivo de KM <mes>.xlsx`. Las columnas esperadas:

| Columna | Tipo | Notas |
|---|---|---|
| Gerencia | str | |
| Operación Cedula | str | Debe coincidir con la generada por `_get_operacion_cedula` |
| Objetivo KM | float | Mensual |
| Objetivo Viajes | int | Mensual |

El procesador deriva objetivos diarios dividiendo por días del mes calendario.

---

**Fuente canónica del original:** `../Ejemplo Actual/Flujo_Logico_Objetivos_Viajes_Resumen.docx`

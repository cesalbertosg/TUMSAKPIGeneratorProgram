# tests/

Suite de pruebas para el KPI Generator. **Vacía por ahora** — sembrada con esta estructura para evitar fricción cuando se quiera empezar a probar.

## Cómo agregar pruebas

```python
# tests/test_change_tracker.py
import pandas as pd
from kpi_generator.domain.change_tracker import ChangeTracker

def test_change_tracker_detecta_ingreso():
    tracker = ChangeTracker()
    # ... construye un df_cedulas de prueba ...
    result = tracker.track_operation_changes(df_cedulas)
    assert (result['Tipo Cambio'] == 'INGRESO').any()
```

## Ejecutar

```powershell
pip install -e .[dev]
pytest
```

## Prioridades sugeridas para los primeros tests

1. `ChangeTracker._detect_unit_changes` — lógica determinística, fácil de mockear
2. `ComodatoManager._get_operacion_cedula_comodato` — pura función de reglas
3. Carga de cédulas: regex de filename → parsing de fecha (`DataProcessor._parse_cedula_filename`)
4. Cálculo de objetivos diarios (mensual / días del mes)

"""Rastreador de cambios operacionales (ingresos, egresos, cambios de operación) por unidad."""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from kpi_generator.config import Config


class ChangeTracker:
    """Rastreador de cambios en operaciones de unidades."""

    def __init__(self, log_callback=print):
        self.log_func = log_callback

    def track_operation_changes(self, df_cedulas: pd.DataFrame, obj_mapping: Dict | None = None) -> pd.DataFrame:
        """Detectar cambios de operación cédula por unidad, incluyendo ingresos y egresos."""
        changes = []

        fecha_min_global = df_cedulas['Fecha Cedula_dt'].min()
        fecha_max_global = df_cedulas['Fecha Cedula_dt'].max()

        total_units = len(df_cedulas['Unidades'].unique())
        units_with_changes = 0
        ingresos = 0
        egresos = 0

        for unit in df_cedulas['Unidades'].unique():
            unit_data = df_cedulas[df_cedulas['Unidades'] == unit].sort_values('Fecha Cedula_dt')

            unit_changes = self._detect_unit_changes(unit_data, obj_mapping, fecha_min_global, fecha_max_global)

            if unit_changes:
                units_with_changes += 1
                for change in unit_changes:
                    if change.get('Tipo Cambio') == 'INGRESO':
                        ingresos += 1
                    elif change.get('Tipo Cambio') == 'EGRESO':
                        egresos += 1
                changes.extend(unit_changes)

        self.log_func(
            f"[CHG] Analizadas {total_units} unidades: {ingresos} ingresos, {egresos} egresos, "
            f"{len(changes) - ingresos - egresos} cambios operacionales"
        )

        if changes:
            self.log_func(f"[CHG] Total: {len(changes)} registros de cambios")
            return pd.DataFrame(changes)
        else:
            self.log_func("[CHG] Sin cambios detectados")
            return pd.DataFrame()

    def _detect_unit_changes(self, unit_data: pd.DataFrame, obj_mapping: Dict | None = None,
                             fecha_min_global: pd.Timestamp | None = None,
                             fecha_max_global: pd.Timestamp | None = None) -> List[Dict]:
        """Detectar cambios para una unidad específica: ingresos, egresos y cambios operacionales."""
        changes = []

        primera_fecha = unit_data['Fecha Cedula_dt'].min()
        ultima_fecha = unit_data['Fecha Cedula_dt'].max()

        first_row = unit_data.iloc[0]
        last_row = unit_data.iloc[-1]

        if fecha_min_global is not None and primera_fecha > fecha_min_global:
            primera_operacion = self._get_operacion_cedula(
                first_row['Operación'], first_row['Circuito'], first_row['Tipo de Unidad']
            )
            tipo_unidad = first_row['Tipo de Unidad']

            obj_km_ingreso = 0
            obj_viajes_ingreso = 0
            if obj_mapping and primera_operacion in obj_mapping:
                obj_km_ingreso = obj_mapping[primera_operacion].get('Objetivo KM Diario', 0)
                obj_viajes_ingreso = obj_mapping[primera_operacion].get('Objetivo Viajes Diario', 0)

            changes.append({
                'Equipo Motriz': str(first_row['Unidades']),
                'Fecha cambio': primera_fecha.strftime("%d/%m/%Y"),
                'Tipo Cambio': 'INGRESO',
                'Operacion inicial': f'POR ASIGNAR {tipo_unidad}',
                'Operacion final': primera_operacion,
                'Gerencia inicial': 'PENDIENTE',
                'Gerencia final': first_row['Gerencia'],
                'Objetivo diario inicial KM': 0,
                'Objetivo diario final KM': obj_km_ingreso,
                'Objetivo diario inicial Viajes': 0,
                'Objetivo diario final Viajes': obj_viajes_ingreso,
            })

        ud = unit_data.copy()
        op_up = ud['Operación'].str.upper()
        circ_up = ud['Circuito'].str.upper()
        tipo_up = ud['Tipo de Unidad'].str.upper()
        ud['_op_ced'] = np.where(circ_up.isin(Config.SPECIAL_CIRCUITS), op_up + ' ' + tipo_up, op_up + ' ' + circ_up)
        ud['_prev_op'] = ud['_op_ced'].shift()
        ud['_prev_ger'] = ud['Gerencia'].shift()

        changed = ud[ud['_prev_op'].notna() & (ud['_op_ced'] != ud['_prev_op'])]
        for _, row in changed.iterrows():
            prev_op = row['_prev_op']
            curr_op = row['_op_ced']
            prev_km = obj_mapping[prev_op].get('Objetivo KM Diario', 0) if obj_mapping and prev_op in obj_mapping else 0
            prev_v = obj_mapping[prev_op].get('Objetivo Viajes Diario', 0) if obj_mapping and prev_op in obj_mapping else 0
            curr_km = obj_mapping[curr_op].get('Objetivo KM Diario', 0) if obj_mapping and curr_op in obj_mapping else 0
            curr_v = obj_mapping[curr_op].get('Objetivo Viajes Diario', 0) if obj_mapping and curr_op in obj_mapping else 0
            changes.append({
                'Equipo Motriz': str(row['Unidades']),
                'Fecha cambio': row['Fecha Cedula_dt'].strftime("%d/%m/%Y"),
                'Tipo Cambio': 'OPERACIONAL',
                'Operacion inicial': prev_op,
                'Operacion final': curr_op,
                'Gerencia inicial': row['_prev_ger'],
                'Gerencia final': row['Gerencia'],
                'Objetivo diario inicial KM': prev_km,
                'Objetivo diario final KM': curr_km,
                'Objetivo diario inicial Viajes': prev_v,
                'Objetivo diario final Viajes': curr_v,
            })

        if fecha_max_global is not None and ultima_fecha < fecha_max_global:
            ultima_operacion = self._get_operacion_cedula(
                last_row['Operación'], last_row['Circuito'], last_row['Tipo de Unidad']
            )
            tipo_unidad = last_row['Tipo de Unidad']

            obj_km_egreso = 0
            obj_viajes_egreso = 0
            if obj_mapping and ultima_operacion in obj_mapping:
                obj_km_egreso = obj_mapping[ultima_operacion].get('Objetivo KM Diario', 0)
                obj_viajes_egreso = obj_mapping[ultima_operacion].get('Objetivo Viajes Diario', 0)

            fecha_egreso = ultima_fecha + pd.Timedelta(days=1)

            changes.append({
                'Equipo Motriz': str(last_row['Unidades']),
                'Fecha cambio': fecha_egreso.strftime("%d/%m/%Y"),
                'Tipo Cambio': 'EGRESO',
                'Operacion inicial': ultima_operacion,
                'Operacion final': f'POR ASIGNAR {tipo_unidad}',
                'Gerencia inicial': last_row['Gerencia'],
                'Gerencia final': 'PENDIENTE',
                'Objetivo diario inicial KM': obj_km_egreso,
                'Objetivo diario final KM': 0,
                'Objetivo diario inicial Viajes': obj_viajes_egreso,
                'Objetivo diario final Viajes': 0,
            })

        return changes

    def _get_operacion_cedula(self, operacion: str, circuito: str, tipo_unidad: str) -> str:
        """Generar cédula de operación según reglas de negocio."""
        circuito_upper = circuito.upper()
        operacion_upper = operacion.upper()
        tipo_unidad_upper = tipo_unidad.upper()

        if circuito_upper in Config.SPECIAL_CIRCUITS:
            return f"{operacion_upper} {tipo_unidad_upper}"
        return f"{operacion_upper} {circuito_upper}"

"""Gestor de registros comodato para días sin viajes."""

from __future__ import annotations

from typing import Dict

import pandas as pd


class ComodatoManager:
    """Gestor modular de registros comodato para días sin viajes."""

    def __init__(self, base_id=2000000000):
        self.comodato_id = base_id

    def _get_operacion_cedula_comodato(self, operacion: str, circuito: str, tipo_unidad: str) -> str:
        """Generar cédula de operación para comodatos según reglas de negocio."""
        circuito_upper = circuito.upper()
        operacion_upper = operacion.upper()
        tipo_unidad_upper = tipo_unidad.upper()

        special_circuits = {'DEDICADO', 'POR ASIGNAR', 'SPRINTER', 'TERCERO', 'VENTA'}

        if circuito_upper in special_circuits:
            return f"{operacion_upper} {tipo_unidad_upper}"
        return f"{operacion_upper} {circuito_upper}"

    def create_comodatos(self, df_trips: pd.DataFrame, df_cedulas: pd.DataFrame,
                         unit_mapping: Dict, log_func=print) -> pd.DataFrame:
        """Generar registros comodato para días sin viajes, respetando fechas de ingreso/egreso.

        IMPORTANTE: Solo genera comodatos para unidades que ESTÁN en cédulas.
        Las unidades fantasma (solo en viajes) NO generan comodatos.
        """
        comodatos = []
        units_in_cedula = df_cedulas['Unidades'].unique()

        for unit in units_in_cedula:
            unit_str = str(unit)
            if unit_str not in unit_mapping:
                continue

            if not unit_mapping[unit_str].get('En Cedula', True):
                continue

            unit_cedulas = df_cedulas[df_cedulas['Unidades'] == unit]
            unit_trips = df_trips[df_trips['Equipo Motriz'] == unit_str]

            primera_fecha_cedula = unit_cedulas['Fecha Cedula_dt'].min().date()
            ultima_fecha_cedula = unit_cedulas['Fecha Cedula_dt'].max().date()

            cedula_dates = set(unit_cedulas['Fecha Cedula_dt'].dt.date)
            trip_dates = set(unit_trips['Fecha creación_date']) if not unit_trips.empty else set()

            missing_dates = cedula_dates - trip_dates
            missing_dates = {
                fecha for fecha in missing_dates
                if primera_fecha_cedula <= fecha <= ultima_fecha_cedula
            }

            if missing_dates:
                info = unit_mapping[unit_str]
                for fecha_missing in missing_dates:
                    cedula_day = unit_cedulas[unit_cedulas['Fecha Cedula_dt'].dt.date == fecha_missing]
                    if not cedula_day.empty:
                        cedula_info = cedula_day.iloc[0]
                        operacion_cedula_dia = self._get_operacion_cedula_comodato(
                            cedula_info['Operación'],
                            cedula_info['Circuito'],
                            cedula_info['Tipo de Unidad'],
                        )

                        comodatos.append({
                            'Número de Viaje': self.comodato_id,
                            'Fecha creación': pd.Timestamp(fecha_missing),
                            'Fecha creación_date': fecha_missing,
                            'Centro': 'COMODATO',
                            'Tipo De Operación': 'COMODATO',
                            'Equipo Motriz': unit_str,
                            'StatusViaje': 'X',
                            'KMLiqCargadoFinal': 0,
                            'KMLiqVacioFinal': 0,
                            'Distancia': 0,
                            'KM_cargado': 0,
                            'KM_vacio': 0,
                            'KM_total': 0,
                            'Diesel_LTS': 0,
                            'Rendimiento': 0,
                            'Viajes_count': 0,
                            'Gerencia': cedula_info['Gerencia'],
                            'Operación': cedula_info['Operación'],
                            'Tipo de Unidad': cedula_info['Tipo de Unidad'],
                            'Circuito': cedula_info['Circuito'],
                            'Operando': cedula_info['Operando'],
                            'Operación cedula': operacion_cedula_dia,
                            'Ruta': 'COMODATO',
                            'Denominación': 'COMODATO',
                            'Alias Origen': 'COMODATO',
                            'Alias Destino': 'COMODATO',
                            'ClaveCategoria': 'COM',
                        })
                        self.comodato_id += 1

        if comodatos:
            log_func(f"[COM] {len(comodatos)} comodatos (solo unidades en cédula)")

        return pd.DataFrame(comodatos)

    def integrate_comodatos(self, df_trips: pd.DataFrame, comodatos: pd.DataFrame) -> pd.DataFrame:
        """Integrar comodatos manteniendo estructura original."""
        if comodatos.empty:
            return df_trips

        try:
            comodatos['Número de Viaje'] = comodatos['Número de Viaje'].astype('int64')
            df_trips['Número de Viaje'] = df_trips['Número de Viaje'].astype('int64')

            if 'Fecha creación' not in df_trips.columns:
                fecha_cols = [col for col in df_trips.columns if 'fecha' in col.lower() or 'creacion' in col.lower()]
                if fecha_cols:
                    df_trips['Fecha creación'] = df_trips[fecha_cols[0]]
                else:
                    raise ValueError("No se encontró columna de fecha en df_trips")

            df_trips['Fecha creación'] = pd.to_datetime(df_trips['Fecha creación'], errors='coerce')
            comodatos['Fecha creación'] = pd.to_datetime(comodatos['Fecha creación'], errors='coerce')

            combined = pd.concat([df_trips, comodatos], ignore_index=True)

            if 'Fecha creación' in combined.columns and 'Equipo Motriz' in combined.columns:
                combined = combined.sort_values(['Equipo Motriz', 'Fecha creación'], na_position='last').reset_index(drop=True)
            else:
                combined = combined.reset_index(drop=True)

            return combined

        except Exception as e:
            print(f"[ERROR] Integrate comodatos: {e}")
            print(f"[DEBUG] df_trips columns: {list(df_trips.columns)}")
            print(f"[DEBUG] comodatos columns: {list(comodatos.columns) if not comodatos.empty else 'Empty'}")
            return df_trips

"""Tests para `domain.period.PeriodContext`.

Cubre las tres variables temporales (`dias_mes`, `dias_corrientes`,
`dias_restantes`), edge cases de calendario (febrero bisiesto, ultimo dia
del mes), y la construccion desde `df_trips` (`from_trips`).
"""

from __future__ import annotations

import pandas as pd
import pytest

from kpi_generator.domain.period import PeriodContext


# ---------- Propiedades temporales ----------

def test_junio_corte_dia_2() -> None:
    """02/06/2026 -> 30 dias mes, 2 corrientes, 28 restantes."""
    ctx = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-06-02"))
    assert ctx.dias_mes == 30
    assert ctx.dias_corrientes == 2
    assert ctx.dias_restantes == 28


def test_febrero_no_bisiesto() -> None:
    """Febrero 2027 (no bisiesto) -> 28 dias."""
    ctx = PeriodContext(anio=2027, mes=2, fecha_ultimo_viaje=pd.Timestamp("2027-02-15"))
    assert ctx.dias_mes == 28
    assert ctx.dias_corrientes == 15
    assert ctx.dias_restantes == 13


def test_febrero_bisiesto() -> None:
    """Febrero 2028 (bisiesto) -> 29 dias."""
    ctx = PeriodContext(anio=2028, mes=2, fecha_ultimo_viaje=pd.Timestamp("2028-02-15"))
    assert ctx.dias_mes == 29
    assert ctx.dias_corrientes == 15
    assert ctx.dias_restantes == 14


def test_ultimo_dia_del_mes_restantes_cero() -> None:
    """Corte el ultimo dia => dias_restantes = 0."""
    ctx = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-06-30"))
    assert ctx.dias_corrientes == 30
    assert ctx.dias_restantes == 0


def test_primer_dia_del_mes_corrientes_uno() -> None:
    """Corte el dia 1 => dias_corrientes = 1, dias_restantes = dias_mes - 1."""
    ctx = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-06-01"))
    assert ctx.dias_corrientes == 1
    assert ctx.dias_restantes == 29


def test_corte_con_hora_solo_se_considera_el_dia() -> None:
    """fecha_ultimo_viaje con timestamp 09:15 => dias_corrientes usa solo el dia."""
    ctx = PeriodContext(anio=2026, mes=6,
                        fecha_ultimo_viaje=pd.Timestamp("2026-06-02 09:15:30"))
    assert ctx.dias_corrientes == 2


# ---------- Limites estructurales ----------

def test_mes_invalido() -> None:
    with pytest.raises(ValueError, match="mes debe estar en"):
        PeriodContext(anio=2026, mes=13, fecha_ultimo_viaje=pd.Timestamp("2026-06-01"))


def test_fecha_de_otro_mes() -> None:
    """Fecha fuera del mes declarado debe rechazarse."""
    with pytest.raises(ValueError, match="no pertenece al mes"):
        PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-07-01"))


# ---------- Fechas auxiliares ----------

def test_fechas_inicio_fin_mes() -> None:
    ctx = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-06-15"))
    assert ctx.fecha_inicio_mes == pd.Timestamp("2026-06-01")
    assert ctx.fecha_fin_mes == pd.Timestamp("2026-06-30")
    assert ctx.fecha_corte == pd.Timestamp("2026-06-15")


def test_rango_corriente() -> None:
    """Rango [dia 1, corte] inclusive, frecuencia diaria."""
    ctx = PeriodContext(anio=2026, mes=6, fecha_ultimo_viaje=pd.Timestamp("2026-06-05"))
    rango = ctx.rango_corriente()
    assert len(rango) == 5
    assert rango[0] == pd.Timestamp("2026-06-01")
    assert rango[-1] == pd.Timestamp("2026-06-05")


# ---------- from_trips ----------

def test_from_trips_basico() -> None:
    df = pd.DataFrame({
        "Fecha creación": pd.to_datetime([
            "2026-06-01 06:00", "2026-06-02 09:15", "2026-06-01 14:30"
        ])
    })
    ctx = PeriodContext.from_trips(df)
    assert ctx.anio == 2026
    assert ctx.mes == 6
    assert ctx.dias_corrientes == 2  # max = 02/06
    assert ctx.dias_restantes == 28


def test_from_trips_multiples_meses_falla() -> None:
    """Pre-condicion: un solo mes. Mezcla -> ValueError."""
    df = pd.DataFrame({
        "Fecha creación": pd.to_datetime(["2026-05-30", "2026-06-02"])
    })
    with pytest.raises(ValueError, match="varios meses"):
        PeriodContext.from_trips(df)


def test_from_trips_sin_fechas_falla() -> None:
    df = pd.DataFrame({"Fecha creación": [pd.NaT, pd.NaT]})
    with pytest.raises(ValueError, match="fechas validas"):
        PeriodContext.from_trips(df)


def test_from_trips_columna_ausente_falla() -> None:
    df = pd.DataFrame({"otra_col": [1, 2]})
    with pytest.raises(ValueError, match="falta columna"):
        PeriodContext.from_trips(df)


def test_from_trips_ignora_filas_con_fecha_nula() -> None:
    """Filas con NaT en fecha no rompen; el corte sale de las validas."""
    df = pd.DataFrame({
        "Fecha creación": [pd.Timestamp("2026-06-02"), pd.NaT, pd.Timestamp("2026-06-01")]
    })
    ctx = PeriodContext.from_trips(df)
    assert ctx.dias_corrientes == 2

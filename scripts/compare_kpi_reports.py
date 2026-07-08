# -*- coding: utf-8 -*-
"""Compara dos reportes KPI hoja contra hoja por clave de negocio.

Formalización del script de diagnóstico de junio 2026 (v0.6.4, plan W5).
Compara la hoja de viajes de dos outputs `KPIs_Transport_*.xlsx` por
`Número de Viaje` (default) y reporta filas solo-en-uno y discrepancias
por columna, con normalización NFKD (acentos/mayúsculas/espacios).

Uso:
    python scripts/compare_kpi_reports.py --ref <viejo.xlsx> --new <nuevo.xlsx>
        [--sheet-ref "Trip Data"] [--sheet-new Viajes]
        [--keys "Número de Viaje"]
        [--cols "Gerencia,Operación,Circuito,Operando"]

Exit code (estilo `kpi-run diff-cedulas`): 0 = sin diferencias, 2 = hay
diferencias, 1 = error de lectura.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata

import pandas as pd

DEFAULT_COLS = "Gerencia,Operación,Circuito,Operando"


def _norm(value) -> str:
    if pd.isna(value):
        return ""
    s = unicodedata.normalize("NFKD", str(value).strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()


def _load(path: str, sheet: str | None, keys: list[str], cols: list[str]) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    if sheet is None:
        sheet = next((s for s in ("Viajes", "Trip Data") if s in xl.sheet_names), None)
        if sheet is None:
            raise SystemExit(f"[ERR] {path}: sin hoja de viajes reconocible ({xl.sheet_names})")
    df = pd.read_excel(path, sheet_name=sheet, usecols=lambda c: c in set(keys + cols))
    faltan = [c for c in keys + cols if c not in df.columns]
    if faltan:
        raise SystemExit(f"[ERR] {path} hoja {sheet!r}: columnas faltantes {faltan}")
    df["_key"] = df[keys].astype(str).agg("|".join, axis=1)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", required=True, help="Reporte de referencia")
    parser.add_argument("--new", required=True, help="Reporte a validar")
    parser.add_argument("--sheet-ref", default=None, help="Hoja en --ref (default: Viajes o Trip Data)")
    parser.add_argument("--sheet-new", default=None, help="Hoja en --new (default: Viajes o Trip Data)")
    parser.add_argument("--keys", default="Número de Viaje",
                        help="Columnas clave separadas por coma (default: Número de Viaje)")
    parser.add_argument("--cols", default=DEFAULT_COLS,
                        help=f"Columnas a comparar separadas por coma (default: {DEFAULT_COLS})")
    parser.add_argument("--max-ejemplos", type=int, default=10)
    args = parser.parse_args(argv)

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    cols = [c.strip() for c in args.cols.split(",") if c.strip()]

    try:
        df_ref = _load(args.ref, args.sheet_ref, keys, cols)
        df_new = _load(args.new, args.sheet_new, keys, cols)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ERR] Lectura falló: {e}", file=sys.stderr)
        return 1

    print(f"[CMP] ref: {len(df_ref)} filas | new: {len(df_new)} filas")

    ref_keys, new_keys = set(df_ref["_key"]), set(df_new["_key"])
    solo_ref = ref_keys - new_keys
    solo_new = new_keys - ref_keys
    print(f"[CMP] claves comunes: {len(ref_keys & new_keys)} | solo ref: {len(solo_ref)} | solo new: {len(solo_new)}")
    for etiqueta, conjunto in (("solo ref", solo_ref), ("solo new", solo_new)):
        for k in sorted(conjunto)[: args.max_ejemplos]:
            print(f"    [{etiqueta}] {k}")

    # Duplicados de clave rompen la comparación 1:1 — repórtalos como diff.
    dup_ref = df_ref["_key"].duplicated().sum()
    dup_new = df_new["_key"].duplicated().sum()
    if dup_ref or dup_new:
        print(f"[!!] claves duplicadas — ref: {dup_ref}, new: {dup_new}")

    ref_idx = df_ref.drop_duplicates("_key").set_index("_key")
    new_idx = df_new.drop_duplicates("_key").set_index("_key")
    comunes = ref_idx.index.intersection(new_idx.index)

    total_diffs = 0
    for col in cols:
        a = ref_idx.loc[comunes, col].map(_norm)
        b = new_idx.loc[comunes, col].map(_norm)
        mask = a != b
        n = int(mask.sum())
        total_diffs += n
        marker = "OK" if n == 0 else "!!"
        print(f"  [{marker}] {col:20s}: {n} diferencias")
        if n:
            for key in comunes[mask][: args.max_ejemplos]:
                print(f"        {key}: ref={ref_idx.at[key, col]!r} vs new={new_idx.at[key, col]!r}")

    sin_diffs = (not solo_ref and not solo_new and total_diffs == 0
                 and not dup_ref and not dup_new)
    print(f"[CMP] RESULTADO: {'IDENTICOS (en claves/columnas comparadas)' if sin_diffs else 'HAY DIFERENCIAS'}")
    return 0 if sin_diffs else 2


if __name__ == "__main__":
    sys.exit(main())

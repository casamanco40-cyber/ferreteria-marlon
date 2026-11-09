import sqlite3
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))

TABLAS = [
    "productos",
    "ventas",
    "detalle_ventas",
    "compras_proveedores",
    "detalle_compras",
    "proveedores",
    "proformas",
    "detalle_proforma",
    "gastos"
]

os.makedirs("respaldo_supabase", exist_ok=True)

for sucursal, archivo in [
    ("admin", "ferreteria_admin.db"),
    ("tienda2", "ferreteria_tienda2.db")
]:
    ruta = os.path.join(BASE, archivo)
    conexion = sqlite3.connect(ruta)
    conexion.row_factory = sqlite3.Row

    datos = {}

    for tabla in TABLAS:
        filas = conexion.execute(
            f"SELECT * FROM {tabla}"
        ).fetchall()

        datos[tabla] = [dict(fila) for fila in filas]

        print(f"{sucursal} | {tabla}: {len(filas)} registros")

    destino = os.path.join(
        BASE, "respaldo_supabase",
        f"{sucursal}.json"
    )

    with open(destino, "w", encoding="utf-8") as archivo_json:
        json.dump(datos, archivo_json, ensure_ascii=False, indent=2)

    conexion.close()

print("\nEXPORTACIÓN COMPLETADA")
#!/usr/bin/env python3
"""
Limpia registros donde energia_kWh = 0 en InfluxDB.
Estrategia: exporta todos los datos, descarta los puntos malos,
borra la serie completa y reimporta los buenos.

Requiere variable INFLUX_HOST (opcional, default localhost).
"""
import os
from influxdb import InfluxDBClient

INFLUX_HOST = os.environ.get('INFLUX_HOST', 'localhost')
INFLUX_PORT = int(os.environ.get('INFLUX_PORT', '8086'))
INFLUX_DB   = os.environ.get('INFLUX_DB', 'tuya')

client = InfluxDBClient(host=INFLUX_HOST, port=INFLUX_PORT, database=INFLUX_DB)

# 1. Leer TODOS los registros
todos = list(client.query('SELECT * FROM "energia"', epoch='ns').get_points())
print(f"Total de registros: {len(todos)}")

# 2. Separar buenos y malos
buenos = [p for p in todos if p.get('energia_kWh', 1) != 0.0]
malos  = [p for p in todos if p.get('energia_kWh', 1) == 0.0]
print(f"  Buenos: {len(buenos)}  |  A eliminar: {len(malos)}")

if not malos:
    print("Nada que limpiar.")
    exit(0)

from datetime import datetime, timezone
for p in malos:
    ts_ns = p['time']
    ts_str = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    print(f"  Eliminando: {ts_str}  energia_kWh={p['energia_kWh']}")

# 3. Borrar toda la serie
client.drop_measurement('energia')
print("Serie 'energia' borrada.")

# 4. Reimportar solo los buenos
campos_excluir = {'time'}
puntos = []
for p in buenos:
    fields = {k: v for k, v in p.items()
              if k not in campos_excluir and v is not None}
    if fields:
        puntos.append({
            "measurement": "energia",
            "time": p['time'],
            "fields": fields,
        })

client.write_points(puntos, time_precision='n', batch_size=500)
print(f"Reimportados {len(puntos)} registros.")

# 5. Verificar
restantes = list(client.query('SELECT count("energia_kWh") FROM "energia" WHERE "energia_kWh" = 0').get_points())
print(f"Registros con energia_kWh=0 restantes: {restantes[0]['count'] if restantes else 0}")
print("Listo.")

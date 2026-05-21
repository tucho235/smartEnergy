#!/usr/bin/env python3
"""
Monitor de consumo energético - Dispositivo Tuya local
Escribe en InfluxDB para visualización en Grafana.

Configuración via variables de entorno (ver .env.example):
  TUYA_DEVICE_ID, TUYA_DEVICE_IP, TUYA_DEVICE_KEY
  INFLUX_HOST, INFLUX_PORT, INFLUX_DB
  LOG_CSV (opcional, vacío para deshabilitar)
"""

import tinytuya
import base64
import time
import csv
import os
from datetime import datetime
from influxdb import InfluxDBClient

# --- Configuración del dispositivo ---
DEVICE_ID  = os.environ['TUYA_DEVICE_ID']
DEVICE_IP  = os.environ['TUYA_DEVICE_IP']
DEVICE_KEY = os.environ['TUYA_DEVICE_KEY']
PROTOCOL   = float(os.environ.get('TUYA_PROTOCOL', '3.3'))

# Intervalo entre lecturas (segundos)
INTERVALO = int(os.environ.get('MONITOR_INTERVAL', '10'))

# InfluxDB
INFLUX_HOST = os.environ.get('INFLUX_HOST', 'localhost')
INFLUX_PORT = int(os.environ.get('INFLUX_PORT', '8086'))
INFLUX_DB   = os.environ.get('INFLUX_DB', 'tuya')

# Archivo de log CSV (vacío para deshabilitar)
LOG_CSV = os.environ.get('LOG_CSV', '/home/pi/consumo.csv') or None


def conectar_tuya():
    d = tinytuya.OutletDevice(
        dev_id=DEVICE_ID,
        address=DEVICE_IP,
        local_key=DEVICE_KEY,
        version=PROTOCOL,
    )
    d.set_socketTimeout(5)
    return d


def conectar_influx():
    client = InfluxDBClient(host=INFLUX_HOST, port=INFLUX_PORT, database=INFLUX_DB)
    client.create_database(INFLUX_DB)
    return client


def parsear_dps6(raw_b64):
    """DPS 6: paquete binario con mediciones instantáneas.
    Formato: 2 bytes voltaje + 3 bytes corriente + 3 bytes potencia + 2 bytes extra
    """
    b = base64.b64decode(raw_b64)
    voltaje   = int.from_bytes(b[0:2], 'big') / 10.0    # V
    corriente = int.from_bytes(b[2:5], 'big') / 1000.0  # A
    potencia  = int.from_bytes(b[5:8], 'big')            # W
    return voltaje, corriente, potencia


def leer_dps(device):
    dps = {}
    base = device.status()
    if base and 'dps' in base:
        dps.update(base['dps'])
    upd = device.updatedps()
    if upd and 'dps' in upd:
        dps.update(upd['dps'])
    return dps if dps else None


def escribir_influx(client, voltaje, corriente, potencia, energia_kwh, alarmas):
    aparente = voltaje * corriente
    fp = min(potencia / aparente, 1.0) if aparente > 0 else 0
    fields = {
        "voltaje_V":             round(voltaje, 1),
        "corriente_A":           round(corriente, 3),
        "potencia_W":            float(potencia),
        "potencia_VA":           round(aparente, 2),
        "factor_potencia":       round(fp, 3),
        "alarma_sobrevoltaje":   int(alarmas['sobrevoltaje']),
        "alarma_sobrecorriente": int(alarmas['sobrecorriente']),
    }
    # solo escribir energia_kWh si el dato llegó (evita enviar 0 por falla parcial)
    if energia_kwh is not None:
        fields["energia_kWh"] = round(energia_kwh, 2)
    client.write_points([{"measurement": "energia", "fields": fields}])


def init_csv():
    if LOG_CSV and not os.path.exists(LOG_CSV):
        with open(LOG_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'voltaje_V', 'corriente_A',
                'potencia_W', 'potencia_VA', 'factor_potencia',
                'energia_kWh', 'alarma_sobrevoltaje', 'alarma_sobrecorriente'
            ])


def guardar_csv(ts, voltaje, corriente, potencia, energia_kwh, alarmas):
    if not LOG_CSV:
        return
    aparente = voltaje * corriente
    fp = min(potencia / aparente, 1.0) if aparente > 0 else 0
    with open(LOG_CSV, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            ts, voltaje, round(corriente, 3),
            potencia, round(aparente, 1), round(fp, 3),
            energia_kwh if energia_kwh is not None else '',
            alarmas['sobrevoltaje'], alarmas['sobrecorriente']
        ])


def imprimir(ts, voltaje, corriente, potencia, energia_kwh, alarmas):
    aparente = voltaje * corriente
    fp = min(potencia / aparente, 1.0) if aparente > 0 else 0
    print(f"\n{'─'*45}")
    print(f"  {ts}")
    print(f"{'─'*45}")
    print(f"  {'Voltaje':<22}: {voltaje:.1f} V")
    print(f"  {'Corriente':<22}: {corriente:.3f} A")
    print(f"  {'Potencia activa':<22}: {potencia:.0f} W")
    print(f"  {'Potencia aparente':<22}: {aparente:.1f} VA")
    print(f"  {'Factor de potencia':<22}: {fp:.3f}")
    ekwh_str = f"{energia_kwh:.2f} kWh" if energia_kwh is not None else "N/D"
    print(f"  {'Energia acumulada':<22}: {ekwh_str}")
    print(f"  {'Alarma sobrevoltaje':<22}: {'SI' if alarmas['sobrevoltaje'] else 'NO'}")
    print(f"  {'Alarma sobrecorriente':<22}: {'SI' if alarmas['sobrecorriente'] else 'NO'}")


def main():
    print(f"Conectando a dispositivo Tuya en {DEVICE_IP}...")
    tuya = conectar_tuya()

    print(f"Conectando a InfluxDB en {INFLUX_HOST}:{INFLUX_PORT}...")
    influx = None
    for intento in range(10):
        try:
            influx = conectar_influx()
            print("InfluxDB conectado.")
            break
        except Exception as e:
            print(f"[WARN] InfluxDB no disponible aun (intento {intento+1}/10): {e}")
            time.sleep(6)
    if influx is None:
        print("[ERROR] No se pudo conectar a InfluxDB. Continuando solo con CSV.")

    init_csv()
    if LOG_CSV:
        print(f"Log CSV: {LOG_CSV}")

    print(f"Leyendo cada {INTERVALO}s. Ctrl+C para detener.\n")

    errores_tuya = 0

    while True:
        try:
            dps = leer_dps(tuya)

            if dps is None or '6' not in dps:
                errores_tuya += 1
                print(f"[WARN] Sin datos del dispositivo (intento {errores_tuya})")
                if errores_tuya >= 3:
                    print("[INFO] Reconectando a Tuya...")
                    tuya = conectar_tuya()
                    errores_tuya = 0
            else:
                errores_tuya = 0
                voltaje, corriente, potencia = parsear_dps6(dps['6'])
                energia_kwh = (dps['1'] / 100.0) if '1' in dps else None
                alarmas = {
                    'sobrevoltaje':   dps.get('105', False),
                    'sobrecorriente': dps.get('106', False),
                }
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                imprimir(ts, voltaje, corriente, potencia, energia_kwh, alarmas)
                guardar_csv(ts, voltaje, corriente, potencia, energia_kwh, alarmas)

                if influx:
                    try:
                        escribir_influx(influx, voltaje, corriente, potencia, energia_kwh, alarmas)
                    except Exception as e:
                        print(f"[WARN] Error escribiendo en InfluxDB: {e}")
                        try:
                            influx = conectar_influx()
                        except Exception:
                            pass

        except KeyboardInterrupt:
            print("\nDetenido por el usuario.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            errores_tuya += 1

        time.sleep(INTERVALO)


if __name__ == '__main__':
    main()

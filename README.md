# smartEnergy — Monitor de consumo eléctrico con Tuya + InfluxDB + Grafana

Sistema de monitoreo de consumo eléctrico en tiempo real usando un breaker inteligente Tuya,
corriendo en una Raspberry Pi.

## Stack

| Componente | Detalle |
|---|---|
| Dispositivo | Breaker Tuya FSE-F723C3D40A0F92 (protocolo local 3.3) |
| Script monitor | Python + tinytuya, lectura cada 10 s |
| Base de datos | InfluxDB 1.8 (Docker, puerto 8086) |
| Dashboard | Grafana 13 (Docker, puerto 3001) |
| Servicio | systemd (`tuya-monitor.service`), arranque automático |

## Paso a paso: configuración inicial

### 1. Obtener credenciales del dispositivo Tuya

Las credenciales del dispositivo se obtienen desde el **Tuya IoT Platform**:

1. Crear cuenta en [iot.tuya.com](https://iot.tuya.com)
2. Crear un proyecto Cloud (tipo "Smart Home")
3. En la app **SmartLife** vincular el dispositivo a tu cuenta Tuya
4. En el portal IoT → **Cloud → Devices**, buscar el dispositivo y anotar:
   - `Device ID` (ej: `eb997d30c011736031xxxxxx`)
   - `Local Key` (clave de cifrado local, 16 caracteres)
5. La IP del dispositivo se puede obtener desde el router o con `tuya-cli wizard`

> **Verificación rápida con tuya-cli:**
> ```bash
> tuya-cli get --id <DEVICE_ID> --key <LOCAL_KEY> --ip <IP> --protocol-version 3.3
> ```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
nano .env   # completar con los valores reales
```

Para el servicio systemd, crear también `/home/pi/.env.tuya` con el mismo contenido
(el servicio lo carga con `EnvironmentFile`).

### 3. Levantar la infraestructura Docker

```bash
docker compose up -d
```

Verifica que ambos contenedores estén corriendo:
```bash
docker ps
```

Acceder a Grafana en `http://<IP_PI>:3001` (usuario `admin`, contraseña definida en `.env`).

### 4. Instalar dependencias Python

```bash
python3 -m venv ~/venv_tuya
source ~/venv_tuya/bin/activate
pip install tinytuya influxdb
```

### 5. Probar el monitor manualmente

```bash
source ~/venv_tuya/bin/activate
# cargar las variables de entorno
export $(cat .env | grep -v '^#' | xargs)
python3 monitor_tuya.py
```

Debería mostrar lecturas como:
```
─────────────────────────────────────────────
  2024-01-15 10:30:05
─────────────────────────────────────────────
  Voltaje               : 228.3 V
  Corriente             : 1.720 A
  Potencia activa       : 388 W
  Potencia aparente     : 392.7 VA
  Factor de potencia    : 0.988
  Energia acumulada     : 142.50 kWh
  Alarma sobrevoltaje   : NO
  Alarma sobrecorriente : NO
```

### 6. Activar el servicio systemd

```bash
# Copiar el archivo de servicio
sudo cp systemd/tuya-monitor.service /etc/systemd/system/

# Activar y arrancar
sudo systemctl daemon-reload
sudo systemctl enable tuya-monitor.service
sudo systemctl start tuya-monitor.service

# Verificar estado
sudo systemctl status tuya-monitor.service
journalctl -u tuya-monitor.service -f
```

### 7. Crear el dashboard en Grafana

```bash
export $(cat .env | grep -v '^#' | xargs)
python3 create_dashboard.py
```

El dashboard `Monitor Energia Tuya` aparecerá en Grafana con:
- Paneles de stats en tiempo real (voltaje, corriente, potencia, FP, energía)
- Series temporales de potencia, voltaje, corriente
- Energía acumulada + consumo en el período seleccionado
- Tasa de consumo (kWh/h)
- Semáforo de factor de potencia + alarmas del dispositivo

#### Ejemplo
<img width="1080" height="2400" alt="IMG_20260521_113003" src="https://github.com/user-attachments/assets/591e6884-c304-40b1-97b9-7ef72df549a6" />

#### Acceder al dashboard

Una vez que la Raspberry Pi está corriendo, el dashboard se abre desde cualquier dispositivo en la misma red local:

```
http://192.168.XX.XX:3001
```

Reemplazá `XX.XX` con la IP local de tu Raspberry Pi (la podés ver con `hostname -I` o desde el router).
Credenciales: usuario `admin`, contraseña definida en `.env`.

> El dashboard se refresca automáticamente cada 10 segundos.

### 8. Crear las alertas

```bash
export $(cat .env | grep -v '^#' | xargs)
python3 create_alerts.py
```

Crea 4 reglas en la carpeta "Tuya Alertas":

| Alerta | Condición | Pendiente |
|---|---|---|
| FP Bajo — Warning | FP < 0.70 con carga > 150 W | 5 min |
| FP Bajo — Critical | FP < 0.60 con carga > 150 W | 2 min |
| Alarma Sobrevoltaje | Breaker activa alarma >250V | 30 s |
| Alarma Sobrecorriente | Breaker activa alarma >40A | 30 s |

> **Pendiente:** Para recibir notificaciones (Telegram, email, etc.) configurar un
> **Contact Point** en Grafana → Alerting → Contact points.

---

## Estructura del proyecto

```
smartEnergy/
├── monitor_tuya.py          # Script principal de monitoreo
├── create_dashboard.py      # Crea/actualiza el dashboard en Grafana
├── create_alerts.py         # Crea las reglas de alerta en Grafana
├── cleanup_zeros.py         # Limpieza puntual de registros con energia=0
├── docker-compose.yml       # InfluxDB 1.8 + Grafana 13
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── influxdb.yml # Datasource auto-provisionado
├── systemd/
│   └── tuya-monitor.service # Servicio systemd
├── .env.example             # Plantilla de variables de entorno
├── .gitignore
└── README.md
```

## Detalles técnicos del dispositivo

El breaker FSE-F723C3D40A0F92 usa protocolo Tuya local 3.3 con los siguientes DPS:

| DPS | Contenido | Cómo leer |
|---|---|---|
| `1` | Energía acumulada (× 0.01 = kWh) | `status()` |
| `6` | Paquete binario de mediciones | `updatedps()` |
| `101` | Umbral alarma sobrevoltaje (configuración) | Solo lectura config |
| `102` | Umbral alarma undervoltaje (configuración) | Solo lectura config |
| `103` | Umbral alarma sobrecorriente (configuración) | Solo lectura config |
| `105` | Flag alarma sobrevoltaje activa | `status()` |
| `106` | Flag alarma sobrecorriente activa | `status()` |

**Formato DPS 6 (binario base64):**
```
Bytes 0-1  → Voltaje    (big-endian, ÷ 10 = V)
Bytes 2-4  → Corriente  (big-endian, ÷ 1000 = A)
Bytes 5-7  → Potencia   (big-endian, directo = W)
Bytes 8-9  → Extra (ignorados)
```

> Nota: el dispositivo pierde precisión en corriente > 9A (ej: secador de cabello).
> El factor de potencia se clampea a `min(P/VA, 1.0)` para evitar valores inválidos.

## Variables de entorno

Ver `.env.example` para la lista completa. Las obligatorias son:

- `TUYA_DEVICE_ID`, `TUYA_DEVICE_IP`, `TUYA_DEVICE_KEY`
- `GRAFANA_PASSWORD`, `GRAFANA_DS_UID`

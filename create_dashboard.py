#!/usr/bin/env python3
"""
Crea/actualiza el dashboard de Grafana via API.
Requiere variables de entorno GRAFANA_URL, GRAFANA_USER, GRAFANA_PASSWORD, GRAFANA_DS_UID.
"""
import json
import os
import requests

GRAFANA  = os.environ.get('GRAFANA_URL', 'http://localhost:3001')
AUTH     = (os.environ.get('GRAFANA_USER', 'admin'),
            os.environ['GRAFANA_PASSWORD'])
DS_UID   = os.environ['GRAFANA_DS_UID']


def ds():
    return {"type": "influxdb", "uid": DS_UID}

def target(ref, query, alias=""):
    return {"refId": ref, "query": query, "rawQuery": True, "alias": alias,
            "resultFormat": "time_series", "datasource": ds()}

def stat_panel(pid, title, query, unit, x, y, w=4, h=4,
               thresholds=None, decimals=1):
    steps = thresholds or [
        {"color": "green",  "value": None},
        {"color": "yellow", "value": None},
        {"color": "red",    "value": None},
    ]
    return {
        "id": pid, "type": "stat", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": ds(),
        "targets": [target("A", query)],
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto",
            "colorMode": "background",
            "graphMode": "area",
            "textMode": "auto",
            "justifyMode": "center",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "thresholds": {"mode": "absolute", "steps": steps},
                "color": {"mode": "thresholds"},
            }
        },
    }

def timeseries_panel(pid, title, targets, x, y, w=24, h=9, unit="watt", overrides=None):
    return {
        "id": pid, "type": "timeseries", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": ds(),
        "targets": targets,
        "options": {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"lineWidth": 2, "fillOpacity": 10},
            },
            "overrides": overrides or [],
        },
    }

Q_LAST  = lambda f: f'SELECT last("{f}") FROM "energia" WHERE $timeFilter'
Q_MEAN  = lambda f, a="": f'SELECT mean("{f}") FROM "energia" WHERE $timeFilter GROUP BY time($__interval) fill(none)'

panels = [
    # --- Fila 1: stats ---
    stat_panel(1, "Potencia activa", Q_LAST("potencia_W"), "watt", x=0, y=0, w=5,
               thresholds=[
                   {"color": "green",  "value": None},
                   {"color": "yellow", "value": 1500},
                   {"color": "red",    "value": 3000},
               ]),
    stat_panel(2, "Potencia aparente", Q_LAST("potencia_VA"), "voltamp", x=5, y=0, w=4,
               decimals=0),
    stat_panel(3, "Corriente", Q_LAST("corriente_A"), "amp", x=9, y=0, w=4,
               thresholds=[
                   {"color": "green",  "value": None},
                   {"color": "yellow", "value": 15},
                   {"color": "red",    "value": 35},
               ], decimals=2),
    stat_panel(4, "Voltaje", Q_LAST("voltaje_V"), "volt", x=13, y=0, w=4,
               thresholds=[
                   {"color": "red",    "value": None},
                   {"color": "green",  "value": 200},
                   {"color": "yellow", "value": 245},
                   {"color": "red",    "value": 250},
               ], decimals=1),
    stat_panel(5, "Factor de potencia", Q_LAST("factor_potencia"), "none", x=17, y=0, w=4,
               decimals=3),
    stat_panel(6, "Energía acumulada", Q_LAST("energia_kWh"), "kwatth", x=21, y=0, w=3,
               decimals=1),

    # --- Fila 2: potencia en el tiempo ---
    timeseries_panel(7, "Potencia",
        targets=[
            target("A", Q_MEAN("potencia_W"),  "Activa (W)"),
            target("B", Q_MEAN("potencia_VA"), "Aparente (VA)"),
        ],
        x=0, y=4, w=24, h=9, unit="watt",
        overrides=[{
            "matcher": {"id": "byName", "options": "Aparente (VA)"},
            "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash"}}],
        }],
    ),

    # --- Fila 3: voltaje y corriente ---
    timeseries_panel(8, "Voltaje",
        targets=[target("A", Q_MEAN("voltaje_V"), "Voltaje (V)")],
        x=0, y=13, w=12, h=8, unit="volt",
    ),
    timeseries_panel(9, "Corriente",
        targets=[target("A", Q_MEAN("corriente_A"), "Corriente (A)")],
        x=12, y=13, w=12, h=8, unit="amp",
    ),

    # --- Fila 4: energía acumulada en el tiempo ---
    timeseries_panel(12, "Energia acumulada total",
        targets=[target("A", Q_MEAN("energia_kWh"), "Energia (kWh)")],
        x=0, y=21, w=17, h=8, unit="kwatth",
    ),

    # Delta kWh en el período seleccionado (SPREAD = max - min sobre rango)
    stat_panel(13, "Consumido en el periodo",
        'SELECT SPREAD("energia_kWh") FROM "energia" WHERE $timeFilter',
        unit="kwatth", x=17, y=21, w=7, h=8, decimals=2,
        thresholds=[{"color": "blue", "value": None}],
    ),

    # Tasa de consumo (kWh/h) — derivada de energía acumulada
    timeseries_panel(14, "Tasa de consumo",
        targets=[target("A",
            'SELECT NON_NEGATIVE_DERIVATIVE(mean("energia_kWh"), 1h) FROM "energia"'
            ' WHERE $timeFilter GROUP BY time($__interval) fill(none)',
            "kWh/h")],
        x=0, y=29, w=24, h=7, unit="kwatth",
    ),

    # --- Fila 6: factor de potencia, alerta FP y alarmas ---
    timeseries_panel(10, "Factor de potencia",
        targets=[target("A", Q_MEAN("factor_potencia"), "FP")],
        x=0, y=36, w=12, h=7, unit="none",
    ),

    # Semáforo FP actual
    stat_panel(15, "FP actual",
        Q_LAST("factor_potencia"),
        unit="none", x=12, y=36, w=4, h=7, decimals=3,
        thresholds=[
            {"color": "red",    "value": None},
            {"color": "orange", "value": 0.60},
            {"color": "yellow", "value": 0.70},
            {"color": "green",  "value": 0.85},
        ],
    ),

    {
        "id": 11, "type": "stat", "title": "Alarmas dispositivo",
        "gridPos": {"x": 16, "y": 36, "w": 8, "h": 7},
        "datasource": ds(),
        "targets": [
            target("A", Q_LAST("alarma_sobrevoltaje"),   "Sobrevoltaje"),
            target("B", Q_LAST("alarma_sobrecorriente"), "Sobrecorriente"),
        ],
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "horizontal",
            "colorMode": "background",
            "textMode": "value_and_name",
        },
        "fieldConfig": {
            "defaults": {
                "unit": "none", "decimals": 0,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red",   "value": 1},
                    ],
                },
                "color": {"mode": "thresholds"},
                "mappings": [
                    {"type": "value", "options": {"0": {"text": "OK"},  "color": "green"}},
                    {"type": "value", "options": {"1": {"text": "ALARMA", "color": "red"}}},
                ],
            }
        },
    },
]

dashboard = {
    "dashboard": {
        "id": None,
        "uid": "tuya-energia-v1",
        "title": "Monitor Energia Tuya",
        "tags": ["tuya", "energia"],
        "timezone": "browser",
        "schemaVersion": 38,
        "refresh": "10s",
        "time": {"from": "now-3h", "to": "now"},
        "panels": panels,
    },
    "overwrite": True,
    "folderId": 0,
}

r = requests.post(f"{GRAFANA}/api/dashboards/db", auth=AUTH,
                  headers={"Content-Type": "application/json"},
                  data=json.dumps(dashboard))
print(r.status_code, r.json())

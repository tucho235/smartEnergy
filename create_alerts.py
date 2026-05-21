#!/usr/bin/env python3
"""
Crea alertas de Grafana para factor de potencia bajo y alarmas del breaker.
Requiere variables de entorno GRAFANA_URL, GRAFANA_USER, GRAFANA_PASSWORD, GRAFANA_DS_UID.
"""
import requests
import os

GRAFANA  = os.environ.get('GRAFANA_URL', 'http://localhost:3001')
AUTH     = (os.environ.get('GRAFANA_USER', 'admin'),
            os.environ['GRAFANA_PASSWORD'])
DS_UID   = os.environ['GRAFANA_DS_UID']

s = requests.Session()
s.auth = AUTH
s.headers.update({"Content-Type": "application/json"})

# 1. Crear carpeta para las alertas
r = s.post(f"{GRAFANA}/api/folders", json={"title": "Tuya Alertas", "uid": "tuya-alertas"})
if r.status_code in (200, 409):
    folder_uid = "tuya-alertas"
    print(f"Carpeta lista: {folder_uid}")
else:
    print(f"Error creando carpeta: {r.status_code} {r.text}")
    exit(1)


def make_fp_alert(uid, title, fp_threshold, potencia_min, for_duration, summary):
    """
    Alerta de FP bajo con 3 nodos:
      A → query InfluxDB (mean FP en ventana)
      B → reduce: last value
      C → threshold: < fp_threshold
    Solo dispara si potencia_W > potencia_min (evita falsos positivos en carga baja)
    """
    query = (
        f'SELECT mean("factor_potencia") FROM "energia" '
        f'WHERE $timeFilter AND "potencia_W" > {potencia_min} '
        f'GROUP BY time(2m) fill(none)'
    )
    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": 600, "to": 0},
                "datasourceUid": DS_UID,
                "model": {
                    "rawQuery": True,
                    "query": query,
                    "resultFormat": "time_series",
                    "refId": "A",
                },
            },
            {
                "refId": "B",
                "relativeTimeRange": {"from": 600, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "reduce",
                    "refId": "B",
                    "expression": "A",
                    "reducer": "last",
                    "settings": {"mode": "dropNN"},
                },
            },
            {
                "refId": "C",
                "relativeTimeRange": {"from": 600, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "threshold",
                    "refId": "C",
                    "expression": "B",
                    "conditions": [{
                        "evaluator": {"type": "lt", "params": [fp_threshold]},
                        "operator":  {"type": "and"},
                        "query":     {"params": ["C"]},
                        "reducer":   {"type": "last"},
                        "type": "query",
                    }],
                },
            },
        ],
        "noDataState":  "NoData",
        "execErrState": "Error",
        "for": for_duration,
        "orgId": 1,
        "folderUID": folder_uid,
        "ruleGroup": "fp-alerts",
        "labels":      {"severity": "warning" if fp_threshold >= 0.65 else "critical"},
        "annotations": {
            "summary":     summary,
            "description": f"Factor de potencia bajo {fp_threshold} sostenido por {for_duration}.",
        },
        "isPaused": False,
    }


def make_alarm_alert(uid, title, field, for_duration, summary):
    """Alerta para alarmas binarias del breaker (sobrevoltaje / sobrecorriente)."""
    query = f'SELECT last("{field}") FROM "energia" WHERE $timeFilter'
    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": 60, "to": 0},
                "datasourceUid": DS_UID,
                "model": {
                    "rawQuery": True,
                    "query": query,
                    "resultFormat": "time_series",
                    "refId": "A",
                },
            },
            {
                "refId": "B",
                "relativeTimeRange": {"from": 60, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "reduce",
                    "refId": "B",
                    "expression": "A",
                    "reducer": "last",
                    "settings": {"mode": "dropNN"},
                },
            },
            {
                "refId": "C",
                "relativeTimeRange": {"from": 60, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "type": "threshold",
                    "refId": "C",
                    "expression": "B",
                    "conditions": [{
                        "evaluator": {"type": "gt", "params": [0]},
                        "operator":  {"type": "and"},
                        "query":     {"params": ["C"]},
                        "reducer":   {"type": "last"},
                        "type": "query",
                    }],
                },
            },
        ],
        "noDataState":  "NoData",
        "execErrState": "Error",
        "for": for_duration,
        "orgId": 1,
        "folderUID": folder_uid,
        "ruleGroup": "fp-alerts",
        "labels":      {"severity": "critical"},
        "annotations": {"summary": summary},
        "isPaused": False,
    }


alertas = [
    make_fp_alert(
        uid="fp-bajo-warning",
        title="FP Bajo — Warning (< 0.70)",
        fp_threshold=0.70,
        potencia_min=150,
        for_duration="5m",
        summary="FP cayó por debajo de 0.70 con carga significativa",
    ),
    make_fp_alert(
        uid="fp-bajo-critical",
        title="FP Bajo — Critical (< 0.60)",
        fp_threshold=0.60,
        potencia_min=150,
        for_duration="2m",
        summary="FP crítico por debajo de 0.60 — revisar cargas inductivas",
    ),
    make_alarm_alert(
        uid="alarma-sobrevoltaje",
        title="Alarma Sobrevoltaje (>250V)",
        field="alarma_sobrevoltaje",
        for_duration="30s",
        summary="El breaker detectó sobrevoltaje (>250V)",
    ),
    make_alarm_alert(
        uid="alarma-sobrecorriente",
        title="Alarma Sobrecorriente (>40A)",
        field="alarma_sobrecorriente",
        for_duration="30s",
        summary="El breaker detectó sobrecorriente (>40A)",
    ),
]

for alerta in alertas:
    r = s.post(f"{GRAFANA}/api/v1/provisioning/alert-rules", json=alerta)
    if r.status_code in (200, 201):
        print(f"Alerta creada: {alerta['title']}")
    else:
        r2 = s.put(f"{GRAFANA}/api/v1/provisioning/alert-rules/{alerta['uid']}", json=alerta)
        if r2.status_code in (200, 201):
            print(f"Alerta actualizada: {alerta['title']}")
        else:
            print(f"Error: {r.status_code} {r.text}")

# Verificar contact points existentes
cp = s.get(f"{GRAFANA}/api/v1/provisioning/contact-points").json()
print(f"\nContact points configurados: {len(cp)}")
for c in cp:
    print(f"  - {c['name']} ({c['type']})")

print("""
Las alertas están activas en Grafana.
Para recibir notificaciones configurá un contact point:
  → Grafana → Alerting → Contact points → Add contact point
  → Elegí Telegram, Email, o cualquier otro canal
""")

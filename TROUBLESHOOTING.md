# Troubleshooting

## App no carga tras un deploy

```bash
systemctl status family-dashboard       # ¿está caído?
journalctl -u family-dashboard -n 20 --no-pager   # ver el error de arranque
```

El error suele aparecer como `Traceback` justo al arrancar. Casos comunes:
- `NameError` — clase o función usada antes de estar definida en el archivo
- `ImportError` — dependencia que falta en requirements.txt
- `SyntaxError` — error de Python en el código nuevo

---

## Logs del servicio

```bash
# Últimas 50 líneas
journalctl -u family-dashboard -n 50 --no-pager

# Solo errores y avisos
journalctl -u family-dashboard -n 100 --no-pager | grep -E "ERROR|WARNING|Traceback"

# Logs del sync
journalctl -u family-dashboard -n 50 --no-pager | grep -E "sync|Token"
```

---

## Estado del runner de GitHub Actions

```bash
systemctl status actions.runner.mglzgsr-family-dashboard.family
```

Si los deploys se quedan en `queued` en GitHub, el runner está caído. Arrancarlo:

```bash
systemctl start actions.runner.mglzgsr-family-dashboard.family
```

---

## Versión desplegada

Confirmar que el código nuevo está activo (desde el servidor o desde fuera):

```bash
curl -s http://localhost:8001/api/status | python3 -c "import sys,json; print(json.load(sys.stdin).get('version'))"
```

También visible en la app: Settings → pie del modal → `vX.X.X · Última sync: ...`

---

## Probar la API directamente

```bash
curl -s http://localhost:8001/api/status | python3 -m json.tool
curl -s "http://localhost:8001/api/events" | python3 -m json.tool
curl -s "http://localhost:8001/api/google/accounts" | python3 -m json.tool
```

---

## Base de datos

```bash
sqlite3 /opt/family-planner/data/family.db

# Comandos útiles dentro de sqlite3:
.tables
SELECT title, source, series_id FROM events LIMIT 20;
SELECT * FROM google_accounts;
SELECT * FROM google_calendars;
SELECT key, value FROM settings;
.quit
```

---

## Sync manual desde el servidor

```bash
curl -s -X POST http://localhost:8001/api/sync | python3 -m json.tool
```

---

## Reiniciar el servicio

```bash
systemctl restart family-dashboard
systemctl status family-dashboard
```

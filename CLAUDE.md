# CLAUDE.md — Family Dashboard

Contexto para Claude Code sobre este proyecto.

## Qué es

App de calendario y tareas familiares, auto-hospedada. Miembros: Ale, Miguel, Noa, Oli + vistas Familia y Cumpleaños. Stack: FastAPI + SQLite + vanilla JS (SPA sin frameworks).

## Stack y estructura

```
main.py           # FastAPI: OAuth, API REST, lifespan, sync periódico
database.py       # SQLite CRUD (WAL mode). Schema: events, tasks, settings,
                  # event_assignments, hidden_events, google_accounts,
                  # google_calendars, ics_calendars
calendar_sync.py  # Sync: Google Calendar (OAuth2), Apple iCloud (CalDAV), ICS/webcal
frontend/
  index.html      # SPA completa (~2400 líneas). Todo vanilla JS + CSS en un solo archivo.
  assets/         # Fotos de miembros: ale.jpg, miguel.jpg, noa.jpg, oli.jpg, family.jpg
data/family.db    # SQLite (creado en runtime, no commitear)
```

## Cómo correr en local

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env  # rellenar credenciales
venv/bin/uvicorn main:app --reload --port 8001
```

## Despliegue en producción

- Servidor compartido (LXC) con otro servicio `finances`
- systemd service: `family-dashboard` corriendo como usuario `family`
- Código en `/opt/family-planner`
- Auto-deploy: GitHub Actions con runner self-hosted — al hacer push a `main` hace `git pull` + `pip install` + `systemctl restart`
- Tunnel Cloudflare Zero Trust → `family.mglzgsr.com`

## Miembros del sistema

```python
MEMBERS = ["ale", "miguel", "noa", "oli", "family", "birthday"]
```

Colores: ale=#7C3AED, miguel=#2563EB, noa=#059669, oli=#D97706, family=#DB2777, birthday=#B45309

Fotos de miembros con cache-busting MD5 via `_img(name)` en main.py. Se sirven como `/static/assets/{name}.jpg?v={hash}`.

## Frontend — arquitectura

- Hash routing: `#/`, `#/member/ale`, etc.
- Estado global en objeto `state` (events, tasks, members, status, currentMember, currentWeek, calView, currentDay)
- Fuentes: Inter (cuerpo), Syne (títulos), DM Mono (monoespaciado)
- Tema dark/light con tokens CSS (`--bg`, `--surface`, `--border`, `--text`, etc.), persistido en localStorage
- Breakpoint móvil: 640px

## Vistas del calendario

- **Semana** (default): grid 7 columnas `repeat(7, minmax(0, 1fr))`, día min-height 380px
- **Día**: lista de eventos del día seleccionado
- **Mes**: grid de celdas, clic en día → vista día

## Modales

- `#modal-event`: Crear evento (título, fecha, hora, todo-el-día, ubicación, descripción, recurrencia, member picker con fotos, multiselección)
- `#modal-event-detail`: Ver evento (vista: participantes no interactivos + Cerrar/Editar) / (editar: participantes clickables + Eliminar/Cancelar/Guardar)
- `#modal-settings`: Cuentas Google, calendarios guardados, feeds ICS, última sync, botón sync, toggle tema

## Panel de tareas

Tabla debajo del calendario. Columnas: checkbox · Tarea · Asignado (foto) · Prioridad (badge colored) · Vence · Eliminar. Formulario inline de creación con photo picker de miembro (single-select).

## API endpoints

```
GET  /api/status                    # miembros, conexión Google/Apple/ICS, last_sync
GET  /api/events?week=&member=      # eventos de una semana
POST /api/events                    # crear evento manual
PUT  /api/events/{id}/assignments   # asignar miembros a evento
DELETE /api/events/{id}             # ocultar/borrar evento
GET  /api/tasks?member=             # listar tareas
POST /api/tasks                     # crear tarea
PATCH /api/tasks/{id}               # actualizar tarea
DELETE /api/tasks/{id}              # borrar tarea
POST /api/sync                      # disparar sync en background
GET  /api/google/accounts           # cuentas Google conectadas
GET  /api/google/calendars          # calendarios disponibles en Google
GET/POST/DELETE /api/google/saved-calendars  # calendarios guardados en DB
GET/POST/DELETE /api/ics-calendars  # feeds ICS en DB
GET  /auth/google                   # iniciar OAuth Google
GET  /auth/google/callback          # callback OAuth
GET  /auth/google/disconnect/{id}   # desconectar cuenta
```

## Calendar sync

`sync_all(weeks_ahead=3)` sincroniza semana actual + 3 semanas adelante. Corre:
- Al arrancar la app (lifespan)
- Cada `SYNC_INTERVAL_MINUTES` minutos (default 30) en background task
- Al conectar una cuenta Google o añadir un calendario
- Manualmente via botón en settings → `POST /api/sync`

El timestamp de última sync se guarda como `datetime.utcnow().isoformat() + "Z"` para que el navegador lo interprete correctamente en cualquier zona horaria.

## Variables de entorno relevantes

```
APP_URL                    # URL pública para redirect OAuth
SYNC_INTERVAL_MINUTES      # default 30
GOOGLE_CLIENT_ID/SECRET    # app OAuth de Google Cloud Console
GOOGLE_CALENDAR_{MEMBER}   # ID de calendario por miembro (env legacy)
APPLE_ID / APPLE_APP_PASSWORD
APPLE_CALENDAR_{MEMBER}    # nombres de calendarios en iCloud
ICS_URL_{MEMBER}           # URLs webcal:// o https://
DB_PATH                    # default ./data/family.db
```

## Compatibilidad iOS

- No usar `?.` (optional chaining) — no soportado en iOS < 14
- No usar `inset: 0` en CSS — usar `top:0; right:0; bottom:0; left:0`
- Los modales usan `position: fixed; top:0; right:0; bottom:0; left:0`
- Modal body: `max-height: calc(100dvh - 32px); overflow-y: auto`

## Convenciones de código

- El frontend es un único archivo HTML. CSS y JS están inline.
- Los eventos sincronizados tienen `source = 'google' | 'apple' | 'ics'`; los manuales `source = 'manual'`
- Eventos ocultados van a tabla `hidden_events` (DELETE no borra sinced events, solo los oculta)
- IDs manuales: `manual-{uuid}`; Google: `gcal-{id}`; Apple: `apple-{uid}`; ICS: `ics-{uid}`
- El member picker de fotos reutiliza las clases CSS `.ev-member-btn`, `.ev-member-picker`, `.ev-m-emoji`, `.ev-m-name`

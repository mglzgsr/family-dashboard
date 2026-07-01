# Family Dashboard — CLAUDE.md

Calendario y tareas familiares. FastAPI + SQLite + HTML/JS vanilla. Sin frameworks.

## Infraestructura
- **Producción**: `/opt/family-planner`, servicio `family-dashboard`, puerto 8001, `family.mglzgsr.com`
- **Deploy**: push a `main` → GitHub Actions runner self-hosted → `git pull` + `pip install` + `systemctl restart`
- **DB**: `/opt/family-planner/data/family.db`
- **Acceso**: Cloudflare Zero Trust Tunnel (sin login propio)
- **Servidor compartido** con `finances` (mismo LXC)

## Archivos clave
- `main.py` — FastAPI app, OAuth Google, API REST, sync periódico en background
- `database.py` — SQLite CRUD (WAL mode, foreign keys on)
- `calendar_sync.py` — sync Google Calendar (OAuth2), Apple iCloud (CalDAV), ICS/webcal
- `frontend/index.html` — SPA completa (~2700 líneas, CSS + JS inline)
- `frontend/assets/` — fotos de miembros: `ale.jpg`, `miguel.jpg`, `noa.jpg`, `oli.jpg`, `family.jpg`

## Base de datos — tablas
- **events**: `id, title, start_dt, end_dt, all_day, member_id, source, description, location, external_id, series_id, manually_edited`
- **tasks**: `id, title, member_id, completed, due_date, priority, notes, created_at, completed_at`
- **event_assignments**: `event_id, member_id` — asignaciones multi-miembro sobre un evento
- **hidden_events**: `event_id` — DELETE de evento synced no borra, solo oculta aquí
- **google_accounts**: `id, email, token_json, connected_at` — multi-cuenta Google
- **google_calendars**: `id, calendar_id, name, member_id, account_id`
- **ics_calendars**: `id, name, url, member_id`
- **settings**: key/value — `last_sync`, `google_token` (legacy migrado)

Columnas añadidas via migración en `init_db` (ALTER TABLE con try/except):
- `events.series_id` — agrupa eventos de una misma serie recurrente
- `events.manually_edited` — `1` si el usuario ha editado el evento; el sync lo respeta y no sobreescribe

## Miembros
```python
MEMBERS = ["ale", "miguel", "noa", "oli", "family", "birthday"]
```
Colores: `ale=#7C3AED`, `miguel=#2563EB`, `noa=#059669`, `oli=#0D9488`, `family=#DB2777`, `birthday=#B45309`

## API endpoints
```
GET    /api/status                          ← miembros, conexión Google/Apple/ICS, last_sync
GET    /api/events?week=&member=            ← eventos de la semana (lunes a lunes)
POST   /api/events                          ← crear evento manual (acepta series_id)
POST   /api/events/batch                    ← crear múltiples eventos de golpe (series)
PATCH  /api/events/{id}                     ← editar campos (title, start_dt, end_dt, location, description, series_id)
PUT    /api/events/{id}/assignments         ← asignar miembros (multi-member)
DELETE /api/events/{id}                     ← oculta synced, borra manual; ?reimport=true borra sin ocultar
DELETE /api/series/{series_id}              ← elimina todos los eventos de una serie
GET    /api/tasks?member=
POST   /api/tasks
PATCH  /api/tasks/{id}
DELETE /api/tasks/{id}
POST   /api/sync                            ← dispara sync en background
POST   /api/admin/reset                     ← borra events, tasks, assignments, hidden_events y relanza sync
GET    /api/google/accounts
GET    /api/google/calendars?account_id=    ← calendarios disponibles vía API Google
GET/POST/DELETE /api/google/saved-calendars
GET/POST/DELETE /api/ics-calendars
GET    /auth/google                         ← iniciar OAuth
GET    /auth/google/callback
GET    /auth/google/disconnect/{account_id}
```

## Calendar sync
- `sync_all(weeks_ahead=3)` — sincroniza semana actual + 3 semanas adelante
- Corre al arrancar, cada `SYNC_INTERVAL_MINUTES` min (default 30), al conectar cuenta, y manualmente
- `last_sync` se guarda como `datetime.utcnow().isoformat() + "Z"` — la `Z` es obligatoria para que el navegador lo interprete en UTC y no como hora local
- IDs de eventos: `gcal-{id}`, `apple-{uid}`, `ics-{uid}`, `manual-{uuid}`
- `OAUTHLIB_RELAX_TOKEN_SCOPE=1` — necesario porque Google devuelve scope extra (`openid`) que rompe la validación
- `upsert_event` tiene `WHERE events.manually_edited = 0` — no sobreescribe eventos editados manualmente

## Frontend — arquitectura
- Hash routing: `#/`, `#/member/ale`, etc.
- Estado global en `state`: `events, tasks, members, status, currentMember, currentWeek, calView, currentDay`
- Tema dark/light con tokens CSS, persistido en localStorage
- Fotos de miembros con cache-busting MD5 via `_img(name)` en `main.py`

## Vistas del calendario
- **Semana** (default): grid `repeat(7, minmax(0, 1fr))` — `minmax(0, 1fr)` evita que columnas se expandan con el contenido
- **Día** / **Mes**: vistas adicionales con navegación propia
- Semana min-height columnas: 380px en desktop, 120px en móvil
- Event chips en vista semana siempre tienen dos líneas (título + lugar con `&nbsp;` si no hay lugar) para altura uniforme

## Modales
- `#modal-event`: crear evento con recurrencia (diaria, semanal con day-picker, mensual, semestral, anual), member picker multi-select con fotos. Usa `/api/events/batch` para series.
- `#modal-event-detail`:
  - **Vista**: meta (fecha/lugar/descripción), participantes no interactivos, botones Cerrar/Editar
  - **Editar**: campos title, date, start/end time, location, recurrence + day-picker, participantes. Botones Eliminar/Cancelar/Guardar
  - **Eliminar manual sin serie**: two-tap (botón → "¿Seguro?" → confirmar)
  - **Eliminar serie**: "Solo este" / "Toda la serie"
  - **Eliminar evento synced**: "Volverá en sync" (borra sin ocultar) / "Ocultar siempre" (hidden_events)
  - Guardar desactiva el botón con `disabled + pointer-events:none` para evitar doble envío; se resetea al entrar en modo edición
- `#modal-settings`: cuentas Google, calendarios guardados, ICS feeds, última sync, botón sync, toggle tema, botón "Borrar todos los datos"

## Google Places Autocomplete
- Campos `#ev-location` y `#detail-edit-location` usan `google.maps.places.Autocomplete`
- Requiere `GOOGLE_MAPS_API_KEY` en `.env` — el backend inyecta la key en el HTML via `__GOOGLE_MAPS_API_KEY__`
- Necesita **Maps JavaScript API** + **Places API** (legacy) habilitadas en Google Cloud

## Series de eventos recurrentes
- Al crear con repetición, todos los eventos del grupo reciben el mismo `series_id`
- `series_id` formato: `series-{timestamp}-{random}`
- `DELETE /api/series/{series_id}` borra todos los eventos del grupo
- Al editar un evento y añadir recurrencia, se asigna `series_id` al evento actual y se crean los adicionales via batch

## Compatibilidad iOS
- No usar `?.` (optional chaining) — no soportado en iOS < 14
- No usar `inset: 0` — usar `top:0; right:0; bottom:0; left:0`
- Modales: `position: fixed` con las cuatro propiedades por separado
- `max-height: calc(100dvh - 32px); overflow-y: auto` en `.modal`

## Decisiones técnicas
- Sin Docker — venv directo en LXC (igual que `finances`)
- Multi-cuenta Google: cada cuenta tiene su `token_json` en `google_accounts`. Hay migración automática del token legacy de `.env` a la nueva tabla
- El refresh de token de Google usa `creds.expired or creds.expiry is None` — sin `expiry is None` falla con tokens migrados que no tienen fecha de expiración
- Day buttons de recurrencia semanal usan `inline styles` en lugar de clases CSS porque `var(--accent)` no estaba definido y los botones desaparecían
- Botón "días de la semana" usa CSS `#ev-days-row { display:none } #ev-days-row:not(.hidden) { display:flex }` — inline `style="display:flex"` sobrescribía `.hidden`. El mismo patrón se aplica a `#detail-ev-days-row` en el modal de edición
- El member picker reutiliza clases `.ev-member-btn`, `.ev-member-picker`, `.ev-m-emoji`, `.ev-m-name` en todos los contextos (evento nuevo, detalle evento, tarea nueva)
- `.detail-day-pill` tiene también clase `.day-pill` para estilos; el listener de `.day-pill` usa `:not(.detail-day-pill)` para evitar doble registro
- `.btn:disabled { opacity:0.5; pointer-events:none }` — previene doble click en Guardar durante operaciones async
- `confirm()` del browser reemplazado por confirmación inline en dos pasos (el usuario marcó "no volver a preguntar")

## Pendiente
- Push notifications para recordatorios de tareas
- Vista de miembro en móvil más compacta
- Soporte para eventos de varios días en vista semana

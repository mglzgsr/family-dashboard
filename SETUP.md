# 🏠 Family Dashboard — Guía de configuración

Stack: **FastAPI + Python 3.12 · SQLite · Docker · Cloudflare Zero Trust**

---

## 1. Primera puesta en marcha (local)

```bash
cd ~/Projects/family-dashboard

# Copiar variables de entorno
cp .env.example .env
# → Edita .env con tus valores (ver secciones abajo)

# Arrancar
docker compose up -d

# Ver logs
docker compose logs -f
```

Abre http://localhost:8001 en el navegador.

---

## 2. Google Calendar

### 2a. Crear proyecto en Google Cloud (una vez)

1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Crea un proyecto nuevo → "Family Dashboard"
3. **APIs y servicios → Biblioteca** → busca y activa **Google Calendar API**
4. **APIs y servicios → Credenciales → + Crear → ID de cliente OAuth 2.0**
   - Tipo: **Aplicación web**
   - URI de redireccionamiento autorizado: `https://family.tudominio.com/auth/google/callback`
   - (también añade `http://localhost:8001/auth/google/callback` para desarrollo)
5. Copia el **Client ID** y **Client Secret** → ponlos en `.env`

### 2b. Pantalla de consentimiento OAuth

1. **APIs y servicios → Pantalla de consentimiento → Externo**
2. Rellena nombre y email
3. Ámbitos → añade: `https://www.googleapis.com/auth/calendar.readonly`
4. Usuarios de prueba → añade tu email (y el de Ale si usa cuenta distinta)

### 2c. IDs de calendarios

En [Google Calendar](https://calendar.google.com):
1. Junto a cada calendario → **⋮ → Configuración y uso compartido**
2. Baja hasta **ID del calendario**
3. Ponlo en `.env` bajo `GOOGLE_CALENDAR_ALE`, `GOOGLE_CALENDAR_MIGUEL`, etc.

### 2d. Conectar

Con la app corriendo, abre http://localhost:8001 → click en **Conectar Google**.
Autoriza → los tokens se guardan en la base de datos. Ya no hace falta repetirlo.

---

## 3. Apple Calendar (iCloud CalDAV)

No necesitas registrarte en ningún portal. Solo necesitas:

### 3a. Contraseña de aplicación (app-specific password)

1. Ve a [appleid.apple.com](https://appleid.apple.com) → **Seguridad → Contraseñas de aplicaciones**
2. Genera una nueva → ponla en `.env` como `APPLE_APP_PASSWORD`
3. El `APPLE_ID` es tu email de Apple

### 3b. Nombres de los calendarios

En la app Calendario de Mac o iOS:
- Los nombres de tus calendarios de iCloud son exactamente los que ves en la barra lateral
- Ponlos en `.env` bajo `APPLE_CALENDAR_ALE`, `APPLE_CALENDAR_MIGUEL`, etc.
- Si un miembro tiene más de un calendario relevante, sepáralos con coma:
  `APPLE_CALENDAR_MIGUEL=Personal,Trabajo`

La sincronización es automática cada 30 minutos, o puedes pulsar el botón de refresh en la app.

---

## 4. Cloudflare Zero Trust (acceso remoto)

Como ya tienes configurado Zero Trust para el proyecto de finanzas, el proceso es el mismo:

1. En el Cloudflare Zero Trust dashboard → **Access → Applications → Add application**
2. Tipo: **Self-hosted**
3. Dominio: `family.tudominio.com`
4. Redirige al tunnel que ya tienes corriendo en la máquina, apuntando al puerto **8001**

Recuerda actualizar `APP_URL=https://family.tudominio.com` en `.env` para que el redirect de Google OAuth funcione correctamente desde fuera.

---

## 5. Despliegue con GitHub Actions

El workflow en `.github/workflows/deploy.yml` usa el mismo **self-hosted runner** que el proyecto de finanzas:

```bash
# En la raíz del repo, el runner ejecuta:
docker compose build --no-cache
docker compose up -d --force-recreate
```

Cada `git push` a `main` despliega automáticamente.

---

## 6. Estructura del proyecto

```
family-dashboard/
├── main.py            # FastAPI: rutas, lifespan, OAuth
├── database.py        # SQLite: init, CRUD eventos/tareas/settings
├── calendar_sync.py   # Google Calendar API + Apple CalDAV
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env               # (no committear — está en .gitignore)
├── .env.example
├── data/              # SQLite DB — persistido vía Docker volume
└── frontend/
    └── index.html     # SPA: familia + vistas individuales
```

---

## 7. Uso diario

- **Vista familia**: página principal con el calendario semanal de toda la familia y lista de tareas
- **Vista individual**: click en el nombre de cualquier miembro → sus eventos y tareas filtrados
- **Añadir evento manual**: botón `+ Evento` en la cabecera (útil para cosas de Apple Calendar que no estén en iCloud, o eventos rápidos)
- **Añadir tarea**: `+ Nueva` en el panel de tareas → título, asignado, prioridad y fecha límite opcional
- **Sync manual**: botón de refresh en la cabecera (la sync automática ocurre cada 30 min)
- **Dark/light mode**: botón 🌙 en la cabecera

---

## 8. Roadmap

- [ ] Vista mensual del calendario
- [ ] Notificaciones push / recordatorios
- [ ] Lista de la compra compartida
- [ ] Fotos/avatares personalizados
- [ ] Importación de .ics para calendarios fuera de Google/iCloud

# SchoolTrackdjango

Sistema de gestión escolar desarrollado con Django.

## Características

- Gestión de usuarios (administradores, administrativos, maestros, alumnos)
- Gestión de carreras, materias y grupos
- Registro de asistencia y calificaciones
- Generación de reportes en PDF
- Sistema de bloqueo de cuentas por seguridad

## Requisitos

- Python 3.8+
- PostgreSQL
- pip

## Instalación local

1. Clonar el repositorio:
```bash
git clone https://github.com/samueleks/SchoolTrackdjango.git
cd SchoolTrackdjango
```

2. Crear entorno virtual:
```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

3. Instalar dependencias:
```bash
pip install -r requirements.txt
```

4. Configurar variables de entorno:
```bash
cp .env.example .env
# Editar .env con tus credenciales de base de datos
```

5. Ejecutar migraciones:
```bash
python manage.py migrate
```

6. Crear superusuario:
```bash
python manage.py createsuperuser
```

7. Ejecutar servidor:
```bash
python manage.py runserver
```

## Deployment en Producción

### Opción 1: Railway

1. Crear cuenta en https://railway.app/
2. Crear nuevo proyecto desde GitHub
3. Railway detectará automáticamente que es Django
4. Configurar variables de entorno en Railway:
   - `DJANGO_SECRET_KEY`: Genera una clave larga y aleatoria
   - `DJANGO_DEBUG`: False
   - `DJANGO_ALLOWED_HOSTS`: tu-dominio.railway.app
   - `DJANGO_CSRF_TRUSTED_ORIGINS`: https://tu-dominio.railway.app
   - `DJANGO_SECURE_COOKIES`: 1
   - `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, `DJANGO_DB_PORT`: Configuración de PostgreSQL en Railway
5. Railway hará deploy automático

### Opción 2: Render

1. Crear cuenta en https://render.com/
2. Crear nuevo Web Service desde GitHub
3. Configurar:
   - Build Command: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - Start Command: `gunicorn SchoolTrackdjango.wsgi:application`
4. Agregar PostgreSQL en Render
5. Configurar variables de entorno (similar a Railway)

### Opción 3: Heroku

1. Instalar Heroku CLI
2. Login: `heroku login`
3. Crear app: `heroku create`
4. Configurar variables de entorno:
```bash
heroku config:set DJANGO_SECRET_KEY=tu-clave
heroku config:set DJANGO_DEBUG=False
heroku config:set DJANGO_ALLOWED_HOSTS=tu-app.herokuapp.com
heroku config:set DJANGO_SECURE_COOKIES=1
```
5. Agregar PostgreSQL: `heroku addons:create heroku-postgresql`
6. Deploy: `git push heroku main`

## Variables de Entorno

Copiar `.env.example` a `.env` y configurar:

- `DJANGO_SECRET_KEY`: Clave secreta de Django (mínimo 50 caracteres)
- `DJANGO_DEBUG`: True para desarrollo, False para producción
- `DJANGO_ALLOWED_HOSTS`: Dominios permitidos (separados por coma)
- `DJANGO_DB_NAME`: Nombre de la base de datos
- `DJANGO_DB_USER`: Usuario de PostgreSQL
- `DJANGO_DB_PASSWORD`: Contraseña de PostgreSQL
- `DJANGO_DB_HOST`: Host de PostgreSQL
- `DJANGO_DB_PORT`: Puerto de PostgreSQL (default: 5432)

## Seguridad en Producción

Para producción, asegúrate de:

- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY` largo y aleatorio
- `DJANGO_ALLOWED_HOSTS` configurado con tu dominio
- `DJANGO_SECURE_COOKIES=1`
- `DJANGO_CSRF_TRUSTED_ORIGINS` configurado con HTTPS
- Base de datos PostgreSQL (no SQLite)
- Usar HTTPS

## Estructura del Proyecto

```
SchoolTrackdjango/
├── SchoolTrackdjango/      # Configuración de Django
├── login/                  # Aplicación principal
│   ├── Templates/          # Plantillas HTML
│   ├── migrations/        # Migraciones de base de datos
│   └── static/           # Archivos estáticos
├── backups/              # Respaldos de base de datos
├── Procfile             # Configuración de deployment
├── requirements.txt     # Dependencias de Python
└── .env.example        # Ejemplo de variables de entorno
```

## Licencia

Este proyecto es para uso educativo.

<!-- Deployment actualizado en Railway -->

# Funcionalidad de Desbloqueo de Cuentas - SchoolTrack

## Descripción General

Se han implementado dos mecanismos de desbloqueo de cuentas bloqueadas:

1. **Desbloqueo Manual por Administrador** - Desbloqueo inmediato controlado
2. **Desbloqueo Automático después de 30 minutos** - Mecanismo de recuperación automática

---

## 1. Bloqueo de Cuentas

### Cuándo se bloquea una cuenta

Una cuenta se bloquea automáticamente cuando:
- El usuario intenta iniciar sesión **5 veces consecutivas** con contraseña incorrecta
- El sistema establece `cuenta_bloqueada = True`
- Se registra la fecha y hora en `fecha_bloqueo`

### Campos relacionados en el modelo `Usuarios`

```python
intentos_fallidos_login = models.IntegerField(default=0)      # Contador de intentos fallidos
cuenta_bloqueada = models.BooleanField(default=False)         # Estado de la cuenta
fecha_bloqueo = models.DateTimeField(null=True, blank=True)   # Cuándo se bloqueó
```

---

## 2. Desbloqueo Automático (30 minutos)

### Implementación

El desbloqueo automático funciona en **cada intento de login**. Si la cuenta está bloqueada:

1. El sistema verifica cuánto tiempo ha pasado desde el bloqueo
2. Si han pasado **≥ 30 minutos**, desbloquea automáticamente:
   - `cuenta_bloqueada = False`
   - `intentos_fallidos_login = 0`
   - `fecha_bloqueo = None`
3. Si han pasado **< 30 minutos**, muestra el tiempo restante

### Código (en `views.py`)

```python
# En login_alumno, login_maestro, login_administrativo, login_administrador

if usuario.cuenta_bloqueada:
    from datetime import timedelta
    tiempo_bloqueo = timezone.now() - usuario.fecha_bloqueo
    
    if tiempo_bloqueo >= timedelta(minutes=30):
        # Desbloquear automáticamente
        usuario.cuenta_bloqueada = False
        usuario.intentos_fallidos_login = 0
        usuario.fecha_bloqueo = None
        usuario.save()
    else:
        minutos_restantes = 30 - int(tiempo_bloqueo.total_seconds() / 60)
        error_msg = f'Cuenta bloqueada. Se desbloqueará en {minutos_restantes} minutos o contacte al administrador.'
```

### Mensaje al usuario

```
"Cuenta bloqueada. Se desbloqueará en 25 minutos o contacte al administrador."
```

---

## 3. Desbloqueo Manual por Administrador

### Acceso

1. Ir a **Panel de Administrador** → **Gestionar Seguridad**
2. Se muestra la sección **"Cuentas Bloqueadas"** en rojo
3. Buscar la cuenta bloqueada
4. Hacer clic en el botón **"Desbloquear"** (verde)

### Interfaz

En `GestionSeguridad.html`:

```html
<form method="POST" action="{% url 'desbloquear_cuenta' cuenta.id_usuario %}" 
      onsubmit="return confirm('¿Desbloquear la cuenta de {{ cuenta.nombre }} {{ cuenta.apellido }}?');">
    {% csrf_token %}
    <button type="submit" class="text-xs bg-green-500 hover:bg-green-600 text-white px-3 py-1 rounded-full font-bold transition">
        <i class="fas fa-unlock"></i> Desbloquear
    </button>
</form>
```

### Flujo

1. El administrador hace clic en "Desbloquear"
2. Se muestra un diálogo de confirmación
3. Si confirma, se envía un POST a `/administrador/seguridad/desbloquear/<usuario_id>/`
4. La función `desbloquear_cuenta` procesa el desbloqueo
5. Se registra en los logs (auditoría)
6. Se muestra un mensaje de éxito

### Código (en `admin_views.py`)

```python
def desbloquear_cuenta(request, usuario_id):
    """Desbloquea una cuenta de usuario manualmente"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    if request.method != 'POST':
        return redirect('gestion_seguridad')
    
    try:
        usuario = Usuarios.objects.get(id_usuario=usuario_id)
        
        if usuario.cuenta_bloqueada:
            usuario.cuenta_bloqueada = False
            usuario.intentos_fallidos_login = 0
            usuario.fecha_bloqueo = None
            usuario.save()
            
            admin_nombre = request.session.get('usuario_nombre', 'Administrador')
            logger.info(f'Cuenta desbloqueada: {usuario.nombre} {usuario.apellido} (ID: {usuario_id}) por {admin_nombre}')
            
            messages.success(request, f'✓ Cuenta de {usuario.nombre} {usuario.apellido} desbloqueada exitosamente.')
        else:
            messages.warning(request, 'La cuenta no está bloqueada.')
            
    except Usuarios.DoesNotExist:
        logger.warning(f'Intento de desbloquear usuario inexistente: {usuario_id}')
        messages.error(request, 'Usuario no encontrado.')
    except Exception as e:
        logger.error(f'Error al desbloquear cuenta {usuario_id}: {str(e)}')
        messages.error(request, f'Error al desbloquear cuenta: {str(e)}')
    
    return redirect('gestion_seguridad')
```

### URL

En `urls.py`:

```python
path('administrador/seguridad/desbloquear/<int:usuario_id>/', admin_views.desbloquear_cuenta, name='desbloquear_cuenta'),
```

---

## 4. Flujo Completo de Bloqueo y Desbloqueo

### Escenario 1: Desbloqueo Automático

```
1. Usuario intenta login 5+ veces con contraseña incorrecta
   ↓
2. Sistema bloquea la cuenta (cuenta_bloqueada = True)
   ↓
3. Usuario recibe mensaje: "Cuenta bloqueada. Se desbloqueará en 30 minutos..."
   ↓
4. Usuario intenta login después de 30 minutos
   ↓
5. Sistema detecta que pasaron 30 minutos
   ↓
6. Sistema desbloquea automáticamente
   ↓
7. Usuario puede ingresar con contraseña correcta ✓
```

### Escenario 2: Desbloqueo Manual

```
1. Usuario intenta login 5+ veces con contraseña incorrecta
   ↓
2. Sistema bloquea la cuenta
   ↓
3. Usuario contacta al administrador
   ↓
4. Administrador va a Gestionar Seguridad
   ↓
5. Administrador ve la cuenta bloqueada
   ↓
6. Administrador hace clic en "Desbloquear"
   ↓
7. Confirma el desbloqueo
   ↓
8. Sistema desbloquea inmediatamente
   ↓
9. Usuario recibe confirmación de desbloqueó ✓
   ↓
10. Usuario puede ingresar con contraseña correcta ✓
```

---

## 5. Mensajes del Sistema

### Para el usuario (login)

| Situación | Mensaje |
|-----------|---------|
| 1-4 intentos fallidos | `"Matrícula o contraseña incorrectos. Intentos restantes: X"` |
| 5+ intentos | `"Cuenta bloqueada por demasiados intentos fallidos. Contacte al administrador."` |
| Cuenta bloqueada < 30 min | `"Cuenta bloqueada. Se desbloqueará en X minutos o contacte al administrador."` |
| Cuenta bloqueada ≥ 30 min | Desbloquea automáticamente |

### Para el administrador (Gestionar Seguridad)

- ✓ Desbloqueo exitoso: `"✓ Cuenta de [Nombre] desbloqueada exitosamente."`
- ⚠ No está bloqueada: `"La cuenta no está bloqueada."`
- ✗ Cuenta no existe: `"Usuario no encontrado."`
- ✗ Error: `"Error al desbloquear cuenta: [detalle]"`

---

## 6. Auditoría y Logs

### Información registrada

Cada desbloqueo manual genera un registro en los logs:

```
logger.info(f'Cuenta desbloqueada: [Usuario] (ID: [X]) por [Admin]')
```

### Dónde ver

- **Archivo de logs**: Django logs (configurado en `settings.py`)
- **Dashboard Seguridad**: 
  - Tabla "Cuentas Bloqueadas"
  - Tabla "Auditoría de Calificaciones" (cambios de estado)

---

## 7. Estadísticas en Dashboard

En **Gestionar Seguridad** se muestra:

| Métrica | Descripción |
|---------|------------|
| Total Usuarios | Cantidad total de usuarios del sistema |
| Usuarios Activos | Usuarios que han accedido al menos una vez |
| Cuentas Bloqueadas | Cantidad actual de cuentas bloqueadas |
| Últimos Accesos | Últimos 10 accesos exitosos |
| Cuentas Bloqueadas | Listado detallado con botones de desbloqueo |

---

## 8. Recuperación de Contraseña vs Desbloqueo

⚠️ **Importante**: Estos son mecanismos diferentes:

| Feature | Propósito | Acción |
|---------|-----------|--------|
| **Bloqueo/Desbloqueo** | Seguridad por intentos fallidos | Reset de intentos fallidos |
| **Restablecer Contraseña** | Usuario olvidó contraseña | Generar nueva contraseña temporal |

### Restablecer Contraseña

- Se encuentra en **Gestionar Usuarios**
- Genera una nueva contraseña temporal
- Usuario debe cambiarla al primer login
- NO requiere desbloquear cuenta

---

## 9. Configuración Personalizable

Actualmente los valores son fijos:

- **Intentos máximos antes de bloqueo**: 5
- **Tiempo de desbloqueo automático**: 30 minutos

Para cambiar estos valores:

1. Editar `views.py` (línea ~404 en login_alumno y similares)
2. Cambiar `if usuario.intentos_fallidos_login >= 5:`
3. Cambiar `if tiempo_bloqueo >= timedelta(minutes=30):`

---

## 10. Pruebas

### Probar Desbloqueo Automático

```bash
1. Crear un usuario de prueba
2. Intentar login 5 veces con contraseña incorrecta → Cuenta se bloquea
3. Intentar login entre 0-30 minutos → Muestra tiempo restante
4. Esperar 30+ minutos
5. Intentar login → Desbloquea automáticamente ✓
```

### Probar Desbloqueo Manual

```bash
1. Crear un usuario de prueba
2. Intentar login 5 veces con contraseña incorrecta → Cuenta se bloquea
3. Ir a Panel Administrador → Gestionar Seguridad
4. Buscar cuenta bloqueada
5. Hacer clic en "Desbloquear"
6. Confirmar
7. Ver mensaje de éxito ✓
8. Intentar login con usuario → Funciona ✓
```

---

## 11. Tabla de Estados

```
Estado                          | cuenta_bloqueada | intentos_fallidos | fecha_bloqueo
-----------------------------|------------------|-------------------|---------------
Normal                        | False            | 0                 | NULL
Después de error 1-4          | False            | 1-4               | NULL
Bloqueada por 5+ errores      | True             | 5+                | Timestamp
Después de desbloqueo manual   | False            | 0                 | NULL
Después de auto-desbloqueo    | False            | 0                 | NULL
```

---

## 12. Flujo en la Base de Datos

### Bloqueo

```sql
UPDATE usuarios 
SET cuenta_bloqueada = TRUE, 
    intentos_fallidos_login = 5, 
    fecha_bloqueo = NOW() 
WHERE id_usuario = X;
```

### Desbloqueo Manual/Automático

```sql
UPDATE usuarios 
SET cuenta_bloqueada = FALSE, 
    intentos_fallidos_login = 0, 
    fecha_bloqueo = NULL 
WHERE id_usuario = X;
```

---

## Resumen de Cambios Implementados

✅ **Modelo (models.py)**
- Ya tenía los campos: `cuenta_bloqueada`, `intentos_fallidos_login`, `fecha_bloqueo`

✅ **Views (views.py)**
- Desbloqueo automático en: `login_alumno`, `login_maestro`, `login_administrativo`, `login_administrador`
- Bloqueo después de 5 intentos fallidos

✅ **Admin Views (admin_views.py)**
- Función `desbloquear_cuenta()` mejorada con logging
- Mejor manejo de errores
- Confirmación antes de desbloquear

✅ **URLs (urls.py)**
- Ruta agregada: `/administrador/seguridad/desbloquear/<usuario_id>/`

✅ **Template (GestionSeguridad.html)**
- Botón "Desbloquear" agregado en cada cuenta bloqueada
- Diálogo de confirmación
- Diseño mejorado

---

## Notas Técnicas

- **Thread-safe**: El desbloqueo automático se ejecuta en el contexto de login, no requiere cron job
- **No requiere cambios en la base de datos**: Solo usa los campos ya existentes
- **Auditoría**: Todos los desbloqueos manuales se registran
- **Compatible**: Funciona con todos los roles (alumno, maestro, administrativo, admin)


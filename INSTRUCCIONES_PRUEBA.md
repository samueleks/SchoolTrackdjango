# 🧪 INSTRUCCIONES DE PRUEBA - Desbloqueo de Cuentas

## Prerequisitos

- Servidor Django corriendo
- Usuario administrador activo
- Al menos un usuario de prueba (alumno, maestro, etc.)

---

## PRUEBA 1: Desbloqueo Automático (30 minutos)

### Objetivo
Verificar que una cuenta bloqueada se desbloquea automáticamente después de 30 minutos.

### Pasos

**Paso 1: Bloquear la cuenta**
```
1. Abrir navegador e ir a login (cualquier rol)
2. Ingresar matrícula del usuario de prueba
3. Ingresar contraseña INCORRECTA 5 veces
4. Observar mensaje: "Cuenta bloqueada por demasiados intentos fallidos"
✓ Cuenta está bloqueada
```

**Paso 2: Intentar login dentro de 30 minutos**
```
1. Intentar login nuevamente (en los próximos 30 minutos)
2. Ingresar matrícula
3. Ingresar contraseña (correcta)
4. Observar mensaje: "Cuenta bloqueada. Se desbloqueará en X minutos..."
✓ Se muestra el contador de tiempo
```

**Paso 3: Esperar 30 minutos (o simular el tiempo)**
```
Opción A - Esperar de verdad:
  - Esperar 30 minutos
  - Intentar login nuevamente
  
Opción B - Simular (solo para prueba):
  - Editar BD: UPDATE usuarios SET fecha_bloqueo = NOW() - INTERVAL 31 MINUTES WHERE id_usuario = X;
  - Intentar login
```

**Paso 4: Verificar desbloqueo automático**
```
1. Ingresar matrícula
2. Ingresar contraseña CORRECTA
3. Resultado esperado: ✓ Login exitoso
✓ Prueba 1 APROBADA
```

---

## PRUEBA 2: Desbloqueo Manual (Administrador)

### Objetivo
Verificar que un administrador puede desbloquear una cuenta manualmente desde la interfaz.

### Pasos

**Paso 1: Bloquear la cuenta**
```
1. Abrir navegador e ir a login (cualquier rol)
2. Ingresar matrícula del usuario de prueba
3. Ingresar contraseña INCORRECTA 5 veces
4. Observar: Cuenta bloqueada
✓ Cuenta está bloqueada
```

**Paso 2: Acceder a Gestionar Seguridad (como Admin)**
```
1. Iniciar sesión como administrador
2. Hacer clic en "Gestionar Seguridad" (menú lateral)
3. Resultado: Se abre el dashboard de seguridad
✓ Se ve el dashboard
```

**Paso 3: Localizar la cuenta bloqueada**
```
1. En la sección "Cuentas Bloqueadas" (rojo, a la derecha)
2. Buscar el nombre del usuario de prueba
3. Verificar:
   - Nombre y apellido visible
   - Matrícula visible
   - "Bloqueada: [fecha/hora]"
   - Número de intentos
   - Botón verde "Desbloquear"
✓ Cuenta bloqueada encontrada
```

**Paso 4: Hacer clic en Desbloquear**
```
1. Hacer clic en el botón "Desbloquear"
2. Resultado: Aparece diálogo de confirmación
3. Diálogo muestra: "¿Desbloquear la cuenta de [Nombre] [Apellido]?"
✓ Diálogo de confirmación aparece
```

**Paso 5: Confirmar el desbloqueo**
```
1. Hacer clic en "Aceptar" (OK)
2. Resultado: Diálogo desaparece
3. Se muestra mensaje verde: 
   "✓ Cuenta de [Nombre] desbloqueada exitosamente."
✓ Mensaje de éxito
```

**Paso 6: Verificar que la cuenta ya no está bloqueada**
```
1. Recargar la página (F5)
2. La cuenta debe DESAPARECER de "Cuentas Bloqueadas"
3. Si no hay más cuentas bloqueadas, se muestra:
   "No hay cuentas bloqueadas"
✓ Cuenta desapareció de la lista
```

**Paso 7: Intentar login con el usuario desbloqueado**
```
1. Cerrar sesión de admin (o abrir navegador anónimo)
2. Ir a login del rol correspondiente
3. Ingresar matrícula del usuario
4. Ingresar contraseña CORRECTA
5. Resultado esperado: ✓ Login exitoso
✓ Prueba 2 APROBADA
```

---

## PRUEBA 3: Auditoría y Logging

### Objetivo
Verificar que se registra la auditoría de desbloqueos.

### Pasos

**Paso 1: Hacer un desbloqueo manual (ver Prueba 2)**

**Paso 2: Verificar logs**
```
1. Abrir terminal/consola del servidor Django
2. Buscar mensaje como:
   "Cuenta desbloqueada: [Usuario] (ID: X) por [Admin]"
3. Si está en archivo de logs:
   Ver en: var/logs/django.log (o según configuración)
✓ Auditoría registrada
```

**Paso 3: Verificar en BD (opcional)**
```
1. Acceder a BD
2. SELECT * FROM usuarios WHERE id_usuario = X;
3. Verificar:
   - cuenta_bloqueada = FALSE
   - intentos_fallidos_login = 0
   - fecha_bloqueo = NULL
✓ Estado correcto en BD
```

---

## PRUEBA 4: Reinicio de Intentos

### Objetivo
Verificar que los intentos fallidos se reinician tras login exitoso.

### Pasos

**Paso 1: Intentar login con contraseña incorrecta 3 veces**
```
1. Ingresar matrícula
2. Ingresar contraseña INCORRECTA
3. Repetir 3 veces
4. Observar: "Intentos restantes: 2" (después del 3ro)
✓ Se incrementa el contador
```

**Paso 2: Intentar login con contraseña CORRECTA**
```
1. Ingresar matrícula
2. Ingresar contraseña CORRECTA
3. Resultado: ✓ Login exitoso
✓ Sesión iniciada
```

**Paso 3: Cerrar sesión y repetir**
```
1. Cerrar sesión (logout)
2. Intentar login nuevamente
3. Ingresar contraseña INCORRECTA
4. Observar: "Intentos restantes: 4"
   (No 3, porque se reinició a 0 tras login exitoso)
✓ Prueba 4 APROBADA
```

---

## PRUEBA 5: Protección de Seguridad

### Objetivo
Verificar que solo administradores pueden desbloquear.

### Pasos

**Paso 1: Intentar acceder a URL de desbloqueo como no-admin**
```
1. Iniciar sesión como usuario normal (alumno, maestro, etc.)
2. Ir a URL: /administrador/seguridad/
3. Resultado esperado: ✓ Redirección a selector_rol
   (No acceso permitido)
✓ Seguridad verificada
```

**Paso 2: Intentar POST manual a desbloquear (sin sesión admin)**
```
1. Sin sesión iniciada
2. Hacer POST a: /administrador/seguridad/desbloquear/1/
3. Resultado esperado: ✓ Redirección a login
✓ Protección verificada
```

---

## PRUEBA 6: Mensajes en Interfaz

### Objetivo
Verificar que todos los mensajes se muestran correctamente.

### Pasos

**Casos de Bloqueo**

```
Escenario 1: Contraseña incorrecta (1er intento)
  Mensaje esperado: "Matrícula o contraseña incorrectos. Intentos restantes: 4"
  ✓ Aparece contador

Escenario 2: Contraseña incorrecta (5to intento)
  Mensaje esperado: "Cuenta bloqueada por demasiados intentos fallidos. Contacte al administrador."
  ✓ Aparece mensaje de bloqueo

Escenario 3: Cuenta bloqueada (< 30 min)
  Mensaje esperado: "Cuenta bloqueada. Se desbloqueará en X minutos o contacte al administrador."
  ✓ Aparece tiempo restante

Escenario 4: Admin desbloquea
  Mensaje esperado: "✓ Cuenta de [Nombre] desbloqueada exitosamente."
  ✓ Aparece mensaje de éxito verde
```

---

## Checklist de Verificación

Marca con ✓ cada paso completado:

### Prueba 1: Desbloqueo Automático
- [ ] Cuenta se bloquea después de 5 intentos
- [ ] Mensaje de contador aparece
- [ ] Se desbloquea automáticamente después de 30 minutos
- [ ] Login exitoso tras desbloqueo automático

### Prueba 2: Desbloqueo Manual
- [ ] Se ve el dashboard de Gestionar Seguridad
- [ ] Se ven las cuentas bloqueadas
- [ ] Se puede hacer clic en "Desbloquear"
- [ ] Aparece diálogo de confirmación
- [ ] Se desbloquea tras confirmar
- [ ] Cuenta desaparece de la lista
- [ ] Login exitoso tras desbloqueo manual

### Prueba 3: Auditoría
- [ ] Se registra en logs del servidor
- [ ] Se actualiza correctamente en BD

### Prueba 4: Reinicio de Intentos
- [ ] Contador se reinicia tras login exitoso
- [ ] No se bloquea si hay solo 2-3 intentos fallidos

### Prueba 5: Seguridad
- [ ] Solo admins pueden ver Gestionar Seguridad
- [ ] No se puede desbloquear sin sesión admin

### Prueba 6: Mensajes
- [ ] Todos los mensajes son claros y precisos
- [ ] Colores: rojo para error, verde para éxito

---

## Resolución de Problemas

### ¿La cuenta no se desbloquea automáticamente?
- Verificar que han pasado 30 minutos exactamente
- Verificar timezone de servidor y cliente
- Ver logs: `python manage.py tail logs`

### ¿El botón Desbloquear no aparece?
- Verificar que hay cuentas bloqueadas
- Recargar la página (F5)
- Limpiar caché del navegador

### ¿Mensaje de error en desbloqueo?
- Verificar logs del servidor
- Asegurar que la BD está accesible
- Verficar que el usuario existe

### ¿No se ve Gestionar Seguridad?
- Verificar que está autenticado como admin
- Verificar que el menú lateral está completo
- Limpiar caché del navegador

---

**Fecha de prueba:** 2026-05-23
**Versión testeada:** 1.0
**Estado:** Listo para validación

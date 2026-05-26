import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SchoolTrackdjango.settings')
import django
django.setup()

from login.models import Usuarios, Administrador

# Crear usuario administrador
usuario = Usuarios(
    nombre='Administrador',
    apellido='Principal',
    rol='admin',
    contrasena='admin123'  # Se encriptará automáticamente
)
usuario.save()

# Crear registro en tabla Administrador
administrador = Administrador(
    id_usuario=usuario,
    puesto='Director',
    nivel_prioridad=1
)
administrador.save()

print(f"Administrador creado exitosamente:")
print(f"Matrícula: {usuario.matricula}")
print(f"Contraseña: admin123")
print(f"Nombre: {usuario.nombre} {usuario.apellido}")
print(f"Rol: {usuario.rol}")
print(f"Puesto: {administrador.puesto}")
print(f"Nivel Prioridad: {administrador.nivel_prioridad}")

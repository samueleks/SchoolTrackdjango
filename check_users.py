import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SchoolTrackdjango.settings')
django.setup()

from login.models import Usuarios, Alumnos, Maestros, Administrativos, Administrador

print("=== USUARIOS EN BASE DE DATOS ===")
print("\n--- Tabla Usuarios ---")
usuarios = Usuarios.objects.all()
print(f"Total usuarios: {usuarios.count()}")
for u in usuarios:
    print(f"  - {u.matricula} | {u.nombre} {u.apellido} | Rol: {u.rol}")

print("\n--- Tabla Alumnos ---")
alumnos = Alumnos.objects.all()
print(f"Total alumnos: {alumnos.count()}")
for a in alumnos:
    print(f"  - ID: {a.id_usuario_id}")

print("\n--- Tabla Maestros ---")
maestros = Maestros.objects.all()
print(f"Total maestros: {maestros.count()}")
for m in maestros:
    print(f"  - ID: {m.id_usuario_id}")

print("\n--- Tabla Administrativos ---")
administrativos = Administrativos.objects.all()
print(f"Total administrativos: {administrativos.count()}")
for a in administrativos:
    print(f"  - ID: {a.id_usuario_id}")

print("\n--- Tabla Administrador ---")
administradores = Administrador.objects.all()
print(f"Total administradores: {administradores.count()}")
for a in administradores:
    print(f"  - ID: {a.id_usuario_id}")

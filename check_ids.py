import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SchoolTrackdjango.settings')
django.setup()

from login.models import Usuarios, Alumnos, Maestros, Administrativos, Administrador

print("=== VERIFICACIÓN DE IDs ===")
print("\n--- Usuarios y sus IDs ---")
usuarios = Usuarios.objects.all()
for u in usuarios:
    print(f"ID: {u.id_usuario} | Matrícula: {u.matricula} | Rol: {u.rol}")

print("\n--- Verificación de coincidencias ---")
print("\nAlumnos:")
alumnos = Alumnos.objects.all()
for a in alumnos:
    try:
        usuario = Usuarios.objects.get(id_usuario=a.id_usuario_id)
        print(f"  [OK] Alumno ID {a.id_usuario_id} -> Usuario {usuario.matricula} ({usuario.rol})")
    except Usuarios.DoesNotExist:
        print(f"  [ERROR] Alumno ID {a.id_usuario_id} -> NO EXISTE en Usuarios")

print("\nMaestros:")
maestros = Maestros.objects.all()
for m in maestros:
    try:
        usuario = Usuarios.objects.get(id_usuario=m.id_usuario_id)
        print(f"  [OK] Maestro ID {m.id_usuario_id} -> Usuario {usuario.matricula} ({usuario.rol})")
    except Usuarios.DoesNotExist:
        print(f"  [ERROR] Maestro ID {m.id_usuario_id} -> NO EXISTE en Usuarios")

print("\nAdministrativos:")
administrativos = Administrativos.objects.all()
for a in administrativos:
    try:
        usuario = Usuarios.objects.get(id_usuario=a.id_usuario_id)
        print(f"  [OK] Administrativo ID {a.id_usuario_id} -> Usuario {usuario.matricula} ({usuario.rol})")
    except Usuarios.DoesNotExist:
        print(f"  [ERROR] Administrativo ID {a.id_usuario_id} -> NO EXISTE en Usuarios")

print("\nAdministrador:")
administradores = Administrador.objects.all()
for a in administradores:
    try:
        usuario = Usuarios.objects.get(id_usuario=a.id_usuario_id)
        print(f"  [OK] Administrador ID {a.id_usuario_id} -> Usuario {usuario.matricula} ({usuario.rol})")
    except Usuarios.DoesNotExist:
        print(f"  [ERROR] Administrador ID {a.id_usuario_id} -> NO EXISTE en Usuarios")

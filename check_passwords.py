import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SchoolTrackdjango.settings')
django.setup()

from login.models import Usuarios

print("=== VERIFICACIÓN DE CONTRASEÑAS ===")
print("\nUsuarios y estado de contraseña:\n")

usuarios = Usuarios.objects.all()
for u in usuarios:
    print(f"Matrícula: {u.matricula}")
    print(f"  Nombre: {u.nombre} {u.apellido}")
    print(f"  Rol: {u.rol}")
    print(f"  Contraseña encriptada: {u.contrasena[:50]}...")
    print(f"  Empieza con pbkdf2: {u.contrasena.startswith('pbkdf2_sha256$')}")
    print(f"  Cuenta bloqueada: {u.cuenta_bloqueada}")
    print(f"  Intentos fallidos: {u.intentos_fallidos_login}")
    print()

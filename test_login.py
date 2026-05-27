import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'SchoolTrackdjango.settings')
django.setup()

from login.models import Usuarios

print("=== PRUEBA DE LOGIN ===")
print("\nUsuarios y contraseñas de prueba:\n")

usuarios = Usuarios.objects.all()
for u in usuarios:
    print(f"Matricula: {u.matricula}")
    print(f"  Nombre: {u.nombre} {u.apellido}")
    print(f"  Rol: {u.rol}")
    print(f"  Prueba con contraseña: '123456'")
    if u.verificar_contrasena('123456'):
        print(f"  [OK] Contrasena 123456 funciona")
    else:
        print(f"  [ERROR] Contrasena 123456 NO funciona")
    print()

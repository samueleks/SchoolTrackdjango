"""Middleware de la app login."""
from django.shortcuts import redirect

_RUTAS_PUBLICAS = (
    '/',
    '/login/',
    '/recuperar-contrasena',
    '/cambiar-contrasena/',
    '/logout/',
    '/static/',
    '/media/',
    '/admin/',
)


def _ruta_publica(path: str) -> bool:
    if path == '/':
        return True
    return any(path.startswith(prefijo) for prefijo in _RUTAS_PUBLICAS if prefijo != '/')


class ContrasenaTemporalMiddleware:
    """Redirige a cambio de contraseña si la sesión tiene contrasena_temporal=True."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _ruta_publica(request.path):
            usuario_id = request.session.get('usuario_id')
            if usuario_id:
                from .models import Usuarios

                try:
                    pk = int(usuario_id)
                except (TypeError, ValueError):
                    pk = None
                if pk and Usuarios.objects.filter(pk=pk, contrasena_temporal=True).exists():
                    return redirect('cambiar_contrasena_temporal')
        return self.get_response(request)

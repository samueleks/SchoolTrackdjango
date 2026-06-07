import logging

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .password_utils import validar_contrasena_usuario

logger = logging.getLogger(__name__)
from .recuperacion_contrasena import (
    ETIQUETA_ROL,
    LOGIN_POR_ROL,
    MENSAJE_SOLICITUD_GENERICO,
    aplicar_nueva_contrasena,
    email_esta_configurado,
    obtener_token_vigente,
    solicitar_recuperacion_contrasena,
)


def _rol_desde_request(request) -> str:
    rol = (request.GET.get('rol') or request.POST.get('rol') or '').strip().lower()
    if rol in LOGIN_POR_ROL:
        return rol
    return ''


def _contexto_rol(rol: str) -> dict:
    login_url = reverse(LOGIN_POR_ROL[rol]) if rol in LOGIN_POR_ROL else reverse('selector_rol')
    return {
        'rol': rol,
        'rol_etiqueta': ETIQUETA_ROL.get(rol, ''),
        'login_url': login_url,
        'selector_url': reverse('selector_rol'),
    }


@require_http_methods(['GET', 'POST'])
def recuperar_contrasena_solicitud(request):
    rol = _rol_desde_request(request)
    error_msg = None
    matricula_valor = ''
    correo_valor = ''

    if request.method == 'POST':
        matricula_valor = request.POST.get('matricula', '').strip()
        correo_valor = request.POST.get('correo', '').strip()

        if not matricula_valor or not correo_valor:
            error_msg = 'Ingresa tu matrícula y tu correo institucional registrado.'
        else:
            try:
                enviado, mensaje = solicitar_recuperacion_contrasena(
                    request=request,
                    matricula=matricula_valor,
                    correo=correo_valor,
                )
            except Exception:
                logger.exception('Error inesperado en recuperación de contraseña')
                error_msg = (
                    'Ocurrió un error al procesar tu solicitud. '
                    'Intenta más tarde o contacta al administrador.'
                )
            else:
                if enviado:
                    messages.success(request, mensaje)
                else:
                    messages.warning(request, mensaje)
                destino = reverse('recuperar_contrasena_enviado')
                if rol:
                    destino = f'{destino}?rol={rol}'
                return redirect(destino)

    context = {
        **_contexto_rol(rol),
        'error_msg': error_msg,
        'matricula_valor': matricula_valor,
        'correo_valor': correo_valor,
        'email_configurado': email_esta_configurado(),
    }
    return render(request, 'recuperar_contrasena_solicitud.html', context)


def recuperar_contrasena_enviado(request):
    rol = _rol_desde_request(request)
    context = {
        **_contexto_rol(rol),
        'mensaje': MENSAJE_SOLICITUD_GENERICO,
    }
    return render(request, 'recuperar_contrasena_enviado.html', context)


@require_http_methods(['GET', 'POST'])
def recuperar_contrasena_establecer(request, token: str):
    registro = obtener_token_vigente(token)
    if not registro:
        messages.error(
            request,
            'Este enlace no es válido o ya expiró. Solicita uno nuevo desde el inicio de sesión.',
        )
        return redirect('recuperar_contrasena_solicitud')

    usuario = registro.id_usuario
    rol = usuario.rol
    error_msg = None
    errores_lista = None

    if request.method == 'POST':
        nueva = request.POST.get('nueva_contrasena', '').strip()
        confirmar = request.POST.get('confirmar_contrasena', '').strip()

        if not nueva or not confirmar:
            error_msg = 'Completa ambos campos de contraseña.'
        elif nueva != confirmar:
            error_msg = 'Las contraseñas no coinciden.'
        else:
            valida, errores = validar_contrasena_usuario(nueva, usuario)
            if not valida:
                errores_lista = errores
                error_msg = errores[0]
            else:
                ok, mensaje, _ = aplicar_nueva_contrasena(token, nueva)
                if ok:
                    messages.success(request, mensaje)
                    login_name = LOGIN_POR_ROL.get(rol, 'selector_rol')
                    return redirect(login_name)
                error_msg = mensaje

    context = {
        **_contexto_rol(rol),
        'usuario_nombre': f'{usuario.nombre} {usuario.apellido}'.strip(),
        'usuario_matricula': usuario.matricula,
        'error_msg': error_msg,
        'errores_lista': errores_lista,
        'token': token,
    }
    return render(request, 'recuperar_contrasena_establecer.html', context)

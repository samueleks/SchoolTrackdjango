"""
Recuperación de contraseña por correo electrónico (producción).
Flujo: matrícula + correo registrado → token de un solo uso → enlace por email.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import DatosPersonales, TokenRecuperacionContrasena, Usuarios

logger = logging.getLogger(__name__)

MENSAJE_SOLICITUD_GENERICO = (
    'Si la matrícula y el correo coinciden con un usuario registrado, '
    'recibirás un enlace para restablecer tu contraseña en los próximos minutos. '
    'Revisa también la carpeta de spam.'
)

LOGIN_POR_ROL = {
    'alumno': 'login_alumno',
    'maestro': 'login_maestro',
    'administrativo': 'login_administrativo',
    'admin': 'login_administrador',
}

ETIQUETA_ROL = {
    'alumno': 'Alumno',
    'maestro': 'Maestro',
    'administrativo': 'Administrativo',
    'admin': 'Administrador',
}


def _normalizar_correo(correo: str) -> str:
    return (correo or '').strip().lower()


def _hash_token(token_plano: str) -> str:
    return hashlib.sha256(token_plano.encode('utf-8')).hexdigest()


def email_esta_configurado() -> bool:
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    if 'console' in backend:
        return True
    if _debe_usar_resend_api():
        return True
    return bool(getattr(settings, 'EMAIL_HOST', ''))


def _debe_usar_resend_api() -> bool:
    host = (getattr(settings, 'EMAIL_HOST', '') or '').strip().lower()
    api_key = (getattr(settings, 'EMAIL_HOST_PASSWORD', '') or '').strip()
    return 'resend.com' in host and api_key.startswith('re_')


def _clave_limite_ip(ip: str) -> str:
    return f'recuperacion_pwd:ip:{ip or "unknown"}'


def _clave_limite_correo(correo: str) -> str:
    return f'recuperacion_pwd:correo:{_normalizar_correo(correo)}'


def _incrementar_limite(clave: str, maximo: int, segundos: int = 3600) -> bool:
    """Retorna True si aún puede continuar; False si excedió el límite."""
    actual = cache.get(clave, 0) or 0
    if actual >= maximo:
        return False
    try:
        if actual == 0:
            cache.set(clave, 1, segundos)
        else:
            cache.incr(clave)
    except ValueError:
        cache.set(clave, 1, segundos)
    return True


def buscar_usuario_por_matricula_y_correo(
    matricula: str,
    correo: str,
    *,
    rol: str | None = None,
) -> Usuarios | None:
    matricula_limpia = (matricula or '').strip()
    correo_limpio = _normalizar_correo(correo)
    if not matricula_limpia or not correo_limpio:
        return None

    usuario = Usuarios.objects.filter(matricula__iexact=matricula_limpia).first()
    if not usuario:
        return None

    if rol and usuario.rol != rol:
        logger.info(
            'Recuperación: rol no coincide para %s (portal %s, cuenta %s)',
            matricula_limpia,
            rol,
            usuario.rol,
        )
        return None

    try:
        datos = DatosPersonales.objects.get(id_usuario=usuario)
    except DatosPersonales.DoesNotExist:
        return None

    if not datos.correo_inst:
        return None
    if _normalizar_correo(datos.correo_inst) != correo_limpio:
        return None

    return usuario


def _construir_url_recuperacion(request, token_plano: str) -> str:
    ruta = reverse('recuperar_contrasena_establecer', kwargs={'token': token_plano})
    base = getattr(settings, 'SCHOOLTRACK_BASE_URL', '').strip()
    if base:
        return f'{base.rstrip("/")}{ruta}'
    return request.build_absolute_uri(ruta)


def _enviar_via_resend_api(
    *,
    destinatario: str,
    asunto: str,
    texto: str,
    html: str,
) -> None:
    api_key = settings.EMAIL_HOST_PASSWORD.strip()
    payload = json.dumps({
        'from': settings.DEFAULT_FROM_EMAIL,
        'to': [destinatario],
        'subject': asunto,
        'html': html,
        'text': texto,
    }).encode('utf-8')
    solicitud = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            # Resend/Cloudflare rechaza peticiones sin User-Agent (error 1010).
            'User-Agent': 'SchoolTrack/1.0 (Django; password-recovery)',
        },
        method='POST',
    )
    timeout = int(getattr(settings, 'EMAIL_TIMEOUT', 15))
    try:
        with urllib.request.urlopen(solicitud, timeout=timeout) as respuesta:
            respuesta.read()
    except urllib.error.HTTPError as exc:
        cuerpo = exc.read().decode('utf-8', errors='replace')
        logger.error('Resend API HTTP %s: %s', exc.code, cuerpo)
        raise RuntimeError(f'Resend rechazó el envío ({exc.code})') from exc
    except urllib.error.URLError as exc:
        logger.error('Resend API sin conexión: %s', exc)
        raise RuntimeError('No se pudo conectar con Resend') from exc


def _enviar_correo_recuperacion(
    *,
    destinatario: str,
    nombre_usuario: str,
    matricula: str,
    enlace: str,
    horas_validez: int,
) -> None:
    asunto = 'SchoolTrack — Restablecer tu contraseña'
    texto = f"""Hola {nombre_usuario},

Recibimos una solicitud para restablecer la contraseña de tu cuenta SchoolTrack.

Matrícula: {matricula}

Abre este enlace (válido por {horas_validez} hora(s)):
{enlace}

Si no solicitaste este cambio, ignora este correo. Tu contraseña actual seguirá funcionando.

— SchoolTrack
"""
    html = f"""<!DOCTYPE html>
<html lang="es">
<body style="font-family:Arial,sans-serif;line-height:1.5;color:#1f2937;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#2b63d9;margin:0 0 8px;">SchoolTrack</h2>
  <p style="color:#6b7280;font-size:13px;margin:0 0 24px;">Restablecer contraseña</p>
  <p>Hola <strong>{nombre_usuario}</strong>,</p>
  <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta.</p>
  <p style="font-size:14px;">Matrícula: <strong>{matricula}</strong></p>
  <p style="margin:28px 0;">
    <a href="{enlace}" style="background:#2b63d9;color:#fff;text-decoration:none;padding:14px 28px;border-radius:10px;font-weight:bold;display:inline-block;">
      Restablecer contraseña
    </a>
  </p>
  <p style="font-size:13px;color:#6b7280;">El enlace expira en <strong>{horas_validez} hora(s)</strong>.</p>
  <p style="font-size:13px;color:#6b7280;">Si no solicitaste este cambio, ignora este mensaje.</p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0;">
  <p style="font-size:11px;color:#9ca3af;">Si el botón no funciona, copia y pega esta URL en tu navegador:<br>{enlace}</p>
</body>
</html>"""

    if _debe_usar_resend_api():
        _enviar_via_resend_api(
            destinatario=destinatario,
            asunto=asunto,
            texto=texto,
            html=html,
        )
        return

    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[destinatario],
    )
    mensaje.attach_alternative(html, 'text/html')
    mensaje.send(fail_silently=False)


def _preparar_token_recuperacion(
    *,
    request,
    usuario: Usuarios,
    ip: str,
) -> tuple[str, DatosPersonales, str, str, int]:
    """Crea el token en BD (transacción corta, sin SMTP)."""
    horas = getattr(settings, 'RECUPERACION_CONTRASENA_HORAS_VALIDEZ', 1)
    token_plano = secrets.token_urlsafe(48)
    token_hash = _hash_token(token_plano)
    ahora = timezone.now()

    with transaction.atomic():
        TokenRecuperacionContrasena.objects.filter(
            id_usuario=usuario,
            usado_en__isnull=True,
            expira_en__gt=ahora,
        ).update(usado_en=ahora)

        datos = DatosPersonales.objects.get(id_usuario=usuario)
        TokenRecuperacionContrasena.objects.create(
            id_usuario=usuario,
            token_hash=token_hash,
            correo_destino=datos.correo_inst,
            expira_en=ahora + timedelta(hours=horas),
            ip_solicitud=ip or None,
        )

    enlace = _construir_url_recuperacion(request, token_plano)
    nombre = f'{usuario.nombre} {usuario.apellido}'.strip()
    return token_plano, datos, enlace, nombre, horas


def solicitar_recuperacion_contrasena(
    *,
    request,
    matricula: str,
    correo: str,
    rol: str = '',
) -> tuple[bool, str]:
    """
    Procesa la solicitud. Siempre retorna mensaje genérico al usuario si hay match o no.
    Retorna (enviado_realmente, mensaje_para_usuario).
    """
    ip = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    if not ip:
        ip = request.META.get('REMOTE_ADDR', '')

    correo_norm = _normalizar_correo(correo)

    if not _incrementar_limite(
        _clave_limite_ip(ip),
        getattr(settings, 'RECUPERACION_CONTRASENA_MAX_POR_IP_HORA', 5),
    ):
        logger.warning('Recuperación bloqueada por límite IP: %s', ip)
        return False, MENSAJE_SOLICITUD_GENERICO

    if correo_norm and not _incrementar_limite(
        _clave_limite_correo(correo_norm),
        getattr(settings, 'RECUPERACION_CONTRASENA_MAX_POR_CORREO_HORA', 3),
    ):
        logger.warning('Recuperación bloqueada por límite correo: %s', correo_norm)
        return False, MENSAJE_SOLICITUD_GENERICO

    rol_esperado = rol if rol in LOGIN_POR_ROL else None
    usuario = buscar_usuario_por_matricula_y_correo(matricula, correo, rol=rol_esperado)
    if not usuario:
        logger.info('Recuperación solicitada sin coincidencia (matrícula/correo/rol).')
        return False, MENSAJE_SOLICITUD_GENERICO

    if usuario.cuenta_bloqueada:
        logger.info('Recuperación ignorada: cuenta bloqueada %s', usuario.matricula)
        return False, MENSAJE_SOLICITUD_GENERICO

    if not email_esta_configurado():
        logger.error(
            'EMAIL_HOST no configurado en producción. No se puede enviar recuperación para %s',
            usuario.matricula,
        )
        return False, (
            'El servicio de correo no está configurado en el servidor. '
            'Contacta al administrador del sistema.'
        )

    try:
        _, datos, enlace, nombre, horas = _preparar_token_recuperacion(
            request=request,
            usuario=usuario,
            ip=ip,
        )
    except Exception:
        logger.exception('Error al crear token de recuperación para %s', usuario.matricula)
        return False, (
            'No pudimos procesar la solicitud en este momento. '
            'Contacta al administrador del sistema.'
        )

    try:
        _enviar_correo_recuperacion(
            destinatario=datos.correo_inst,
            nombre_usuario=nombre,
            matricula=usuario.matricula,
            enlace=enlace,
            horas_validez=horas,
        )
    except Exception as exc:
        logger.exception('Error al enviar correo de recuperación para %s: %s', usuario.matricula, exc)
        return False, (
            'No pudimos enviar el correo en este momento. '
            'Verifica la configuración de correo en el servidor o intenta más tarde.'
        )

    logger.info('Correo de recuperación enviado a %s (%s)', usuario.matricula, datos.correo_inst)
    return True, MENSAJE_SOLICITUD_GENERICO


def obtener_token_vigente(token_plano: str) -> TokenRecuperacionContrasena | None:
    if not token_plano or len(token_plano) < 20:
        return None
    token_hash = _hash_token(token_plano)
    try:
        registro = TokenRecuperacionContrasena.objects.select_related('id_usuario').get(
            token_hash=token_hash,
        )
    except TokenRecuperacionContrasena.DoesNotExist:
        return None
    if not registro.vigente:
        return None
    return registro


@transaction.atomic
def aplicar_nueva_contrasena(token_plano: str, nueva_contrasena: str) -> tuple[bool, str, Usuarios | None]:
    registro = obtener_token_vigente(token_plano)
    if not registro:
        return False, 'El enlace no es válido o ya expiró. Solicita uno nuevo.', None

    usuario = registro.id_usuario
    usuario.contrasena = nueva_contrasena
    usuario.contrasena_temporal = False
    usuario.intentos_fallidos_login = 0
    usuario.cuenta_bloqueada = False
    usuario.fecha_bloqueo = None
    usuario.save()

    ahora = timezone.now()
    registro.usado_en = ahora
    registro.save(update_fields=['usado_en'])

    TokenRecuperacionContrasena.objects.filter(
        id_usuario=usuario,
        usado_en__isnull=True,
    ).update(usado_en=ahora)

    return True, 'Contraseña actualizada correctamente. Ya puedes iniciar sesión.', usuario

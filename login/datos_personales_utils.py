from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import DatosPersonales


def validar_telefono(telefono: str) -> str | None:
    if not telefono:
        return None
    telefono_limpio = telefono.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
    if not telefono_limpio.isdigit():
        return 'El teléfono debe contener solo números (sin guiones ni espacios)'
    if len(telefono_limpio) != 10:
        return 'El teléfono debe tener exactamente 10 dígitos'
    return None


def validar_correo_institucional(correo: str, usuario_id: int | None = None) -> str | None:
    if not correo:
        return None
    try:
        validate_email(correo)
    except ValidationError:
        return 'El formato del correo no es válido.'
    correos = DatosPersonales.objects.filter(correo_inst__iexact=correo)
    if usuario_id is not None:
        correos = correos.exclude(id_usuario_id=usuario_id)
    if correos.exists():
        return 'Este correo electrónico ya está registrado en el sistema.'
    return None


def validar_cp(cp: str, requerido: bool = False) -> str | None:
    cp_limpio = (cp or '').strip()
    if not cp_limpio:
        if requerido:
            return 'Este campo es obligatorio'
        return None
    if not cp_limpio.isdigit():
        return 'El código postal debe contener solo números'
    if len(cp_limpio) != 5:
        return 'El código postal debe tener exactamente 5 dígitos'
    return None


def validar_datos_contacto_perfil(correo: str, telefono: str, usuario_id: int) -> dict[str, str]:
    errores: dict[str, str] = {}
    error_telefono = validar_telefono(telefono)
    if error_telefono:
        errores['telefono'] = error_telefono
    error_correo = validar_correo_institucional(correo, usuario_id)
    if error_correo:
        errores['correo_institucional'] = error_correo
    return errores


def validar_datos_perfil_usuario(
    correo: str,
    telefono: str,
    cp: str,
    usuario_id: int,
) -> dict[str, str]:
    errores = validar_datos_contacto_perfil(correo, telefono, usuario_id)
    error_cp = validar_cp(cp, requerido=True)
    if error_cp:
        errores['cp'] = error_cp
    return errores

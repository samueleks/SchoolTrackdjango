import re
import secrets
import string

# Caracteres que se confunden al copiar o escribir la contraseña temporal.
_AMBIGUOS = frozenset('0O1lI')

# Símbolos seguros para copiar/pegar (sin comillas, barras ni espacios).
_SIMBOLOS_SEGUROS = '!@#$%&*+-=?'

_LONGITUD_TEMPORAL = 16
LONGITUD_MINIMA_USUARIO = 12
LONGITUD_MAXIMA_USUARIO = 128

_CONTRASENAS_COMUNES = frozenset({
    'password', 'password123', 'password1234', '12345678', '123456789',
    '1234567890', 'qwerty123', 'qwertyuiop', 'admin123', 'admin1234',
    'schooltrack', 'contrasena', 'contraseña', 'letmein', 'welcome123',
    'iloveyou', 'abc12345', 'changeme', 'temporal123',
})


def generar_contrasena_temporal(longitud=_LONGITUD_TEMPORAL):
    """
    Genera una contraseña temporal criptográficamente segura.

    Garantiza mayúsculas, minúsculas, dígitos y símbolos, mezclados al azar.
    """
    if longitud < 12:
        longitud = 12

    minusculas = ''.join(c for c in string.ascii_lowercase if c not in _AMBIGUOS)
    mayusculas = ''.join(c for c in string.ascii_uppercase if c not in _AMBIGUOS)
    digitos = ''.join(c for c in string.digits if c not in _AMBIGUOS)
    simbolos = _SIMBOLOS_SEGUROS
    todos = minusculas + mayusculas + digitos + simbolos

    requeridos = [
        secrets.choice(minusculas),
        secrets.choice(minusculas),
        secrets.choice(mayusculas),
        secrets.choice(mayusculas),
        secrets.choice(digitos),
        secrets.choice(digitos),
        secrets.choice(simbolos),
        secrets.choice(simbolos),
    ]

    restantes = longitud - len(requeridos)
    password_chars = requeridos + [secrets.choice(todos) for _ in range(restantes)]

    for i in range(len(password_chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        password_chars[i], password_chars[j] = password_chars[j], password_chars[i]

    return ''.join(password_chars)


def _tiene_simbolo_seguro(contrasena):
    return any(caracter in _SIMBOLOS_SEGUROS for caracter in contrasena)


def validar_contrasena_usuario(contrasena, usuario=None):
    """Valida la contraseña elegida por el usuario. Retorna (ok, lista_errores)."""
    errores = []

    if not contrasena:
        return False, ['La contraseña no puede estar vacía.']

    if len(contrasena) < LONGITUD_MINIMA_USUARIO:
        errores.append(f'Debe tener al menos {LONGITUD_MINIMA_USUARIO} caracteres.')
    if len(contrasena) > LONGITUD_MAXIMA_USUARIO:
        errores.append(f'No puede exceder {LONGITUD_MAXIMA_USUARIO} caracteres.')
    if not re.search(r'[A-Z]', contrasena):
        errores.append('Debe incluir al menos una letra mayúscula.')
    if not re.search(r'[a-z]', contrasena):
        errores.append('Debe incluir al menos una letra minúscula.')
    if not re.search(r'\d', contrasena):
        errores.append('Debe incluir al menos un número.')
    if not _tiene_simbolo_seguro(contrasena):
        errores.append('Debe incluir al menos un símbolo (!@#$%&*+-=?).')
    if contrasena.lower() in _CONTRASENAS_COMUNES:
        errores.append('Esa contraseña es demasiado común. Elige otra.')

    if usuario:
        datos_personales = [
            getattr(usuario, 'nombre', '') or '',
            getattr(usuario, 'apellido', '') or '',
            getattr(usuario, 'matricula', '') or '',
        ]
        contrasena_lower = contrasena.lower()
        for dato in datos_personales:
            fragmento = dato.strip().lower()
            if len(fragmento) >= 3 and fragmento in contrasena_lower:
                errores.append('No puede contener tu nombre, apellido o matrícula.')
                break

    return len(errores) == 0, errores

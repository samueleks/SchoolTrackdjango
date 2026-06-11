"""Configuración opcional para tests (hasher más rápido)."""

from .settings import *  # noqa: F403

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

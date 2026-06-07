"""Sincronización de secuencias PostgreSQL para AutoField."""
from __future__ import annotations

from django.db import connection
from django.db.models import Max, Model


def info_secuencia_postgresql(model: type[Model]) -> tuple[str | None, int]:
    """Nombre de secuencia y valor que tomaría el próximo INSERT."""
    if connection.vendor != 'postgresql':
        return None, 0

    table = model._meta.db_table
    column = model._meta.pk.column
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT pg_get_serial_sequence(%s, %s)',
            [table, column],
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return None, 0
        sequence_name = row[0]
        cursor.execute(f'SELECT last_value, is_called FROM {sequence_name}')
        last_value, is_called = cursor.fetchone()
        return sequence_name, int(last_value + (1 if is_called else 0))


def asegurar_secuencia_postgresql(model: type[Model]) -> None:
    """
    Adelanta la secuencia si va rezagada respecto al MAX(id) existente.
    No la retrocede: los IDs eliminados no se reutilizan.
    """
    if connection.vendor != 'postgresql':
        return

    pk_field = model._meta.pk.name
    max_id = model.objects.aggregate(m=Max(pk_field))['m'] or 0
    sequence_name, proximo_secuencia = info_secuencia_postgresql(model)
    if not sequence_name or proximo_secuencia > max_id:
        return

    with connection.cursor() as cursor:
        cursor.execute('SELECT setval(%s, %s, true)', [sequence_name, max_id])


def avanzar_secuencia_tras_eliminar(model: type[Model], id_eliminado: int) -> None:
    """Evita reasignar el ID del registro que acaba de borrarse."""
    if connection.vendor != 'postgresql':
        return

    pk_field = model._meta.pk.name
    max_id = model.objects.aggregate(m=Max(pk_field))['m'] or 0
    if id_eliminado <= max_id:
        return

    sequence_name, proximo_secuencia = info_secuencia_postgresql(model)
    if not sequence_name or proximo_secuencia > id_eliminado:
        return

    with connection.cursor() as cursor:
        cursor.execute('SELECT setval(%s, %s, true)', [sequence_name, id_eliminado])

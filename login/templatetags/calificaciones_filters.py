from django import template

register = template.Library()


@register.filter
def has_unit_below_70(unidades):
    """
    Verifica si alguna unidad en la lista es menor a 70.
    Retorna True si hay alguna unidad < 70, False en caso contrario.
    """
    for unidad in unidades:
        if unidad != '—':
            try:
                valor = float(unidad)
                if valor < 70:
                    return True
            except (ValueError, TypeError):
                pass
    return False


@register.filter
def get_promedio_display(promedio, unidades):
    """
    Retorna 'NA' si alguna unidad es menor a 70, en caso contrario retorna el promedio.
    """
    for unidad in unidades:
        if unidad != '—':
            try:
                valor = float(unidad)
                if valor < 70:
                    return 'NA'
            except (ValueError, TypeError):
                pass
    return promedio


@register.simple_tag
def calculate_general_average(calificaciones_rows):
    """
    Calcula el promedio general ignorando las materias con 'NA'.
    Solo promedia las materias que tienen promedio numérico válido.
    """
    suma_promedios = 0
    contador_validos = 0

    for row in calificaciones_rows:
        tiene_unidad_menor_70 = False

        # Verificar si alguna unidad es menor a 70
        for unidad in row.get('unidades', []):
            if unidad != '—':
                try:
                    valor = float(unidad)
                    if valor < 70:
                        tiene_unidad_menor_70 = True
                        break
                except (ValueError, TypeError):
                    pass

        # Si no tiene unidades menores a 70, sumar el promedio
        if not tiene_unidad_menor_70:
            try:
                promedio_val = float(row.get('promedio', 0))
                suma_promedios += promedio_val
                contador_validos += 1
            except (ValueError, TypeError):
                pass

    # Calcular el promedio general
    if contador_validos > 0:
        promedio_final = suma_promedios / contador_validos
        return round(promedio_final, 2)
    else:
        return 0

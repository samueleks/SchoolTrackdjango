from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """
    Obtiene un valor de un diccionario de forma segura en templates.
    Uso: {{ dict|get_item:'key' }}
    """
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None

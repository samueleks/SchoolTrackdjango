import re
from datetime import date, datetime

PERIODO_PARTE_MAP = {
    'A': 0,
    'B': 1,
    '1': 0,
    '2': 1,
}


def periodo_desde_mes(mes: int) -> str:
    """Periodo escolar según el mes de inicio (misma regla que _periodo_actual)."""
    if mes <= 6:
        return 'A'
    if mes >= 8:
        return 'B'
    # Julio queda como A para no romper el flujo de captura.
    return 'A'


def periodo_esperado_por_fecha(fecha: date | datetime) -> str:
    """Devuelve 'A' o 'B' según el mes de la fecha de inicio del ciclo."""
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    return periodo_desde_mes(fecha.month)


def validar_periodo_coherente_con_fecha(periodo: str, fecha_inicio: date) -> None:
    """Lanza ValueError si el periodo no coincide con la fecha de inicio."""
    esperado = periodo_esperado_por_fecha(fecha_inicio)
    if periodo != esperado:
        raise ValueError(
            f'El periodo debe ser {esperado} según la fecha de inicio '
            f'({fecha_inicio.strftime("%d/%m/%Y")}), no {periodo}'
        )


def parse_periodo(periodo: str) -> tuple[int, int] | None:
    """Convierte un periodo como '2026-A' en (año, índice_parcial)."""
    if not periodo:
        return None
    coincidencia = re.match(r'^(\d{4})-([AB12])$', periodo.strip().upper())
    if not coincidencia:
        return None
    año = int(coincidencia.group(1))
    parte = PERIODO_PARTE_MAP.get(coincidencia.group(2))
    if parte is None:
        return None
    return año, parte


def periodo_a_indice(periodo: str) -> int | None:
    """Asigna un índice ordenable a cada periodo escolar (A=0, B=1 por año)."""
    parsed = parse_periodo(periodo)
    if not parsed:
        return None
    año, parte = parsed
    return año * 2 + parte


def calcular_semestre_desde_ingreso(periodo_ingreso: str, periodo_actual: str) -> int | None:
    """
    Calcula el semestre actual según el periodo de ingreso y el periodo vigente.
    Cada periodo (A o B) cuenta como un semestre; el periodo de ingreso es el 1.º.
    """
    indice_ingreso = periodo_a_indice(periodo_ingreso)
    indice_actual = periodo_a_indice(periodo_actual)
    if indice_ingreso is None or indice_actual is None:
        return None
    if indice_actual < indice_ingreso:
        return 1
    return max(1, min(12, indice_actual - indice_ingreso + 1))


def resolver_semestre_alumno(
    periodo_ingreso: str,
    semestre_guardado: int | None,
    periodo_actual: str,
) -> int:
    """Usa el semestre calculado por periodo de ingreso; si no puede, el guardado en BDD."""
    calculado = calcular_semestre_desde_ingreso(periodo_ingreso, periodo_actual)
    if calculado is not None:
        return calculado
    if semestre_guardado is not None:
        return semestre_guardado
    return 1

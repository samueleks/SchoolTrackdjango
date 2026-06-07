"""Generación de PDF de horario semanal por grupo (administrativo)."""
from __future__ import annotations

from datetime import datetime, timedelta

from fpdf import FPDF

from .admin_views import (
    _PDF_COLOR_BORDE,
    _PDF_COLOR_ENCABEZADO,
    _PDF_COLOR_TEXTO_SECUNDARIO,
    _PDF_COLOR_ZEBRA,
    _fpdf_output_bytes,
    _pdf_fecha_legible,
    _pdf_insertar_logo,
    _pdf_texto_seguro,
)
from .alumno_boleta_export import _BoletaAlumnoPDF, _pdf_ancho_util
from .models import CicloEscolar, Grupo, Horario

_TITULO = 'Carga Académica'
_DIAS_TABLA = ('lunes', 'martes', 'miercoles', 'jueves', 'viernes')
_HEADERS_DIAS = ('LUNES', 'MARTES', 'MIÉRCOLES', 'JUEVES', 'VIERNES')
_DIA_MAP_INVERSE = {
    'Lunes': 'lunes',
    'Martes': 'martes',
    'Miercoles': 'miercoles',
    'Jueves': 'jueves',
    'Viernes': 'viernes',
    'Sabado': 'sabado',
    'Domingo': 'domingo',
}

_ANCHO_COL_HORA = 28
_ALTURA_FILA_MIN = 8
_ALTURA_BLOQUE_SEGMENTO = 11.5
_PADDING_CELDA = 1.2


def _split_horario_en_unidades(horario: Horario) -> list[dict]:
    fmt = '%H:%M'
    inicio_s = str(horario.hora_inicio)[:5]
    fin_s = str(horario.hora_fin)[:5]
    inicio_dt = datetime.strptime(inicio_s, fmt)
    fin_dt = datetime.strptime(fin_s, fmt)

    segmentos = []
    current = inicio_dt
    unidad = 1
    while current + timedelta(hours=1) <= fin_dt:
        seg_fin_dt = current + timedelta(hours=1)
        segmentos.append({
            'unidad': unidad,
            'hora_inicio': current.strftime(fmt),
            'hora_fin': seg_fin_dt.strftime(fmt),
        })
        unidad += 1
        current = seg_fin_dt

    if current < fin_dt and fin_dt - current >= timedelta(minutes=30):
        segmentos.append({
            'unidad': unidad,
            'hora_inicio': current.strftime(fmt),
            'hora_fin': fin_dt.strftime(fmt),
        })

    return segmentos


def _construir_horarios_tabla(horarios) -> list[dict]:
    puntos_tiempo: set[str] = set()
    segmentos_por_horario: dict[int, list[dict]] = {}

    for horario in horarios:
        segmentos = _split_horario_en_unidades(horario)
        segmentos_por_horario[horario.id_horario] = segmentos
        for seg in segmentos:
            puntos_tiempo.add(seg['hora_inicio'])
            puntos_tiempo.add(seg['hora_fin'])

    puntos_tiempo_ordenados = sorted(puntos_tiempo)
    intervalos_hora = [
        f'{puntos_tiempo_ordenados[i]}-{puntos_tiempo_ordenados[i + 1]}'
        for i in range(len(puntos_tiempo_ordenados) - 1)
    ]

    horarios_tabla = [
        {
            'hora': intervalo,
            'lunes': [],
            'martes': [],
            'miercoles': [],
            'jueves': [],
            'viernes': [],
        }
        for intervalo in intervalos_hora
    ]

    for horario in horarios:
        dia_key = _DIA_MAP_INVERSE.get(horario.dia_semana, horario.dia_semana.lower())
        if dia_key not in _DIAS_TABLA:
            continue
        asignacion = horario.id_asignacion_materia
        for seg in segmentos_por_horario.get(horario.id_horario, []):
            intervalo_str = f"{seg['hora_inicio']}-{seg['hora_fin']}"
            for fila in horarios_tabla:
                if fila['hora'] != intervalo_str:
                    continue
                fila[dia_key].append({
                    'materia': asignacion.id_materia.nombre,
                    'docente': (
                        f'{asignacion.id_maestro.id_usuario.nombre} '
                        f'{asignacion.id_maestro.id_usuario.apellido}'
                    ),
                    'aula': horario.aula or '---',
                    'hora_inicio': seg['hora_inicio'],
                    'hora_fin': seg['hora_fin'],
                })
                break

    return horarios_tabla


def obtener_datos_horario_pdf(ciclo_filtro: str, grupo_filtro: str) -> dict | None:
    ciclo_filtro = (ciclo_filtro or '').strip()
    grupo_filtro = (grupo_filtro or '').strip()
    if not ciclo_filtro or not grupo_filtro:
        return None

    ciclo_obj = CicloEscolar.objects.filter(nombre_ciclo=ciclo_filtro).first()
    grupo_obj = Grupo.objects.filter(clave__iexact=grupo_filtro).select_related(
        'id_ciclo_escolar', 'id_carrera'
    ).first()
    if not grupo_obj:
        return None

    horarios = (
        Horario.objects.select_related(
            'id_asignacion_materia__id_grupo',
            'id_asignacion_materia__id_materia',
            'id_asignacion_materia__id_maestro__id_usuario',
        )
        .filter(id_asignacion_materia__id_grupo=grupo_obj)
        .order_by('dia_semana', 'hora_inicio')
    )

    return {
        'ciclo': ciclo_filtro,
        'ciclo_obj': ciclo_obj,
        'grupo_clave': grupo_obj.clave,
        'grupo_nombre': grupo_obj.nombre,
        'grupo_turno': grupo_obj.turno,
        'grupo_semestre': grupo_obj.semestre,
        'carrera': grupo_obj.id_carrera.nombre if grupo_obj.id_carrera_id else '',
        'horarios_tabla': _construir_horarios_tabla(horarios),
        'total_bloques': horarios.count(),
    }


def _altura_fila(pdf: FPDF, fila: dict, ancho_dia: float) -> float:
    altura = _ALTURA_FILA_MIN
    for dia in _DIAS_TABLA:
        segmentos = fila.get(dia) or []
        if not segmentos:
            continue
        bloques = len(segmentos) * _ALTURA_BLOQUE_SEGMENTO + _PADDING_CELDA
        for seg in segmentos:
            materia = _pdf_texto_seguro(str(seg.get('materia') or ''))
            pdf.set_font('Arial', 'B', 6)
            lineas = max(1, int(pdf.get_string_width(materia) / max(ancho_dia - 4, 8)) + 1)
            bloques += (lineas - 1) * 2.5
        altura = max(altura, bloques)
    return altura


def _pdf_dibujar_cabecera_horario(
    pdf: FPDF,
    *,
    datos: dict,
    fecha: datetime,
    generado_por: str,
) -> None:
    y_inicio = pdf.t_margin
    tiene_logo = _pdf_insertar_logo(pdf, x=pdf.l_margin, y=y_inicio, ancho=18, alto=18)
    bloque_logo = 22
    x_texto = pdf.l_margin + (bloque_logo if tiene_logo else 0)
    ancho_texto = _pdf_ancho_util(pdf) - (bloque_logo if tiene_logo else 0)

    pdf.set_xy(x_texto, y_inicio)
    pdf.set_font('Arial', 'B', 18)
    pdf.set_text_color(17, 24, 39)
    pdf.cell(ancho_texto, 8, _pdf_texto_seguro(_TITULO), 0, 1, 'L')

    pdf.set_x(x_texto)
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
    pdf.cell(ancho_texto, 5, _pdf_texto_seguro('SchoolTrack · Horario semanal'), 0, 1, 'L')

    pdf.set_x(x_texto)
    pdf.set_font('Arial', '', 9)
    pdf.cell(
        ancho_texto,
        5,
        _pdf_texto_seguro(f'Periodo: {datos.get("ciclo") or "---"} · Exportado el {_pdf_fecha_legible(fecha)}'),
        0,
        1,
        'L',
    )

    if generado_por:
        pdf.set_x(x_texto)
        pdf.set_font('Arial', '', 9)
        pdf.cell(ancho_texto, 5, _pdf_texto_seguro(f'Administrativo: {generado_por}'), 0, 1, 'L')

    y_fin = max(pdf.get_y(), y_inicio + bloque_logo)
    pdf.set_y(y_fin + 2)
    pdf.set_draw_color(*_PDF_COLOR_BORDE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(55, 65, 81)
    info = (
        f'Grupo: {datos.get("grupo_clave") or "---"} · '
        f'Nombre: {datos.get("grupo_nombre") or "---"} · '
        f'Semestre: {datos.get("grupo_semestre") or "---"} · '
        f'Turno: {datos.get("grupo_turno") or "---"}'
    )
    pdf.cell(0, 5, _pdf_texto_seguro(info), 0, 1, 'L')
    if datos.get('carrera'):
        pdf.set_font('Arial', '', 8)
        pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
        pdf.cell(0, 4, _pdf_texto_seguro(f'Carrera: {datos["carrera"]}'), 0, 1, 'L')
    pdf.ln(3)


def _pdf_dibujar_encabezado_tabla_horario(
    pdf: FPDF,
    *,
    x_inicio: float,
    ancho_hora: float,
    ancho_dia: float,
    altura: float = 7,
) -> None:
    pdf.set_font('Arial', 'B', 7)
    pdf.set_text_color(255, 255, 255)
    pdf.set_fill_color(*_PDF_COLOR_ENCABEZADO)
    pdf.set_draw_color(*_PDF_COLOR_BORDE)

    x = x_inicio
    pdf.set_xy(x, pdf.get_y())
    pdf.cell(ancho_hora, altura, _pdf_texto_seguro('HORA'), 1, 0, 'C', True)
    x += ancho_hora
    for encabezado in _HEADERS_DIAS:
        pdf.set_xy(x, pdf.get_y())
        pdf.cell(ancho_dia, altura, _pdf_texto_seguro(encabezado), 1, 0, 'C', True)
        x += ancho_dia
    pdf.ln(altura)


def _pdf_dibujar_bloque_segmento(
    pdf: FPDF,
    *,
    x: float,
    y: float,
    ancho: float,
    segmento: dict,
) -> float:
    bloque_h = _ALTURA_BLOQUE_SEGMENTO
    pdf.set_fill_color(239, 246, 255)
    pdf.rect(x, y, ancho, bloque_h, 'F')

    cy = y + 0.8
    pad_x = x + 0.8
    ancho_texto = ancho - 1.6

    pdf.set_xy(pad_x, cy)
    pdf.set_font('Arial', 'B', 6)
    pdf.set_text_color(17, 24, 39)
    pdf.multi_cell(ancho_texto, 2.6, _pdf_texto_seguro(str(segmento.get('materia') or '---')), 0, 'L')
    cy = pdf.get_y() + 0.3

    pdf.set_xy(pad_x, cy)
    pdf.set_font('Arial', '', 5.5)
    pdf.set_text_color(75, 85, 99)
    pdf.cell(ancho_texto, 2.4, _pdf_texto_seguro(str(segmento.get('docente') or '---')), 0, 1, 'L')

    pdf.set_x(pad_x)
    pdf.cell(ancho_texto, 2.4, _pdf_texto_seguro(f"Aula: {segmento.get('aula') or '---'}"), 0, 1, 'L')

    pdf.set_x(pad_x)
    pdf.set_font('Arial', 'B', 5.5)
    pdf.set_text_color(37, 99, 235)
    horario_txt = f"{segmento.get('hora_inicio') or ''} - {segmento.get('hora_fin') or ''}"
    pdf.cell(ancho_texto, 2.4, _pdf_texto_seguro(horario_txt), 0, 1, 'L')

    return max(bloque_h, pdf.get_y() - y + 0.5)


def _pdf_dibujar_celda_dia(
    pdf: FPDF,
    *,
    x: float,
    y: float,
    ancho: float,
    alto: float,
    segmentos: list[dict],
    fill: bool,
) -> None:
    pdf.set_draw_color(*_PDF_COLOR_BORDE)
    if fill:
        pdf.set_fill_color(*_PDF_COLOR_ZEBRA)
        pdf.rect(x, y, ancho, alto, 'DF')
    else:
        pdf.rect(x, y, ancho, alto)

    if not segmentos:
        return

    cy = y + _PADDING_CELDA
    ancho_interior = ancho - 2 * _PADDING_CELDA
    for segmento in segmentos:
        bloque_h = _pdf_dibujar_bloque_segmento(
            pdf,
            x=x + _PADDING_CELDA,
            y=cy,
            ancho=ancho_interior,
            segmento=segmento,
        )
        cy += bloque_h + 0.8
        if cy >= y + alto - 1:
            break


def _pdf_dibujar_tabla_horario(pdf: FPDF, horarios_tabla: list[dict]) -> None:
    ancho_util = _pdf_ancho_util(pdf)
    ancho_dia = (ancho_util - _ANCHO_COL_HORA) / len(_DIAS_TABLA)
    x_inicio = pdf.l_margin
    altura_encabezado = 7

    if not horarios_tabla:
        pdf.set_font('Arial', 'I', 9)
        pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
        pdf.cell(0, 8, _pdf_texto_seguro('Sin horarios registrados para este grupo.'), 0, 1, 'C')
        return

    _pdf_dibujar_encabezado_tabla_horario(
        pdf,
        x_inicio=x_inicio,
        ancho_hora=_ANCHO_COL_HORA,
        ancho_dia=ancho_dia,
        altura=altura_encabezado,
    )

    for indice, fila in enumerate(horarios_tabla):
        altura_fila = _altura_fila(pdf, fila, ancho_dia)
        if pdf.get_y() + altura_fila > pdf.h - pdf.b_margin:
            pdf.add_page()
            _pdf_dibujar_encabezado_tabla_horario(
                pdf,
                x_inicio=x_inicio,
                ancho_hora=_ANCHO_COL_HORA,
                ancho_dia=ancho_dia,
                altura=altura_encabezado,
            )

        y_fila = pdf.get_y()
        x = x_inicio
        fill = indice % 2 == 1

        pdf.set_font('Arial', 'B', 6.5)
        pdf.set_text_color(31, 41, 55)
        if fill:
            pdf.set_fill_color(249, 250, 251)
            pdf.rect(x, y_fila, _ANCHO_COL_HORA, altura_fila, 'DF')
        else:
            pdf.rect(x, y_fila, _ANCHO_COL_HORA, altura_fila)
        pdf.set_xy(x, y_fila + (altura_fila - 4) / 2)
        pdf.cell(_ANCHO_COL_HORA, 4, _pdf_texto_seguro(str(fila.get('hora') or '---')), 0, 0, 'C')
        x += _ANCHO_COL_HORA

        for dia in _DIAS_TABLA:
            _pdf_dibujar_celda_dia(
                pdf,
                x=x,
                y=y_fila,
                ancho=ancho_dia,
                alto=altura_fila,
                segmentos=fila.get(dia) or [],
                fill=fill,
            )
            x += ancho_dia

        pdf.set_y(y_fila + altura_fila)


def generar_pdf_horario_grupo(
    datos: dict,
    *,
    ahora: datetime,
    generado_por: str = '',
) -> bytes:
    pdf = _BoletaAlumnoPDF(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    _pdf_dibujar_cabecera_horario(pdf, datos=datos, fecha=ahora, generado_por=generado_por)

    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(55, 65, 81)
    pdf.cell(0, 6, _pdf_texto_seguro('Horario semanal'), 0, 1, 'L')
    pdf.ln(1)

    _pdf_dibujar_tabla_horario(pdf, datos.get('horarios_tabla') or [])

    pdf.ln(3)
    pdf.set_font('Arial', 'B', 8)
    pdf.set_text_color(17, 24, 39)
    pdf.cell(
        0,
        5,
        _pdf_texto_seguro(f'Total de bloques horario: {datos.get("total_bloques", 0)}'),
        0,
        1,
        'R',
    )

    return _fpdf_output_bytes(pdf)

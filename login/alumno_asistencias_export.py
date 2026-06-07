"""Generación de reporte PDF de asistencias para alumno."""
from __future__ import annotations

from datetime import datetime

from fpdf import FPDF

from .admin_views import (
    _PDF_COLOR_BORDE,
    _PDF_COLOR_TEXTO_SECUNDARIO,
    _fpdf_output_bytes,
    _pdf_dibujar_encabezados_tabla,
    _pdf_dibujar_fila_tabla,
    _pdf_fecha_legible,
    _pdf_insertar_logo,
    _pdf_texto_seguro,
)
from .alumno_boleta_export import (
    _BoletaAlumnoPDF,
    _pdf_alineaciones,
    _pdf_ancho_util,
    _pdf_dibujar_datos_alumno,
    _pdf_layout_tabla,
    _pdf_valor,
)

_TITULO = 'Reporte de Asistencias'

_CONFIG_COLUMNAS = {
    'Fecha': {'peso': 0.85, 'min': 20, 'max': 26, 'align': 'C'},
    'Unidad': {'peso': 0.45, 'min': 14, 'max': 18, 'align': 'C'},
    'Materia': {'peso': 2.0, 'min': 32, 'max': 80, 'align': 'L'},
    'Grupo': {'peso': 0.55, 'min': 14, 'max': 20, 'align': 'C'},
    'Horario': {'peso': 1.1, 'min': 24, 'max': 38, 'align': 'C'},
    'Estado': {'peso': 0.8, 'min': 18, 'max': 24, 'align': 'C'},
    'Observaciones': {'peso': 1.8, 'min': 22, 'max': 60, 'align': 'L'},
}

_HEADERS = list(_CONFIG_COLUMNAS.keys())


def _pdf_dibujar_titulo_asistencias(
    pdf: FPDF,
    *,
    perfil: dict,
    fecha: datetime,
    filtros: list[str] | None = None,
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
    pdf.cell(ancho_texto, 5, _pdf_texto_seguro('SchoolTrack · Sistema de gestión escolar'), 0, 1, 'L')

    periodo = _pdf_valor(perfil, 'periodo_actual')
    pdf.set_x(x_texto)
    pdf.set_font('Arial', '', 9)
    pdf.cell(
        ancho_texto,
        5,
        _pdf_texto_seguro(
            f'Periodo: {periodo} · Exportado el {_pdf_fecha_legible(fecha)}'
        ),
        0,
        1,
        'L',
    )

    if filtros:
        pdf.set_x(x_texto)
        pdf.set_font('Arial', 'I', 8)
        pdf.cell(ancho_texto, 5, _pdf_texto_seguro('Filtros: ' + ' · '.join(filtros)), 0, 1, 'L')

    y_fin = max(pdf.get_y(), y_inicio + bloque_logo)
    pdf.set_y(y_fin + 2)
    pdf.set_draw_color(*_PDF_COLOR_BORDE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)


def _fila_asistencia(row: dict) -> list[str]:
    observaciones = (row.get('observaciones') or '').strip()
    materia = str(row.get('materia') or '---')
    clave = row.get('materia_clave')
    if clave:
        materia = f'{clave} - {materia}'
    return [
        str(row.get('fecha') or '---'),
        f"U{row.get('unidad', '---')}",
        materia,
        str(row.get('grupo') or '---'),
        str(row.get('horario') or '---'),
        str(row.get('estatus') or '---'),
        observaciones or '---',
    ]


def generar_pdf_asistencias_alumno(
    *,
    perfil: dict,
    rows: list[dict],
    resumen: dict,
    filtros: list[str] | None,
    ahora: datetime,
    foto_ruta: str | None = None,
) -> bytes:
    pdf = _BoletaAlumnoPDF(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    filas = [_fila_asistencia(row) for row in rows]
    if not filas:
        filas = [['---', '---', 'Sin asistencias registradas', '---', '---', '---', '---']]

    x_inicio, anchos = _pdf_layout_tabla(pdf, _HEADERS, filas, _CONFIG_COLUMNAS)
    ancho_tabla = sum(anchos)

    _pdf_dibujar_titulo_asistencias(pdf, perfil=perfil, fecha=ahora, filtros=filtros)
    _pdf_dibujar_datos_alumno(
        pdf,
        perfil=perfil,
        foto_ruta=foto_ruta,
        ancho_bloque=ancho_tabla,
    )

    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(55, 65, 81)
    resumen_txt = (
        f'Total: {resumen.get("total", 0)} · Presentes: {resumen.get("presentes", 0)} · '
        f'Ausentes: {resumen.get("ausentes", 0)} · Tarde: {resumen.get("tarde", 0)} · '
        f'Justificado: {resumen.get("justificado", 0)} · Asistencia: {resumen.get("porcentaje", "0.0")}%'
    )
    pdf.cell(0, 5, _pdf_texto_seguro(resumen_txt), 0, 1, 'L')
    pdf.ln(2)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 6, _pdf_texto_seguro('Registros de asistencia'), 0, 1, 'L')
    pdf.ln(2)

    alineaciones = _pdf_alineaciones(_HEADERS, _CONFIG_COLUMNAS)
    _pdf_dibujar_encabezados_tabla(pdf, _HEADERS, anchos, x_inicio=x_inicio)
    for indice, fila in enumerate(filas):
        _pdf_dibujar_fila_tabla(
            pdf,
            fila,
            anchos,
            alineaciones=alineaciones,
            x_inicio=x_inicio,
            fill=indice % 2 == 1,
            headers=_HEADERS,
        )

    return _fpdf_output_bytes(pdf)

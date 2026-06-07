"""Generación de boleta PDF para alumno."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from fpdf import FPDF

from .admin_views import (
    _PDF_COLOR_BORDE,
    _PDF_COLOR_ENCABEZADO,
    _PDF_COLOR_TEXTO_SECUNDARIO,
    _PDF_COLOR_ZEBRA,
    _PDF_VALOR_NO_APLICA,
    _fpdf_output_bytes,
    _pdf_dibujar_encabezados_tabla,
    _pdf_dibujar_fila_tabla,
    _pdf_es_celda_vacia,
    _pdf_fecha_legible,
    _pdf_insertar_logo,
    _pdf_rect_borde,
    _pdf_texto_seguro,
)

logger = logging.getLogger(__name__)

_TITULO = 'Boleta de Calificaciones'

_CONFIG_COLUMNAS = {
    'Clave': {'peso': 0.7, 'min': 16, 'max': 26, 'align': 'L'},
    'Materia': {'peso': 2.4, 'min': 40, 'max': 95, 'align': 'L'},
    'Cal': {'peso': 0.5, 'min': 12, 'max': 18, 'align': 'C'},
    'U1': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
    'U2': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
    'U3': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
    'U4': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
    'U5': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
    'U6': {'peso': 0.42, 'min': 11, 'max': 16, 'align': 'C'},
}

_HEADERS = list(_CONFIG_COLUMNAS.keys())


class _BoletaAlumnoPDF(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*_PDF_COLOR_BORDE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
        ancho_pie = self.w - self.l_margin - self.r_margin
        ancho_izq = ancho_pie * 0.38
        ancho_centro = ancho_pie * 0.42
        ancho_der = ancho_pie - ancho_izq - ancho_centro
        self.set_x(self.l_margin)
        self.cell(ancho_izq, 4, _pdf_texto_seguro('Generado automáticamente por SchoolTrack'), 0, 0, 'L')
        self.cell(ancho_centro, 4, _pdf_texto_seguro('Documento informativo · sin validez oficial'), 0, 0, 'C')
        self.cell(ancho_der, 4, _pdf_texto_seguro(f'Página {self.page_no()}'), 0, 0, 'R')


def _pdf_ancho_util(pdf: FPDF) -> float:
    return pdf.w - pdf.l_margin - pdf.r_margin


def _pdf_ancho_texto(pdf: FPDF, texto: str, *, padding: float = 5) -> float:
    return pdf.get_string_width(_pdf_texto_seguro(texto)) + padding


def _pdf_ancho_minimo_columna(
    pdf: FPDF,
    header: str,
    col_idx: int,
    filas: list[list[str]],
    config: dict,
) -> float:
    pdf.set_font('Arial', 'B', 9)
    ancho = _pdf_ancho_texto(pdf, header, padding=6)
    pdf.set_font('Arial', '', 8)
    ancho = max(ancho, _pdf_ancho_texto(pdf, _PDF_VALOR_NO_APLICA))
    for fila in filas:
        if col_idx >= len(fila) or _pdf_es_celda_vacia(fila[col_idx]):
            continue
        ancho = max(ancho, _pdf_ancho_texto(pdf, fila[col_idx]))
    cfg = config.get(header, {})
    minimo = cfg.get('min', 18)
    maximo = cfg.get('max', 55)
    return min(maximo, max(minimo, ancho))


def _pdf_layout_tabla(
    pdf: FPDF,
    headers: list[str],
    filas: list[list[str]],
    config: dict,
) -> tuple[float, list[float]]:
    anchos = [
        _pdf_ancho_minimo_columna(pdf, header, col_idx, filas, config)
        for col_idx, header in enumerate(headers)
    ]
    ancho_util = _pdf_ancho_util(pdf)
    total = sum(anchos)

    if total > ancho_util:
        factor = ancho_util / total
        anchos = [ancho * factor for ancho in anchos]
    else:
        sobrante = ancho_util - total
        while sobrante > 0.05:
            indices = [
                i for i, header in enumerate(headers)
                if anchos[i] < config.get(header, {}).get('max', 90)
            ]
            if not indices:
                break
            pesos = [config.get(headers[i], {}).get('peso', 1.0) for i in indices]
            suma_pesos = sum(pesos) or 1
            asignado = 0.0
            for indice, peso in zip(indices, pesos):
                tope = config.get(headers[indice], {}).get('max', 90)
                incremento = min(tope - anchos[indice], sobrante * (peso / suma_pesos))
                anchos[indice] += incremento
                asignado += incremento
            if asignado <= 0.05:
                break
            sobrante -= asignado

        total = sum(anchos)
        if total < ancho_util:
            factor = ancho_util / total
            anchos = [ancho * factor for ancho in anchos]

    return pdf.l_margin, anchos


def _pdf_alineaciones(headers: list[str], config: dict) -> list[str]:
    return [config.get(header, {}).get('align', 'C') for header in headers]


def _pdf_valor(perfil: dict, clave: str, *, default: str = '---') -> str:
    valor = perfil.get(clave)
    if valor in (None, ''):
        return default
    return str(valor)


def _pdf_insertar_foto_alumno(
    pdf: FPDF,
    *,
    ruta: str | None,
    x: float,
    y: float,
    ancho: float,
    alto: float,
) -> bool:
    if not ruta or not os.path.isfile(ruta):
        pdf.set_draw_color(*_PDF_COLOR_BORDE)
        pdf.set_line_width(0.3)
        pdf.rect(x, y, ancho, alto)
        pdf.set_xy(x, y + alto / 2 - 3)
        pdf.set_font('Arial', 'I', 7)
        pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
        pdf.cell(ancho, 6, _pdf_texto_seguro('Sin foto'), 0, 0, 'C')
        return False
    try:
        with open(ruta, 'rb') as archivo:
            firma = archivo.read(4)
        tipo = 'JPEG' if firma[:2] == b'\xff\xd8' else 'PNG'
        pdf.image(ruta, x=x, y=y, w=ancho, h=alto, type=tipo)
        _pdf_rect_borde(pdf, x, y, ancho, alto)
        return True
    except Exception:
        logger.warning('No se pudo cargar la foto del alumno para la boleta', exc_info=True)
        return False


def _pdf_dibujar_titulo_boleta(
    pdf: FPDF,
    *,
    perfil: dict,
    fecha: datetime,
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

    y_fin = max(pdf.get_y(), y_inicio + bloque_logo)
    pdf.set_y(y_fin + 2)
    pdf.set_draw_color(*_PDF_COLOR_BORDE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)


def _pdf_dibujar_datos_alumno(
    pdf: FPDF,
    *,
    perfil: dict,
    foto_ruta: str | None,
    ancho_bloque: float,
) -> None:
    """Datos del alumno y foto alineados al mismo ancho que la tabla."""
    foto_ancho = 24
    foto_alto = 30
    gap_foto = 8
    ancho_texto = max(ancho_bloque - foto_ancho - gap_foto, 80)
    ancho_col = ancho_texto / 2
    x_col_der = pdf.l_margin + ancho_col
    x_foto = pdf.l_margin + ancho_texto + gap_foto
    y_inicio = pdf.get_y()

    _pdf_insertar_foto_alumno(
        pdf,
        ruta=foto_ruta,
        x=x_foto,
        y=y_inicio,
        ancho=foto_ancho,
        alto=foto_alto,
    )

    campos = [
        ('No. Control', _pdf_valor(perfil, 'matricula')),
        ('Nombre', _pdf_valor(perfil, 'nombre_completo')),
        ('CURP', _pdf_valor(perfil, 'curp')),
        ('Carrera', _pdf_valor(perfil, 'carrera')),
        ('Semestre', _pdf_valor(perfil, 'semestre')),
        ('Estatus', _pdf_valor(perfil, 'estatus')),
        ('Periodo', _pdf_valor(perfil, 'periodo_actual')),
        ('Periodo de ingreso', _pdf_valor(perfil, 'periodo_ingreso')),
    ]

    mitad = (len(campos) + 1) // 2
    col_izq = campos[:mitad]
    col_der = campos[mitad:]
    altura_fila = 6
    ancho_etiqueta_izq = 22
    ancho_etiqueta_der = 24

    for indice in range(max(len(col_izq), len(col_der))):
        y_fila = y_inicio + (indice * altura_fila)
        if indice < len(col_izq):
            etiqueta, valor = col_izq[indice]
            pdf.set_xy(pdf.l_margin, y_fila)
            pdf.set_font('Arial', 'B', 7)
            pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
            pdf.cell(ancho_etiqueta_izq, altura_fila, _pdf_texto_seguro(etiqueta + ':'), 0, 0, 'L')
            pdf.set_font('Arial', '', 8)
            pdf.set_text_color(31, 41, 55)
            pdf.cell(ancho_col - ancho_etiqueta_izq, altura_fila, _pdf_texto_seguro(valor), 0, 0, 'L')
        if indice < len(col_der):
            etiqueta, valor = col_der[indice]
            pdf.set_xy(x_col_der, y_fila)
            pdf.set_font('Arial', 'B', 7)
            pdf.set_text_color(*_PDF_COLOR_TEXTO_SECUNDARIO)
            pdf.cell(ancho_etiqueta_der, altura_fila, _pdf_texto_seguro(etiqueta + ':'), 0, 0, 'L')
            pdf.set_font('Arial', '', 8)
            pdf.set_text_color(31, 41, 55)
            pdf.cell(ancho_col - ancho_etiqueta_der, altura_fila, _pdf_texto_seguro(valor), 0, 0, 'L')

    alto_datos = max(len(col_izq), len(col_der)) * altura_fila
    pdf.set_y(max(y_inicio + foto_alto, y_inicio + alto_datos) + 6)


def _fila_boleta(row: dict) -> list[str]:
    unidades = row.get('unidades', [])
    promedio = row.get('promedio', '---')
    materia = str(row.get('materia') or '---')
    return [
        str(row.get('codigo') or '---'),
        materia,
        str(promedio),
        *[str(u) for u in unidades],
    ]


def generar_pdf_boleta_alumno(
    *,
    perfil: dict,
    rows: list[dict],
    promedio_general: str,
    ahora: datetime,
    foto_ruta: str | None = None,
) -> bytes:
    pdf = _BoletaAlumnoPDF(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    filas = [_fila_boleta(row) for row in rows]
    if not filas:
        filas = [['---', 'Sin calificaciones registradas', '---', '---', '---', '---', '---', '---', '---']]

    x_inicio, anchos = _pdf_layout_tabla(pdf, _HEADERS, filas, _CONFIG_COLUMNAS)
    ancho_tabla = sum(anchos)

    _pdf_dibujar_titulo_boleta(pdf, perfil=perfil, fecha=ahora)
    _pdf_dibujar_datos_alumno(
        pdf,
        perfil=perfil,
        foto_ruta=foto_ruta,
        ancho_bloque=ancho_tabla,
    )

    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(55, 65, 81)
    pdf.cell(0, 6, _pdf_texto_seguro('Calificaciones del periodo'), 0, 1, 'L')
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

    pdf.ln(4)
    pdf.set_font('Arial', 'B', 10)
    pdf.set_text_color(17, 24, 39)
    pdf.set_x(x_inicio)
    pdf.cell(
        ancho_tabla,
        8,
        _pdf_texto_seguro(f'Promedio general: {promedio_general}'),
        0,
        1,
        'R',
    )

    return _fpdf_output_bytes(pdf)

import os
import json
import logging
from datetime import datetime
import unicodedata
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
import csv
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from fpdf import FPDF

from .models import Usuarios, Alumnos, Maestros, Administrativos, Administrador, DatosPersonales, Carrera, CicloEscolar, Grupo, LogCalificacion


logger = logging.getLogger(__name__)


def sesion_roles_permitidas(request, roles: tuple) -> bool:
    role = request.session.get('usuario_rol')
    return role is not None and role in roles


def construir_direccion(data) -> str | None:
    """
    Une los campos separados de dirección en un solo texto para guardar en DatosPersonales.direccion.
    """
    direccion_directa = data.get('direccion', '').strip()
    if direccion_directa:
        return direccion_directa

    partes = [
        data.get('calle', '').strip(),
        data.get('numero_exterior', '').strip(),
        data.get('numero_interior', '').strip(),
        data.get('colonia', '').strip(),
        data.get('municipio', '').strip(),
        data.get('estado', '').strip(),
        data.get('cp', '').strip(),
    ]

    partes = [parte for parte in partes if parte]
    if not partes:
        return None

    return ', '.join(partes)


def _parse_fecha_nacimiento(valor: str):
    if not valor:
        return None
    return datetime.strptime(valor, '%Y-%m-%d').date()


def _periodo_actual() -> str:
    hoy = timezone.now()
    if hoy.month <= 6:
        periodo = 'A'
    elif hoy.month >= 8:
        periodo = 'B'
    else:
        # Julio queda como A para no romper el flujo de captura.
        periodo = 'A'

    return f"{hoy.year}-{periodo}"


def desglosar_direccion(direccion: str | None) -> dict:
    """Convierte la dirección guardada en texto a campos separados para editarla."""
    if not direccion:
        return {
            'calle': '',
            'numero_exterior': '',
            'numero_interior': '',
            'colonia': '',
            'municipio': '',
            'estado': '',
            'cp': '',
        }

    partes = [parte.strip() for parte in str(direccion).split(',')]
    partes += [''] * (7 - len(partes))

    return {
        'calle': partes[0],
        'numero_exterior': partes[1],
        'numero_interior': partes[2],
        'colonia': partes[3],
        'municipio': partes[4],
        'estado': partes[5],
        'cp': partes[6],
    }


def _normalizar_texto(valor: str) -> str:
    texto = unicodedata.normalize('NFD', valor or '')
    texto = ''.join(ch for ch in texto if unicodedata.category(ch) != 'Mn')
    return texto.lower().strip()


def _validar_datos_personales_unicos(correo: str, curp: str, usuario_id: int | None = None) -> str | None:
    """
    Valida solo los datos que deben ser únicos en el sistema.
    Devuelve un mensaje de error si encuentra duplicados; si no, devuelve None.
    """
    errores = []

    if correo:
        correos = DatosPersonales.objects.filter(correo_inst__iexact=correo)
        if usuario_id is not None:
            correos = correos.exclude(id_usuario_id=usuario_id)
        if correos.exists():
            errores.append('El correo ya está registrado')

    if curp:
        curps = DatosPersonales.objects.filter(curp__iexact=curp)
        if usuario_id is not None:
            curps = curps.exclude(id_usuario_id=usuario_id)
        if curps.exists():
            errores.append('La CURP ya está registrada')

    if errores:
        return '. '.join(errores) + '.'

    return None


def _formatear_error_validacion(error: ValidationError) -> str:
    """
    Convierte un ValidationError del modelo en un mensaje de error legible.
    """
    if hasattr(error, 'message_dict'):
        # Error con múltiples campos
        mensajes = []
        for campo, errores in error.message_dict.items():
            if isinstance(errores, list):
                mensajes.extend(errores)
            else:
                mensajes.append(str(errores))
        return '. '.join(mensajes)
    elif hasattr(error, 'messages'):
        # Error con lista de mensajes
        return '. '.join(error.messages)
    else:
        # Error simple
        return str(error)


def _validar_nombre_carrera_unico(nombre: str, carrera_id: int | None = None) -> str | None:
    """
    Evita duplicados de carrera por nombre, ignorando mayúsculas, minúsculas y acentos.
    """
    nombre_normalizado = _normalizar_texto(nombre)
    if not nombre_normalizado:
        return None

    for carrera in Carrera.objects.all():
        if carrera_id is not None and carrera.id == carrera_id:
            continue

        if _normalizar_texto(carrera.nombre) == nombre_normalizado:
            return 'Ya existe una carrera con ese nombre.'

    return None


# ==================== VISTAS PRINCIPALES DE ADMINISTRADOR ====================

def gestion_usuarios(request):
    """Vista principal para gestionar todos los usuarios"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    # Obtener todos los usuarios con sus datos específicos
    usuarios_data = []
    
    # Alumnos
    alumnos = Alumnos.objects.select_related('id_usuario', 'id_carrera').all()
    for alumno in alumnos:
        usuarios_data.append({
            'id_usuario': alumno.id_usuario.id_usuario,
            'matricula': alumno.id_usuario.matricula,
            'nombre': alumno.id_usuario.nombre,
            'apellido': alumno.id_usuario.apellido,
            'rol': 'alumno',
            'carrera': str(alumno.id_carrera) if alumno.id_carrera else '',
            'semestre': alumno.semestre,
            'estatus': alumno.estatus,
            'ultimo_acceso': alumno.id_usuario.ultimo_acceso
        })
    
    # Maestros
    maestros = Maestros.objects.select_related('id_usuario').all()
    for maestro in maestros:
        usuarios_data.append({
            'id_usuario': maestro.id_usuario.id_usuario,
            'matricula': maestro.id_usuario.matricula,
            'nombre': maestro.id_usuario.nombre,
            'apellido': maestro.id_usuario.apellido,
            'rol': 'maestro',
            'departamento': maestro.departamento,
            'cubiculo': maestro.cubiculo,
            'grado_academico': maestro.grado_academico,
            'ultimo_acceso': maestro.id_usuario.ultimo_acceso
        })
    
    # Administrativos
    administrativos = Administrativos.objects.select_related('id_usuario').all()
    for administrativo in administrativos:
        usuarios_data.append({
            'id_usuario': administrativo.id_usuario.id_usuario,
            'matricula': administrativo.id_usuario.matricula,
            'nombre': administrativo.id_usuario.nombre,
            'apellido': administrativo.id_usuario.apellido,
            'rol': 'administrativo',
            'departamento': administrativo.departamento,
            'puesto': administrativo.puesto,
            'ultimo_acceso': administrativo.id_usuario.ultimo_acceso
        })
    
    # Administradores
    administradores = Administrador.objects.select_related('id_usuario').all()
    for administrador in administradores:
        usuarios_data.append({
            'id_usuario': administrador.id_usuario.id_usuario,
            'matricula': administrador.id_usuario.matricula,
            'nombre': administrador.id_usuario.nombre,
            'apellido': administrador.id_usuario.apellido,
            'rol': 'admin',
            'puesto': administrador.puesto,
            'nivel_prioridad': administrador.nivel_prioridad,
            'ultimo_acceso': administrador.id_usuario.ultimo_acceso
        })
    
    # Ordenar por ID de usuario, de menor a mayor
    usuarios_data.sort(key=lambda x: x['id_usuario'])

    # Búsqueda global por nombre, matrícula, rol o datos visibles del perfil
    busqueda = request.GET.get('q', '').strip()
    usuarios_filtrados = usuarios_data
    if busqueda:
        texto_busqueda = _normalizar_texto(busqueda)

        def coincide(usuario: dict) -> bool:
            campos = [
                str(usuario.get('id_usuario', '')),
                usuario.get('matricula', ''),
                usuario.get('nombre', ''),
                usuario.get('apellido', ''),
                f"{usuario.get('nombre', '')} {usuario.get('apellido', '')}",  # Nombre completo
                usuario.get('rol', ''),
                usuario.get('carrera', ''),
                usuario.get('departamento', ''),
                usuario.get('puesto', ''),
                usuario.get('grado_academico', ''),
                str(usuario.get('semestre', '')),
                str(usuario.get('nivel_prioridad', '')),
                usuario.get('estatus', ''),
            ]
            return any(texto_busqueda in _normalizar_texto(campo) for campo in campos if campo is not None)

        usuarios_filtrados = [usuario for usuario in usuarios_data if coincide(usuario)]

    # Filtro por rol
    rol_filtro = request.GET.get('rol', '').strip()
    if rol_filtro:
        usuarios_filtrados = [usuario for usuario in usuarios_filtrados if usuario.get('rol') == rol_filtro]
    
    # Paginación
    paginator = Paginator(usuarios_filtrados, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'usuarios': page_obj,
        'total_usuarios': len(usuarios_data),
        'usuarios_encontrados': len(usuarios_filtrados),
        'busqueda': busqueda,
        'rol_filtro': rol_filtro,
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    }
    
    return render(request, 'administrador/GestionUsuarios.html', context)


def exportar_usuarios(request):
    """Exporta la lista de usuarios a Excel con formato profesional"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    # Obtener todos los usuarios con sus datos específicos
    usuarios_data = []

    # Alumnos
    alumnos = Alumnos.objects.select_related('id_usuario', 'id_carrera').all()
    for alumno in alumnos:
        usuarios_data.append({
            'Matrícula': alumno.id_usuario.matricula,
            'Nombre': alumno.id_usuario.nombre,
            'Apellido': alumno.id_usuario.apellido,
            'Rol': 'Alumno',
            'Carrera': str(alumno.id_carrera) if alumno.id_carrera else '-',
            'Semestre': alumno.semestre,
            'Estatus': alumno.estatus,
            'Último Acceso': alumno.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if alumno.id_usuario.ultimo_acceso else '-'
        })

    # Maestros
    maestros = Maestros.objects.select_related('id_usuario').all()
    for maestro in maestros:
        usuarios_data.append({
            'Matrícula': maestro.id_usuario.matricula,
            'Nombre': maestro.id_usuario.nombre,
            'Apellido': maestro.id_usuario.apellido,
            'Rol': 'Maestro',
            'Departamento': maestro.departamento,
            'Cubículo': maestro.cubiculo or '-',
            'Grado Académico': maestro.grado_academico,
            'Último Acceso': maestro.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if maestro.id_usuario.ultimo_acceso else '-'
        })

    # Administrativos
    administrativos = Administrativos.objects.select_related('id_usuario').all()
    for administrativo in administrativos:
        usuarios_data.append({
            'Matrícula': administrativo.id_usuario.matricula,
            'Nombre': administrativo.id_usuario.nombre,
            'Apellido': administrativo.id_usuario.apellido,
            'Rol': 'Administrativo',
            'Departamento': administrativo.departamento,
            'Puesto': administrativo.puesto,
            'Último Acceso': administrativo.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if administrativo.id_usuario.ultimo_acceso else '-'
        })

    # Administradores
    administradores = Administrador.objects.select_related('id_usuario').all()
    for administrador in administradores:
        usuarios_data.append({
            'Matrícula': administrador.id_usuario.matricula,
            'Nombre': administrador.id_usuario.nombre,
            'Apellido': administrador.id_usuario.apellido,
            'Rol': 'Administrador',
            'Puesto': administrador.puesto,
            'Nivel Prioridad': administrador.nivel_prioridad,
            'Último Acceso': administrador.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if administrador.id_usuario.ultimo_acceso else '-'
        })

    # Ordenar por Matrícula
    usuarios_data.sort(key=lambda x: x['Matrícula'])

    # Aplicar filtros si existen
    busqueda = request.GET.get('q', '').strip()
    rol_filtro = request.GET.get('rol', '').strip()

    if busqueda:
        texto_busqueda = _normalizar_texto(busqueda)
        def coincide(usuario: dict) -> bool:
            campos = [
                usuario.get('Matrícula', ''),
                usuario.get('Nombre', ''),
                usuario.get('Apellido', ''),
                f"{usuario.get('Nombre', '')} {usuario.get('Apellido', '')}",
                usuario.get('Rol', ''),
                usuario.get('Carrera', ''),
                usuario.get('Departamento', ''),
                usuario.get('Puesto', ''),
                usuario.get('Grado Académico', ''),
                str(usuario.get('Semestre', '')),
                str(usuario.get('Nivel Prioridad', '')),
                usuario.get('Estatus', ''),
            ]
            return any(texto_busqueda in _normalizar_texto(campo) for campo in campos if campo is not None)
        usuarios_data = [usuario for usuario in usuarios_data if coincide(usuario)]

    if rol_filtro:
        rol_map = {'alumno': 'Alumno', 'maestro': 'Maestro', 'administrativo': 'Administrativo', 'admin': 'Administrador'}
        usuarios_data = [usuario for usuario in usuarios_data if usuario.get('Rol') == rol_map.get(rol_filtro, rol_filtro)]

    # Crear DataFrame con pandas
    df = pd.DataFrame(usuarios_data)

    # Filtrar columnas según el rol seleccionado
    if rol_filtro:
        if rol_filtro == 'alumno':
            columnas_a_mantener = ['Matrícula', 'Nombre', 'Apellido', 'Rol', 'Carrera', 'Semestre', 'Estatus', 'Último Acceso']
        elif rol_filtro == 'maestro':
            columnas_a_mantener = ['Matrícula', 'Nombre', 'Apellido', 'Rol', 'Departamento', 'Cubículo', 'Grado Académico', 'Último Acceso']
        elif rol_filtro == 'administrativo':
            columnas_a_mantener = ['Matrícula', 'Nombre', 'Apellido', 'Rol', 'Departamento', 'Puesto', 'Último Acceso']
        elif rol_filtro == 'admin':
            columnas_a_mantener = ['Matrícula', 'Nombre', 'Apellido', 'Rol', 'Puesto', 'Nivel Prioridad', 'Último Acceso']
        else:
            columnas_a_mantener = list(df.columns)
        
        # Solo mantener columnas que existen
        columnas_a_mantener = [col for col in columnas_a_mantener if col in df.columns]
        df = df[columnas_a_mantener]

    # Crear archivo Excel en memoria
    from io import BytesIO
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Usuarios', index=False, startrow=3)
        
        # Obtener el workbook y worksheet
        workbook = writer.book
        worksheet = writer.sheets['Usuarios']
        
        # Estilos
        header_font = Font(name='Arial', size=12, bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='2B63D9', end_color='2B63D9', fill_type='solid')
        title_font = Font(name='Arial', size=16, bold=True, color='1E40AF')
        date_font = Font(name='Arial', size=10, italic=True, color='666666')
        footer_font = Font(name='Arial', size=9, italic=True, color='999999')
        
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título del reporte (merge de todas las columnas)
        worksheet['A1'] = 'Reporte de Usuarios - SchoolTrack'
        worksheet['A1'].font = title_font
        worksheet['A1'].alignment = Alignment(horizontal='left')

        # Fecha de exportación
        fecha_exportacion = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        worksheet['A2'] = f'Fecha de exportación: {fecha_exportacion}'
        worksheet['A2'].font = date_font
        worksheet['A2'].alignment = Alignment(horizontal='left')
        
        # Estilizar encabezados de columna
        for col_num, column in enumerate(df.columns, 1):
            cell = worksheet.cell(row=4, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border
        
        # Estilizar datos con alineación específica
        for row_num, row in enumerate(df.values, 5):
            for col_num, value in enumerate(row, 1):
                cell = worksheet.cell(row=row_num, column=col_num)
                column_name = df.columns[col_num - 1]

                # Alineación centrada para todos los datos
                cell.alignment = Alignment(horizontal='center', vertical='center')

                cell.border = thin_border
        
        # Auto-ajustar ancho de columnas con mejor cálculo
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value:
                        # Calcular ancho basado en el contenido
                        text_length = len(str(cell.value))
                        # Para caracteres especiales como acentos, aumentar un poco
                        if any(ord(c) > 127 for c in str(cell.value)):
                            text_length = int(text_length * 1.2)
                        if text_length > max_length:
                            max_length = text_length
                except:
                    pass
            # Ajustar ancho con un mínimo de 12 y máximo de 50
            adjusted_width = max(12, min(max_length + 4, 50))
            worksheet.column_dimensions[column_letter].width = adjusted_width

        # Merge del título después del auto-ajuste de columnas
        from openpyxl.utils import get_column_letter
        num_cols = len(df.columns)
        end_col_letter = get_column_letter(num_cols)
        worksheet.merge_cells(f'A1:{end_col_letter}1')
        worksheet['A1'].alignment = Alignment(horizontal='center', vertical='center')

        # Pie de página
        ultima_fila = len(df) + 5
        worksheet[f'A{ultima_fila}'] = 'Generado automáticamente por SchoolTrack'
        worksheet[f'A{ultima_fila}'].font = footer_font
        worksheet[f'A{ultima_fila}'].alignment = Alignment(horizontal='left')
    
    output.seek(0)
    
    # Crear respuesta
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="reporte_usuarios_{}.xlsx"'.format(
        datetime.now().strftime('%Y%m%d_%H%M%S')
    )
    
    return response


def exportar_usuarios_pdf(request):
    """Exporta la lista de usuarios a PDF con formato profesional"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    # Obtener todos los usuarios con sus datos específicos
    usuarios_data = []

    # Alumnos
    alumnos = Alumnos.objects.select_related('id_usuario', 'id_carrera').all()
    for alumno in alumnos:
        usuarios_data.append({
            'Matrícula': alumno.id_usuario.matricula,
            'Nombre': alumno.id_usuario.nombre,
            'Apellido': alumno.id_usuario.apellido,
            'Rol': 'Alumno',
            'Carrera': str(alumno.id_carrera) if alumno.id_carrera else '-',
            'Semestre': alumno.semestre,
            'Estatus': alumno.estatus,
            'Último Acceso': alumno.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if alumno.id_usuario.ultimo_acceso else '-'
        })

    # Maestros
    maestros = Maestros.objects.select_related('id_usuario').all()
    for maestro in maestros:
        usuarios_data.append({
            'Matrícula': maestro.id_usuario.matricula,
            'Nombre': maestro.id_usuario.nombre,
            'Apellido': maestro.id_usuario.apellido,
            'Rol': 'Maestro',
            'Departamento': maestro.departamento,
            'Cubículo': maestro.cubiculo or '-',
            'Grado Académico': maestro.grado_academico,
            'Último Acceso': maestro.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if maestro.id_usuario.ultimo_acceso else '-'
        })

    # Administrativos
    administrativos = Administrativos.objects.select_related('id_usuario').all()
    for administrativo in administrativos:
        usuarios_data.append({
            'Matrícula': administrativo.id_usuario.matricula,
            'Nombre': administrativo.id_usuario.nombre,
            'Apellido': administrativo.id_usuario.apellido,
            'Rol': 'Administrativo',
            'Departamento': administrativo.departamento,
            'Puesto': administrativo.puesto,
            'Último Acceso': administrativo.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if administrativo.id_usuario.ultimo_acceso else '-'
        })

    # Administradores
    administradores = Administrador.objects.select_related('id_usuario').all()
    for administrador in administradores:
        usuarios_data.append({
            'Matrícula': administrador.id_usuario.matricula,
            'Nombre': administrador.id_usuario.nombre,
            'Apellido': administrador.id_usuario.apellido,
            'Rol': 'Administrador',
            'Puesto': administrador.puesto,
            'Nivel Prioridad': administrador.nivel_prioridad,
            'Último Acceso': administrador.id_usuario.ultimo_acceso.strftime('%d/%m/%Y %H:%M') if administrador.id_usuario.ultimo_acceso else '-'
        })

    # Ordenar por Matrícula
    usuarios_data.sort(key=lambda x: x['Matrícula'])

    # Aplicar filtros si existen
    busqueda = request.GET.get('q', '').strip()
    rol_filtro = request.GET.get('rol', '').strip()

    if busqueda:
        texto_busqueda = _normalizar_texto(busqueda)
        def coincide(usuario: dict) -> bool:
            campos = [
                usuario.get('Matrícula', ''),
                usuario.get('Nombre', ''),
                usuario.get('Apellido', ''),
                f"{usuario.get('Nombre', '')} {usuario.get('Apellido', '')}",
                usuario.get('Rol', ''),
                usuario.get('Carrera', ''),
                usuario.get('Departamento', ''),
                usuario.get('Puesto', ''),
                usuario.get('Grado Académico', ''),
                str(usuario.get('Semestre', '')),
                str(usuario.get('Nivel Prioridad', '')),
                usuario.get('Estatus', ''),
            ]
            return any(texto_busqueda in _normalizar_texto(campo) for campo in campos if campo is not None)
        usuarios_data = [usuario for usuario in usuarios_data if coincide(usuario)]

    if rol_filtro:
        rol_map = {'alumno': 'Alumno', 'maestro': 'Maestro', 'administrativo': 'Administrativo', 'admin': 'Administrador'}
        usuarios_data = [usuario for usuario in usuarios_data if usuario.get('Rol') == rol_map.get(rol_filtro, rol_filtro)]

    # Crear PDF con orientación horizontal
    pdf = FPDF(orientation='L')
    pdf.add_page()

    # Configurar fuente
    pdf.set_font('Arial', '', 10)

    # Título
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, 'Reporte de Usuarios - SchoolTrack', 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 6, f'Fecha de exportación: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}', 0, 1, 'L')
    pdf.ln(5)

    # Encabezados de tabla (sin ID, Último Acceso, Estatus, Puesto, Grado Académico para reducir compresión)
    headers = ['Matrícula', 'Nombre', 'Apellido', 'Rol']
    if rol_filtro:
        if rol_filtro == 'alumno':
            headers.extend(['Carrera', 'Semestre'])
        elif rol_filtro == 'maestro':
            headers.extend(['Departamento', 'Cubículo'])
        elif rol_filtro == 'administrativo':
            headers.extend(['Departamento'])
        elif rol_filtro == 'admin':
            headers.extend(['Nivel Prioridad'])
    else:
        headers.extend(['Carrera', 'Semestre', 'Departamento'])

    # Calcular ancho de columnas (landscape tiene más espacio)
    col_widths = []
    total_width = 277  # Ancho total en landscape (297mm - 20mm márgenes)
    num_cols = len(headers)
    base_width = total_width / num_cols

    for i, header in enumerate(headers):
        if header in ['Semestre', 'Nivel Prioridad']:
            col_widths.append(base_width * 0.5)
        elif header in ['Matrícula']:
            col_widths.append(base_width * 0.7)
        elif header in ['Nombre']:
            col_widths.append(base_width * 1.0)
        elif header in ['Apellido']:
            col_widths.append(base_width * 1.0)
        elif header in ['Carrera']:
            col_widths.append(base_width * 1.8)  # Más ancho para nombres largos de carrera
        elif header in ['Rol']:
            col_widths.append(base_width * 0.6)  # Reducido
        elif header in ['Departamento']:
            col_widths.append(base_width * 1.15)
        elif header in ['Grado Académico']:
            col_widths.append(base_width * 0.9)
        elif header in ['Último Acceso']:
            col_widths.append(base_width * 0.8)
        else:
            col_widths.append(base_width)

    # Dibujar encabezados
    pdf.set_fill_color(43, 99, 217)  # Azul institucional
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 10)

    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 7, header, 1, 0, 'C', 1)
    pdf.ln()

    # Datos
    pdf.set_fill_color(245, 245, 245)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', '', 9)

    for i, usuario in enumerate(usuarios_data):
        if i % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
        else:
            pdf.set_fill_color(255, 255, 255)

        row_data = [
            usuario.get('Matrícula', '-'),
            usuario.get('Nombre', '-'),
            usuario.get('Apellido', '-'),
            usuario.get('Rol', '-')
        ]

        if rol_filtro:
            if rol_filtro == 'alumno':
                row_data.extend([
                    usuario.get('Carrera', '-'),
                    str(usuario.get('Semestre', '-'))
                ])
            elif rol_filtro == 'maestro':
                row_data.extend([
                    usuario.get('Departamento', '-'),
                    usuario.get('Cubículo', '-')
                ])
            elif rol_filtro == 'administrativo':
                row_data.extend([
                    usuario.get('Departamento', '-')
                ])
            elif rol_filtro == 'admin':
                row_data.extend([
                    str(usuario.get('Nivel Prioridad', '-'))
                ])
        else:
            row_data.extend([
                usuario.get('Carrera', '-'),
                str(usuario.get('Semestre', '-')),
                usuario.get('Departamento', '-')
            ])

        for j, data in enumerate(row_data):
            pdf.cell(col_widths[j], 6, str(data), 1, 0, 'C', 1)
        pdf.ln()

    # Pie de página
    pdf.set_y(-15)
    pdf.set_font('Arial', 'I', 8)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(0, 5, 'Generado automáticamente por SchoolTrack', 0, 0, 'L')
    pdf.cell(0, 5, f'Página {pdf.page_no()}', 0, 0, 'R')

    # Generar respuesta
    response = HttpResponse(pdf.output(dest='S').encode('latin-1'), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="reporte_usuarios_{}.pdf"'.format(
        datetime.now().strftime('%Y%m%d_%H%M%S')
    )

    return response


def gestion_carreras(request):
    """Vista para administrar carreras"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    context = {
        'carreras': Carrera.objects.order_by('nombre').all(),
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    }
    return render(request, 'administrador/GestionCarreras.html', context)


def agregar_carrera(request):
    """Crea una nueva carrera"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('gestion_carreras')

    try:
        nombre = request.POST.get('nombre', '').strip()
        clave = request.POST.get('clave', '').strip().upper()

        if not nombre or not clave:
            messages.error(request, 'Nombre y clave son obligatorios')
            return redirect('gestion_carreras')

        error_nombre = _validar_nombre_carrera_unico(nombre)
        if error_nombre:
            messages.error(request, error_nombre)
            return redirect('gestion_carreras')

        if Carrera.objects.filter(clave__iexact=clave).exists():
            messages.error(request, 'Ya existe una carrera con esa clave')
            return redirect('gestion_carreras')

        Carrera.objects.create(nombre=nombre, clave=clave)
        messages.success(request, f'Carrera {nombre} agregada correctamente')
    except Exception as e:
        messages.error(request, f'Error al agregar carrera: {str(e)}')

    return redirect('gestion_carreras')


def editar_carrera(request, carrera_id):
    """Edita una carrera existente"""
    if not sesion_roles_permitidas(request, ('admin',)):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'No autorizado'}, status=403)
        return redirect('selector_rol')

    carrera = get_object_or_404(Carrera, pk=carrera_id)

    if request.method != 'POST':
        return redirect('gestion_carreras')

    try:
        nombre = request.POST.get('nombre', '').strip()
        clave = request.POST.get('clave', '').strip().upper()

        if not nombre or not clave:
            error_msg = 'Nombre y clave son obligatorios'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return redirect('gestion_carreras')

        error_nombre = _validar_nombre_carrera_unico(nombre, carrera.id)
        if error_nombre:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': error_nombre})
            messages.error(request, error_nombre)
            return redirect('gestion_carreras')

        if Carrera.objects.exclude(pk=carrera.pk).filter(clave__iexact=clave).exists():
            error_msg = 'Ya existe otra carrera con esa clave'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return redirect('gestion_carreras')

        # Advertencia si la carrera tiene alumnos
        num_alumnos = Alumnos.objects.filter(id_carrera=carrera).count()
        warning_msg = None
        if num_alumnos > 0:
            warning_msg = f'Esta carrera tiene {num_alumnos} alumno(s) inscrito(s). Cambiar el nombre puede afectar reportes históricos.'

        carrera.nombre = nombre
        carrera.clave = clave
        carrera.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'warning': warning_msg})
        messages.success(request, f'Carrera {nombre} actualizada correctamente')
        if warning_msg:
            messages.warning(request, warning_msg)
    except Exception as e:
        error_msg = f'Error al editar carrera: {str(e)}'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)

    return redirect('gestion_carreras')


def eliminar_carrera(request, carrera_id):
    """Elimina una carrera"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('gestion_carreras')

    carrera = get_object_or_404(Carrera, pk=carrera_id)

    try:
        # Validar que la carrera no tenga alumnos asociados
        if Alumnos.objects.filter(id_carrera=carrera).exists():
            messages.error(request, f'No se puede eliminar "{carrera.nombre}" porque tiene alumnos inscritos')
            return redirect('gestion_carreras')

        # Validar que la carrera no tenga grupos asociados (con materias)
        if Grupo.objects.filter(id_carrera=carrera).exists():
            messages.error(request, f'No se puede eliminar "{carrera.nombre}" porque tiene grupos/materias asignadas')
            return redirect('gestion_carreras')

        nombre = carrera.nombre
        carrera.delete()
        messages.success(request, f'Carrera {nombre} eliminada correctamente')
    except Exception as e:
        messages.error(request, f'Error al eliminar carrera: {str(e)}')

    return redirect('gestion_carreras')


def verificar_clave_carrera(request):
    """Verifica si una clave de carrera ya existe (para validación AJAX)"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return JsonResponse({'existe': False}, status=403)

    clave = request.GET.get('clave', '').strip().upper()
    carrera_id = request.GET.get('carrera_id', '')

    if not clave:
        return JsonResponse({'existe': False})

    queryset = Carrera.objects.filter(clave__iexact=clave)

    # Si estamos editando, excluir la carrera actual
    if carrera_id:
        try:
            queryset = queryset.exclude(pk=int(carrera_id))
        except ValueError:
            pass

    existe = queryset.exists()
    return JsonResponse({'existe': existe})


def agregar_usuario(request):
    """Vista para agregar un nuevo usuario - La matrícula se genera automáticamente"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    # Obtener el próximo ID disponible
    from django.db.models import Max
    max_id = Usuarios.objects.aggregate(Max('id_usuario'))['id_usuario__max'] or 0
    proximo_id = max_id + 1
    
    # Obtener las últimas matrículas por rol para predecir la siguiente
    año_actual = timezone.now().year
    siguientes = {}
    prefijos = {'admin': 'ADM-', 'maestro': 'EMP-', 'administrativo': 'AD-', 'alumno': ''}
    
    for rol, prefijo in prefijos.items():
        patron = f"{prefijo}{año_actual}"
        ultima = Usuarios.objects.filter(matricula__startswith=patron).order_by('-matricula').first()
        if ultima:
            try:
                ultimo_num = int(ultima.matricula[-4:])
                siguientes[rol] = f"{patron}{ultimo_num + 1:04d}"
            except:
                siguientes[rol] = f"{patron}0001"
        else:
            siguientes[rol] = f"{patron}0001"
    
    if request.method == 'POST':
        # Obtener datos del formulario
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        rol = request.POST.get('rol', '').strip()
        if rol == 'administrador':
            rol = 'admin'
        
        # Generar contraseña temporal segura de 8 caracteres
        import secrets
        import string
        caracteres = string.ascii_letters + string.digits + string.punctuation
        contrasena_temporal = ''.join(secrets.choice(caracteres) for _ in range(8))
        
        # Obtener datos personales
        correo = request.POST.get('correo_inst', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        curp = request.POST.get('curp', '').strip()
        fecha_nacimiento = request.POST.get('fecha_nacimiento', '').strip()
        genero = request.POST.get('genero', '').strip()
        carrera_id = request.POST.get('carrera_id', '').strip()
        semestre = request.POST.get('semestre', '').strip()
        departamento = request.POST.get('departamento', '').strip()
        cubiculo = request.POST.get('cubiculo', '').strip()
        grado_academico = request.POST.get('grado_academico', '').strip()
        puesto = request.POST.get('puesto', '').strip()
        nivel_prioridad = request.POST.get('nivel_prioridad', '').strip()
        id_ciclo_escolar = request.POST.get('id_ciclo_escolar', '').strip()
        estatus = request.POST.get('estatus', 'Activo').strip()
        direccion = construir_direccion(request.POST)
        
        # Diccionario para acumular TODOS los errores
        errores_campos = {}
        
        # ============ VALIDAR CAMPOS OBLIGATORIOS ============
        if not nombre:
            errores_campos['nombre'] = 'El nombre es obligatorio'
        elif not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', nombre):
            errores_campos['nombre'] = 'El nombre solo puede contener letras y espacios'
        
        if not apellido:
            errores_campos['apellido'] = 'El apellido es obligatorio'
        elif not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', apellido):
            errores_campos['apellido'] = 'El apellido solo puede contener letras y espacios'
        
        if not rol:
            errores_campos['rol'] = 'El rol es obligatorio'
        
        # ============ VALIDAR CAMPOS ESPECÍFICOS POR ROL ============
        if rol == 'alumno' and not carrera_id:
            errores_campos['carrera_id'] = 'Por favor selecciona una carrera de la lista para el alumno'
        
        if rol == 'alumno' and carrera_id:
            try:
                Carrera.objects.get(pk=carrera_id)
            except Carrera.DoesNotExist:
                errores_campos['carrera_id'] = 'La carrera seleccionada no es válida. Por favor selecciona otra'
        
        if rol == 'alumno' and not semestre:
            errores_campos['semestre'] = 'Por favor ingresa el semestre actual del alumno (1-12)'
        
        if rol == 'alumno' and semestre:
            try:
                semestre_int = int(semestre)
                if semestre_int < 1 or semestre_int > 12:
                    errores_campos['semestre'] = 'El semestre debe ser un número entre 1 y 12'
            except ValueError:
                errores_campos['semestre'] = 'El semestre debe ser un número válido (ejemplo: 3)'
        
        if rol == 'alumno' and estatus:
            estatus_validos = ['Activo', 'Baja', 'Egresado']
            if estatus not in estatus_validos:
                errores_campos['estatus'] = 'El estatus seleccionado no es válido'
        
        if rol == 'maestro' and not grado_academico:
            errores_campos['grado_academico'] = 'Por favor selecciona el grado académico del maestro'
        
        if rol == 'maestro' and grado_academico:
            grados_validos = ['Licenciatura', 'Maestria', 'Doctorado']
            if grado_academico not in grados_validos:
                errores_campos['grado_academico'] = 'Por favor selecciona un grado académico válido'
        
        if rol == 'maestro' and not departamento:
            errores_campos['departamento'] = 'Por favor ingresa el departamento donde trabaja el maestro'
        
        if rol == 'administrativo' and not puesto:
            errores_campos['puesto'] = 'Por favor ingresa el puesto del administrativo'
        
        if rol == 'administrativo' and not departamento:
            errores_campos['departamento'] = 'Por favor ingresa el departamento del administrativo'
        
        if rol == 'admin' and not puesto:
            errores_campos['puesto'] = 'Por favor selecciona el puesto del administrador'
        
        if rol == 'admin' and puesto:
            puestos_validos = ['Director', 'Subdirector', 'Auxiliar']
            if puesto not in puestos_validos:
                errores_campos['puesto'] = 'Por favor selecciona un puesto válido de la lista'
        
        if rol == 'admin' and nivel_prioridad:
            try:
                prioridad_int = int(nivel_prioridad)
                if prioridad_int < 1 or prioridad_int > 10:
                    errores_campos['nivel_prioridad'] = 'El nivel de prioridad debe ser un número entre 1 y 10'
            except ValueError:
                errores_campos['nivel_prioridad'] = 'El nivel de prioridad debe ser un número válido (ejemplo: 5)'
        
        # ============ VALIDAR DATOS PERSONALES ============
        # Validar teléfono (solo números y 10 dígitos si se proporciona)
        if telefono:
            telefono_limpio = telefono.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
            if not telefono_limpio.isdigit():
                errores_campos['telefono'] = 'El teléfono debe contener solo números (sin guiones ni espacios)'
            elif len(telefono_limpio) != 10:
                errores_campos['telefono'] = 'El teléfono debe tener exactamente 10 dígitos'
        
        # Validar correo duplicado y formato
        if correo:
            # Validar formato de email usando EmailValidator de Django
            try:
                validate_email(correo)
            except ValidationError:
                errores_campos['correo_inst'] = 'El formato del correo no es válido.'
            else:
                # Si el formato es válido, verificar duplicados
                if DatosPersonales.objects.filter(correo_inst__iexact=correo).exists():
                    errores_campos['correo_inst'] = 'Este correo electrónico ya está registrado en el sistema.'
        
        # Validar CURP duplicado y formato
        if curp:
            # Validar formato (18 caracteres, patrón correcto)
            curp_upper = curp.upper().strip()
            if len(curp_upper) != 18:
                errores_campos['curp'] = 'La CURP debe tener exactamente 18 caracteres. Verifica que esté completa'
            elif not re.match(r'^[A-Z]{4}\d{6}[A-Z0-9]{6}[A-Z0-9]{2}$', curp_upper):
                errores_campos['curp'] = 'CURP inválida.'
            elif DatosPersonales.objects.filter(curp__iexact=curp).exists():
                errores_campos['curp'] = 'Esta CURP ya está registrada en el sistema. Verifica los datos'
        
        # Validar fecha de nacimiento (no puede ser futura, debe ser al menos 10 años, y año mínimo 1900)
        if fecha_nacimiento:
            try:
                fecha_obj = _parse_fecha_nacimiento(fecha_nacimiento)
                if fecha_obj:
                    hoy = timezone.now().date()
                    if fecha_obj.year < 1900:
                        errores_campos['fecha_nacimiento'] = 'El año debe ser 1900 o posterior'
                    elif fecha_obj > hoy:
                        errores_campos['fecha_nacimiento'] = 'La fecha de nacimiento no puede ser una fecha futura. Verifica el año'
                    elif (hoy - fecha_obj).days < 3650:  # Menos de 10 años
                        errores_campos['fecha_nacimiento'] = 'La fecha de nacimiento debe corresponder a una persona de al menos 10 años'
            except:
                errores_campos['fecha_nacimiento'] = 'El formato de la fecha no es válido. Usa el formato: AAAA-MM-DD (ejemplo: 2000-05-15)'
        
        # Validar género choices
        if genero:
            generos_validos = ['M', 'F', 'otro']
            if genero not in generos_validos:
                errores_campos['genero'] = 'El género seleccionado no es válido'
        
        # ============ SI HAY ERRORES, MOSTRAR FORMULARIO CON TODOS LOS ERRORES ============
        if errores_campos:
            context = {
                'proximo_id': proximo_id,
                'siguientes': siguientes,
                'año_actual': año_actual,
                'periodo_actual': _periodo_actual(),
                'carreras': Carrera.objects.order_by('nombre').all(),
                'ciclos_escolares': CicloEscolar.objects.order_by('-fecha_inicio').all(),
                'perfil': {
                    'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
                    'matricula': request.session.get('usuario_matricula', 'N/A')
                },
                'form_data': request.POST,
                'errores_campos': errores_campos,
                'timestamp': timezone.now().timestamp(),
            }
            return render(request, 'administrador/AgregarUsuario.html', context)
        
        # ============ SI NO HAY ERRORES, CREAR USUARIO ============
        try:
            with transaction.atomic():
                # Crear usuario base
                try:
                    usuario = Usuarios(
                        nombre=nombre,
                        apellido=apellido,
                        rol=rol,
                        contrasena=contrasena_temporal
                    )
                    usuario.save()
                except ValidationError as e:
                    raise ValueError(_formatear_error_validacion(e))
                
                # Crear registro específico según rol
                if rol == 'alumno':
                    periodo_ingreso = request.POST.get('periodo_ingreso', '').strip().upper() or _periodo_actual()
                    Alumnos.objects.create(
                        id_usuario=usuario,
                        id_carrera=Carrera.objects.get(pk=carrera_id),
                        semestre=int(semestre) if semestre else 1,
                        periodo_ingreso=periodo_ingreso,
                        estatus=estatus if estatus else 'Activo'
                    )
                
                elif rol == 'maestro':
                    Maestros.objects.create(
                        id_usuario=usuario,
                        departamento=departamento,
                        cubiculo=cubiculo if cubiculo else None,
                        grado_academico=grado_academico
                    )
                
                elif rol == 'administrativo':
                    Administrativos.objects.create(
                        id_usuario=usuario,
                        departamento=departamento,
                        puesto=puesto
                    )
                
                elif rol == 'admin':
                    ciclo_escolar = None
                    if id_ciclo_escolar:
                        try:
                            ciclo_escolar = CicloEscolar.objects.get(pk=id_ciclo_escolar)
                        except CicloEscolar.DoesNotExist:
                            pass
                    
                    Administrador.objects.create(
                        id_usuario=usuario,
                        puesto=puesto,
                        nivel_prioridad=int(nivel_prioridad) if nivel_prioridad else 1,
                        id_ciclo_escolar=ciclo_escolar
                    )
                
                # Crear datos personales siempre (para consistencia)
                fecha_nacimiento_obj = _parse_fecha_nacimiento(fecha_nacimiento)
                
                try:
                    DatosPersonales.objects.create(
                        id_usuario=usuario,
                        correo_inst=correo if correo else None,
                        telefono=telefono if telefono else None,
                        curp=curp if curp else None,
                        fecha_nacimiento=fecha_nacimiento_obj,
                        genero=genero if genero else None,
                        direccion=direccion if direccion else None
                    )
                except ValidationError as e:
                    raise ValueError(_formatear_error_validacion(e))
                
                messages.success(request, f'¡Usuario creado! Matrícula: {usuario.matricula}. La contraseña temporal se ha generado. Por favor, cópiala ahora: {contrasena_temporal}')
                return redirect('gestion_usuarios')
                
        except ValueError as e:
            error_msg = str(e.args[0]) if e.args else str(e)
            messages.error(request, f'Error: {error_msg}')
            return redirect('agregar_usuario')
        except Exception as e:
            messages.error(request, f'Error al crear usuario: {str(e)}')
            return redirect('agregar_usuario')
    
    context = {
        'proximo_id': proximo_id,
        'siguientes': siguientes,
        'año_actual': año_actual,
        'periodo_actual': _periodo_actual(),
        'carreras': Carrera.objects.order_by('nombre').all(),
        'ciclos_escolares': CicloEscolar.objects.order_by('-fecha_inicio').all(),
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        },
        'errores_campos': {}
    }
    
    return render(request, 'administrador/AgregarUsuario.html', context)


def editar_usuario(request, usuario_id):
    """Vista para editar un usuario existente"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    usuario = get_object_or_404(Usuarios, id_usuario=usuario_id)
    
    # Obtener datos específicos según rol
    datos_especificos = None
    datos_personales = None
    
    try:
        if usuario.rol == 'alumno':
            datos_especificos = Alumnos.objects.get(id_usuario=usuario)
        elif usuario.rol == 'maestro':
            datos_especificos = Maestros.objects.get(id_usuario=usuario)
        elif usuario.rol == 'administrativo':
            datos_especificos = Administrativos.objects.get(id_usuario=usuario)
        elif usuario.rol == 'admin':
            datos_especificos = Administrador.objects.get(id_usuario=usuario)
    except:
        pass
    
    try:
        datos_personales = DatosPersonales.objects.get(id_usuario=usuario)
    except:
        pass
    
    if request.method == 'POST':
        # Obtener datos del formulario
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        correo = request.POST.get('correo_inst', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        curp = request.POST.get('curp', '').strip()
        fecha_nacimiento = request.POST.get('fecha_nacimiento', '').strip()
        genero = request.POST.get('genero', '').strip()
        carrera_id = request.POST.get('carrera_id', '').strip()
        semestre = request.POST.get('semestre', '').strip()
        departamento = request.POST.get('departamento', '').strip()
        cubiculo = request.POST.get('cubiculo', '').strip()
        grado_academico = request.POST.get('grado_academico', '').strip()
        puesto = request.POST.get('puesto', '').strip()
        nivel_prioridad = request.POST.get('nivel_prioridad', '').strip()
        estatus = request.POST.get('estatus', '').strip()
        direccion = construir_direccion(request.POST)
        
        # Diccionario para acumular errores
        errores_campos = {}
        
        # ============ VALIDAR CAMPOS OBLIGATORIOS ============
        if not nombre:
            errores_campos['nombre'] = 'El nombre es obligatorio'
        elif not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', nombre):
            errores_campos['nombre'] = 'El nombre solo puede contener letras y espacios'
        
        if not apellido:
            errores_campos['apellido'] = 'El apellido es obligatorio'
        elif not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', apellido):
            errores_campos['apellido'] = 'El apellido solo puede contener letras y espacios'
        
        # ============ VALIDAR CAMPOS ESPECÍFICOS POR ROL ============
        if usuario.rol == 'alumno' and carrera_id:
            try:
                Carrera.objects.get(pk=carrera_id)
            except Carrera.DoesNotExist:
                errores_campos['carrera_id'] = 'La carrera seleccionada no es válida. Por favor selecciona otra'
        
        if usuario.rol == 'alumno' and semestre:
            try:
                semestre_int = int(semestre)
                if semestre_int < 1 or semestre_int > 12:
                    errores_campos['semestre'] = 'El semestre debe ser un número entre 1 y 12'
            except ValueError:
                errores_campos['semestre'] = 'El semestre debe ser un número válido (ejemplo: 3)'
        
        if usuario.rol == 'alumno' and estatus:
            estatus_validos = ['Activo', 'Baja', 'Egresado']
            if estatus not in estatus_validos:
                errores_campos['estatus'] = 'El estatus seleccionado no es válido'
        
        if usuario.rol == 'maestro' and grado_academico:
            grados_validos = ['Licenciatura', 'Maestria', 'Doctorado']
            if grado_academico not in grados_validos:
                errores_campos['grado_academico'] = 'Por favor selecciona un grado académico válido'
        
        if usuario.rol == 'maestro' and not departamento:
            errores_campos['departamento'] = 'Por favor ingresa el departamento donde trabaja el maestro'
        
        if usuario.rol == 'administrativo' and not puesto:
            errores_campos['puesto'] = 'Por favor ingresa el puesto del administrativo'
        
        if usuario.rol == 'administrativo' and not departamento:
            errores_campos['departamento'] = 'Por favor ingresa el departamento del administrativo'
        
        if usuario.rol == 'admin' and puesto:
            puestos_validos = ['Director', 'Subdirector', 'Auxiliar']
            if puesto not in puestos_validos:
                errores_campos['puesto'] = 'Por favor selecciona un puesto válido de la lista'
        
        if usuario.rol == 'admin' and nivel_prioridad:
            try:
                prioridad_int = int(nivel_prioridad)
                if prioridad_int < 1 or prioridad_int > 10:
                    errores_campos['nivel_prioridad'] = 'El nivel de prioridad debe ser un número entre 1 y 10'
            except ValueError:
                errores_campos['nivel_prioridad'] = 'El nivel de prioridad debe ser un número válido (ejemplo: 5)'
        
        # ============ VALIDAR DATOS PERSONALES ============
        # Validar teléfono (solo números y 10 dígitos si se proporciona)
        if telefono:
            telefono_limpio = telefono.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
            if not telefono_limpio.isdigit():
                errores_campos['telefono'] = 'El teléfono debe contener solo números (sin guiones ni espacios)'
            elif len(telefono_limpio) != 10:
                errores_campos['telefono'] = 'El teléfono debe tener exactamente 10 dígitos'
        
        # Validar correo duplicado y formato
        if correo:
            # Validar formato de email usando EmailValidator de Django
            try:
                validate_email(correo)
            except ValidationError:
                errores_campos['correo_inst'] = 'El formato del correo no es válido.'
            else:
                # Si el formato es válido, verificar duplicados
                correos = DatosPersonales.objects.filter(correo_inst__iexact=correo).exclude(id_usuario=usuario.id_usuario)
                if correos.exists():
                    errores_campos['correo_inst'] = 'Este correo electrónico ya está registrado en el sistema.'
        
        # Validar CURP duplicado y formato
        if curp:
            # Validar formato (18 caracteres, patrón correcto)
            curp_upper = curp.upper().strip()
            if len(curp_upper) != 18:
                errores_campos['curp'] = 'La CURP debe tener exactamente 18 caracteres. Verifica que esté completa'
            elif not re.match(r'^[A-Z]{4}\d{6}[A-Z0-9]{6}[A-Z0-9]{2}$', curp_upper):
                errores_campos['curp'] = 'CURP inválida. Debe tener 18 caracteres'
            else:
                # Si el formato es válido, verificar duplicados
                curps = DatosPersonales.objects.filter(curp__iexact=curp).exclude(id_usuario=usuario.id_usuario)
                if curps.exists():
                    errores_campos['curp'] = 'Esta CURP ya está registrada en el sistema. Verifica los datos'
        
        # Validar fecha de nacimiento (no puede ser futura, debe ser al menos 10 años, y año mínimo 1900)
        if fecha_nacimiento:
            try:
                fecha_obj = _parse_fecha_nacimiento(fecha_nacimiento)
                if fecha_obj:
                    hoy = timezone.now().date()
                    if fecha_obj.year < 1900:
                        errores_campos['fecha_nacimiento'] = 'El año debe ser 1900 o posterior'
                    elif fecha_obj > hoy:
                        errores_campos['fecha_nacimiento'] = 'La fecha de nacimiento no puede ser una fecha futura. Verifica el año'
                    elif (hoy - fecha_obj).days < 3650:  # Menos de 10 años
                        errores_campos['fecha_nacimiento'] = 'La fecha de nacimiento debe corresponder a una persona de al menos 10 años'
            except:
                errores_campos['fecha_nacimiento'] = 'El formato de la fecha no es válido. Usa el formato: AAAA-MM-DD (ejemplo: 2000-05-15)'
        
        # Validar género choices
        if genero:
            generos_validos = ['M', 'F', 'otro']
            if genero not in generos_validos:
                errores_campos['genero'] = 'El género seleccionado no es válido'
        
        # ============ SI HAY ERRORES, MOSTRAR FORMULARIO CON ERRORES ============
        if errores_campos:
            context = {
                'usuario': usuario,
                'datos_especificos': datos_especificos,
                'datos_personales': datos_personales,
                'carreras': Carrera.objects.order_by('nombre').all(),
                'direccion_data': desglosar_direccion(datos_personales.direccion if datos_personales else None),
                'alumno_data': {
                    'id_carrera_id': datos_especificos.id_carrera_id if usuario.rol == 'alumno' and datos_especificos else '',
                    'semestre': datos_especificos.semestre if usuario.rol == 'alumno' and datos_especificos else '',
                    'periodo_ingreso': datos_especificos.periodo_ingreso if usuario.rol == 'alumno' and datos_especificos else '',
                    'estatus': datos_especificos.estatus if usuario.rol == 'alumno' and datos_especificos else 'Activo',
                },
                'maestro_data': {
                    'departamento': datos_especificos.departamento if usuario.rol == 'maestro' and datos_especificos else '',
                    'cubiculo': datos_especificos.cubiculo if usuario.rol == 'maestro' and datos_especificos else '',
                    'grado_academico': datos_especificos.grado_academico if usuario.rol == 'maestro' and datos_especificos else '',
                },
                'administrativo_data': {
                    'departamento': datos_especificos.departamento if usuario.rol == 'administrativo' and datos_especificos else '',
                    'puesto': datos_especificos.puesto if usuario.rol == 'administrativo' and datos_especificos else '',
                },
                'admin_data': {
                    'puesto': datos_especificos.puesto if usuario.rol == 'admin' and datos_especificos else '',
                    'nivel_prioridad': datos_especificos.nivel_prioridad if usuario.rol == 'admin' and datos_especificos else 1,
                },
                'datos_personales_data': {
                    'correo_inst': correo,
                    'telefono': telefono,
                    'curp': curp,
                    'fecha_nacimiento': fecha_nacimiento,
                    'genero': genero,
                    'direccion': direccion,
                },
                'form_data': request.POST,
                'errores_campos': errores_campos,
                'timestamp': timezone.now().timestamp(),
                'perfil': {
                    'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
                    'matricula': request.session.get('usuario_matricula', 'N/A')
                }
            }
            return render(request, 'administrador/EditarUsuario.html', context)
        
        # ============ SI NO HAY ERRORES, ACTUALIZAR USUARIO ============
        try:
            with transaction.atomic():
                # Actualizar datos básicos
                usuario.nombre = nombre
                usuario.apellido = apellido
                
                # Actualizar contraseña si se proporcionó
                nueva_contrasena = request.POST.get('contrasena', '').strip()
                if nueva_contrasena:
                    usuario.contrasena = nueva_contrasena

                try:
                    usuario.save()
                except ValidationError as e:
                    raise ValueError(_formatear_error_validacion(e))
                
                # Actualizar datos específicos según rol
                if usuario.rol == 'alumno' and datos_especificos:
                    carrera_id = request.POST.get('carrera_id', '').strip()
                    if carrera_id:
                        datos_especificos.id_carrera = Carrera.objects.get(pk=carrera_id)
                    semestre = request.POST.get('semestre', '').strip()
                    if semestre:
                        datos_especificos.semestre = int(semestre)
                    datos_especificos.estatus = request.POST.get('estatus', '').strip()
                    datos_especificos.save()
                
                elif usuario.rol == 'maestro' and datos_especificos:
                    datos_especificos.departamento = request.POST.get('departamento', '').strip()
                    datos_especificos.cubiculo = request.POST.get('cubiculo', '').strip() or None
                    datos_especificos.grado_academico = request.POST.get('grado_academico', '').strip()
                    datos_especificos.save()
                
                elif usuario.rol == 'administrativo' and datos_especificos:
                    datos_especificos.departamento = request.POST.get('departamento', '').strip()
                    datos_especificos.puesto = request.POST.get('puesto', '').strip()
                    datos_especificos.save()
                
                elif usuario.rol == 'admin' and datos_especificos:
                    datos_especificos.puesto = request.POST.get('puesto', '').strip()
                    nivel_prioridad = request.POST.get('nivel_prioridad', '').strip()
                    if nivel_prioridad:
                        datos_especificos.nivel_prioridad = int(nivel_prioridad)
                    datos_especificos.save()
                
                # Actualizar datos personales
                correo = request.POST.get('correo_inst', '').strip()
                telefono = request.POST.get('telefono', '').strip()
                curp = request.POST.get('curp', '').strip()
                fecha_nacimiento = request.POST.get('fecha_nacimiento', '').strip()
                genero = request.POST.get('genero', '').strip()
                direccion = construir_direccion(request.POST)
                
                fecha_nacimiento_obj = _parse_fecha_nacimiento(fecha_nacimiento)

                error_unicos = _validar_datos_personales_unicos(correo, curp, usuario.id_usuario)
                if error_unicos:
                    raise ValueError(error_unicos)

                if datos_personales:
                    datos_personales.correo_inst = correo if correo else None
                    datos_personales.telefono = telefono if telefono else None
                    datos_personales.curp = curp if curp else None
                    datos_personales.fecha_nacimiento = fecha_nacimiento_obj
                    datos_personales.genero = genero if genero else None
                    datos_personales.direccion = direccion if direccion else None
                    try:
                        datos_personales.save()
                    except ValidationError as e:
                        raise ValueError(_formatear_error_validacion(e))
                elif correo or telefono or curp or direccion or fecha_nacimiento_obj or genero:
                    try:
                        DatosPersonales.objects.create(
                            id_usuario=usuario,
                            correo_inst=correo if correo else None,
                            telefono=telefono if telefono else None,
                            curp=curp if curp else None,
                            fecha_nacimiento=fecha_nacimiento_obj,
                            genero=genero if genero else None,
                            direccion=direccion if direccion else None
                        )
                    except ValidationError as e:
                        raise ValueError(_formatear_error_validacion(e))
                
                messages.success(request, f'¡Cambios guardados! Se actualizó la información de {usuario.nombre}')
                return redirect('gestion_usuarios')
                
        except Exception as e:
            messages.error(request, f'Error al actualizar usuario: {str(e)}')
            return redirect('editar_usuario', usuario_id=usuario_id)
    
    context = {
        'usuario': usuario,
        'datos_especificos': datos_especificos,
        'datos_personales': datos_personales,
        'carreras': Carrera.objects.order_by('nombre').all(),
        'direccion_data': desglosar_direccion(datos_personales.direccion if datos_personales else None),
        'alumno_data': {
            'id_carrera_id': datos_especificos.id_carrera_id if usuario.rol == 'alumno' and datos_especificos else '',
            'semestre': datos_especificos.semestre if usuario.rol == 'alumno' and datos_especificos else '',
            'periodo_ingreso': datos_especificos.periodo_ingreso if usuario.rol == 'alumno' and datos_especificos else '',
            'estatus': datos_especificos.estatus if usuario.rol == 'alumno' and datos_especificos else 'Activo',
        },
        'maestro_data': {
            'departamento': datos_especificos.departamento if usuario.rol == 'maestro' and datos_especificos else '',
            'cubiculo': datos_especificos.cubiculo if usuario.rol == 'maestro' and datos_especificos else '',
            'grado_academico': datos_especificos.grado_academico if usuario.rol == 'maestro' and datos_especificos else '',
        },
        'administrativo_data': {
            'departamento': datos_especificos.departamento if usuario.rol == 'administrativo' and datos_especificos else '',
            'puesto': datos_especificos.puesto if usuario.rol == 'administrativo' and datos_especificos else '',
        },
        'admin_data': {
            'puesto': datos_especificos.puesto if usuario.rol == 'admin' and datos_especificos else '',
            'nivel_prioridad': datos_especificos.nivel_prioridad if usuario.rol == 'admin' and datos_especificos else 1,
        },
        'datos_personales_data': {
            'correo_inst': datos_personales.correo_inst if datos_personales else '',
            'telefono': datos_personales.telefono if datos_personales else '',
            'curp': datos_personales.curp if datos_personales else '',
            'fecha_nacimiento': datos_personales.fecha_nacimiento.isoformat() if datos_personales and datos_personales.fecha_nacimiento else '',
            'genero': datos_personales.genero if datos_personales else '',
            'direccion': datos_personales.direccion if datos_personales else '',
        },
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    }
    
    return render(request, 'administrador/EditarUsuario.html', context)


def eliminar_usuario(request, usuario_id):
    """Vista para eliminar un usuario"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    usuario = get_object_or_404(Usuarios, id_usuario=usuario_id)
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Prevenir eliminar al último administrador
                if usuario.rol == 'admin':
                    total_admins = Usuarios.objects.filter(rol='admin').count()
                    if total_admins <= 1:
                        messages.error(request, 'No puedes eliminar al último administrador del sistema')
                        return redirect('gestion_usuarios')
                
                # Eliminar en cascada (se eliminarán los registros relacionados)
                usuario_nombre = f"{usuario.nombre} {usuario.apellido}"
                admin_nombre = request.session.get('usuario_nombre', 'Administrador')
                logger.info(f'Usuario eliminado: {usuario_nombre} (ID: {usuario_id}) por {admin_nombre}')
                usuario.delete()
                
                messages.success(request, f'Usuario {usuario_nombre} eliminado exitosamente')
                return redirect('gestion_usuarios')
                
        except Exception as e:
            logger.error(f'Error al eliminar usuario {usuario_id}: {str(e)}')
            messages.error(request, f'Error al eliminar usuario: {str(e)}')
            return redirect('gestion_usuarios')
    
    context = {
        'usuario': usuario,
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    }
    
    return render(request, 'administrador/confirmar_eliminar.html', context)


def gestion_seguridad(request):
    """Vista para gestionar la seguridad del sistema"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    # Estadísticas de seguridad
    total_usuarios = Usuarios.objects.count()
    usuarios_activos = Usuarios.objects.filter(ultimo_acceso__isnull=False).count()
    usuarios_por_rol = Usuarios.objects.values('rol').count()
    
    # Últimos accesos
    ultimos_accesos = Usuarios.objects.filter(
        ultimo_acceso__isnull=False
    ).order_by('-ultimo_acceso')[:10]
    
    # Cuentas bloqueadas
    cuentas_bloqueadas = Usuarios.objects.filter(
        cuenta_bloqueada=True
    ).order_by('-fecha_bloqueo')[:10]
    
    # Logs de calificaciones recientes
    logs_calificaciones = LogCalificacion.objects.select_related(
        'id_usuario_modifico'
    ).order_by('-fecha_modificacion')[:10]
    
    context = {
        'total_usuarios': total_usuarios,
        'usuarios_activos': usuarios_activos,
        'usuarios_por_rol': usuarios_por_rol,
        'logs_acceso': ultimos_accesos,
        'cuentas_bloqueadas': cuentas_bloqueadas,
        'logs_calificaciones': logs_calificaciones,
        'ultimo_respaldo': 'N/A',  # Se puede implementar después
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    }
    
    return render(request, 'administrador/GestionSeguridad.html', context)


def desbloquear_cuenta(request, usuario_id):
    """Desbloquea una cuenta de usuario manualmente"""
    if not sesion_roles_permitidas(request, ('admin',)):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'No tienes permisos para realizar esta acción'})
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('gestion_seguridad')

    try:
        usuario = Usuarios.objects.get(id_usuario=usuario_id)

        if usuario.cuenta_bloqueada:
            usuario.cuenta_bloqueada = False
            usuario.intentos_fallidos_login = 0
            usuario.fecha_bloqueo = None
            usuario.save()

            admin_nombre = request.session.get('usuario_nombre', 'Administrador')
            logger.info(f'Cuenta desbloqueada: {usuario.nombre} {usuario.apellido} (ID: {usuario_id}) por {admin_nombre}')

            mensaje = f'✓ Cuenta de {usuario.nombre} {usuario.apellido} desbloqueada exitosamente.'

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'message': mensaje})

            messages.success(request, mensaje)
        else:
            mensaje = 'La cuenta no está bloqueada.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': mensaje})
            messages.warning(request, mensaje)

    except Usuarios.DoesNotExist:
        logger.warning(f'Intento de desbloquear usuario inexistente: {usuario_id}')
        mensaje = 'Usuario no encontrado.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': mensaje})
        messages.error(request, mensaje)
    except Exception as e:
        logger.error(f'Error al desbloquear cuenta {usuario_id}: {str(e)}')
        mensaje = f'Error al desbloquear cuenta: {str(e)}'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': mensaje})
        messages.error(request, mensaje)

    return redirect('gestion_seguridad')


@transaction.atomic
def restablecer_contrasena(request, usuario_id):
    """Vista para generar una nueva contraseña temporal a un usuario - Solo POST"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('gestion_usuarios')

    usuario = get_object_or_404(Usuarios, id_usuario=usuario_id)

    try:
        # Generar nueva contraseña temporal segura
        import secrets
        import string
        caracteres = string.ascii_letters + string.digits + string.punctuation
        nueva_contrasena = ''.join(secrets.choice(caracteres) for _ in range(8))

        # Guardar encriptada (el modelo se encarga de make_password en el save)
        usuario.contrasena = nueva_contrasena
        usuario.save()

        messages.success(request, f"¡Clave restablecida! Nueva contraseña para {usuario.nombre}: {nueva_contrasena}")
    except Exception as e:
        messages.error(request, f"Error al restablecer contraseña: {str(e)}")

    return redirect('gestion_usuarios')


def respaldo_bdd(request):
    """Vista para gestionar respaldos de la base de datos"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    from django.conf import settings
    import glob
    
    # Obtener lista de respaldos existentes
    backup_dir = settings.BACKUP_DIR
    backup_files = []
    
    if os.path.exists(backup_dir):
        for file_path in glob.glob(os.path.join(backup_dir, '*.sql')):
            try:
                stat = os.stat(file_path)
                size_mb = stat.st_size / (1024 * 1024)
                backup_files.append({
                    'nombre': os.path.basename(file_path),
                    'ruta': file_path,
                    'tamaño': f"{size_mb:.2f} MB",
                    'fecha': datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M:%S')
                })
            except:
                pass
    
    # Ordenar por fecha (más reciente primero)
    backup_files.sort(key=lambda x: x['fecha'], reverse=True)
    
    # Generar nombre para próximo respaldo
    fecha_respaldo = datetime.now().strftime('%Y%m%d_%H%M%S')
    ruta_respaldo = os.path.join(backup_dir, f"respaldo_schooltrack_{fecha_respaldo}.sql")
    
    context = {
        'perfil': {
            'nombre_completo': request.session.get('usuario_nombre', 'Administrador'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        },
        'fecha_respaldo': fecha_respaldo,
        'ruta_respaldo': ruta_respaldo,
        'backup_files': backup_files,
        'total_backups': len(backup_files)
    }
    
    return render(request, 'administrador/RespaldoBDD.html', context)


def ejecutar_respaldo(request):
    """Vista para ejecutar un respaldo de la base de datos"""
    if not sesion_roles_permitidas(request, ('admin',)):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': 'No tienes permisos para realizar esta acción'})
        return redirect('selector_rol')
    
    if request.method == 'POST':
        try:
            from django.conf import settings
            import subprocess
            
            # Obtener configuración de base de datos
            db_config = settings.DATABASES['default']
            backup_dir = settings.BACKUP_DIR
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = os.path.join(backup_dir, f"respaldo_schooltrack_{timestamp}.sql")
            
            # Verificar si el archivo ya existe (confirmación de sobrescritura)
            if os.path.exists(backup_file):
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': False, 
                        'message': f'El archivo {os.path.basename(backup_file)} ya existe. Elimínelo manualmente o espere al próximo respaldo.'
                    })
                messages.error(request, f'El archivo ya existe. Elimínelo manualmente o espere al próximo respaldo.')
                return redirect('respaldo_bdd')
            
            # Construir comando pg_dump
            pg_dump_cmd = [
                'pg_dump',
                f'--host={db_config["HOST"]}',
                f'--port={db_config["PORT"]}',
                f'--username={db_config["USER"]}',
                f'--dbname={db_config["NAME"]}',
                '--no-password',
                '--format=plain',
                '--no-owner',
                '--no-acl',
                f'--file={backup_file}'
            ]
            
            # Establecer variable de entorno para contraseña
            env = os.environ.copy()
            env['PGPASSWORD'] = db_config['PASSWORD']
            
            # Ejecutar pg_dump
            result = subprocess.run(pg_dump_cmd, env=env, capture_output=True, text=True)
            
            if result.returncode != 0:
                error_msg = result.stderr or 'Error desconocido al ejecutar pg_dump'
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'message': f'Error al ejecutar pg_dump: {error_msg}'})
                messages.error(request, f'Error al ejecutar pg_dump: {error_msg}')
                return redirect('respaldo_bdd')
            
            # Obtener tamaño del archivo
            file_size = os.path.getsize(backup_file)
            size_mb = file_size / (1024 * 1024)
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True, 
                    'message': f'Respaldo realizado exitosamente. Tamaño: {size_mb:.2f} MB',
                    'filename': os.path.basename(backup_file),
                    'size_mb': f'{size_mb:.2f} MB'
                })
            
            messages.success(request, f'Respaldo realizado exitosamente. Tamaño: {size_mb:.2f} MB')
            return redirect('respaldo_bdd')
            
        except Exception as e:
            logger.error(f"Error al realizar respaldo: {str(e)}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': f'Error al realizar respaldo: {str(e)}'})
            
            messages.error(request, f'Error al realizar respaldo: {str(e)}')
            return redirect('respaldo_bdd')
    
    return redirect('respaldo_bdd')


def descargar_respaldo_especifico(request, filename):
    """Vista para descargar un respaldo específico"""
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    try:
        from django.conf import settings
        import mimetypes
        
        backup_dir = settings.BACKUP_DIR
        file_path = os.path.join(backup_dir, filename)
        
        # Verificar que el archivo existe y está dentro del directorio de respaldos
        if not os.path.exists(file_path) or not os.path.abspath(file_path).startswith(os.path.abspath(backup_dir)):
            messages.error(request, 'El archivo de respaldo no existe o no es válido')
            return redirect('respaldo_bdd')
        
        # Determinar el tipo MIME
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = 'application/octet-stream'
        
        # Leer el archivo
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        # Crear respuesta HTTP
        response = HttpResponse(file_content, content_type=mime_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Content-Length'] = str(len(file_content))
        
        return response
        
    except Exception as e:
        logger.error(f"Error al descargar respaldo: {str(e)}")
        messages.error(request, f'Error al descargar respaldo: {str(e)}')
        return redirect('respaldo_bdd')

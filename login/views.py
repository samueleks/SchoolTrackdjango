import io
import json
import logging
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

import pytesseract
from PIL import Image

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, HttpResponseNotAllowed, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction, models
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from fpdf import FPDF

from .models import (
    Usuarios,
    Alumnos,
    Maestros,
    Administrativos,
    Administrador,
    DatosPersonales,
    Materia,
    LogCalificacion,
    Grupo,
    Inscripcion,
    AsignacionMateria,
    Horario,
    Asistencia,
    Calificacion,
    CicloEscolar,
)


logger = logging.getLogger(__name__)

OCR_UPLOAD_MAX_BYTES = int(getattr(settings, 'OCR_UPLOAD_MAX_BYTES', 5 * 1024 * 1024))
ESCALA_MAXIMA_CALIFICACION = Decimal('100')
MINIMO_APROBATORIO_CALIFICACION = Decimal(str(getattr(settings, 'MINIMO_APROBATORIO_CALIFICACION', 70)))

pytesseract.pytesseract.tesseract_cmd = getattr(
    settings,
    'TESSERACT_CMD',
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
)


def _safe_server_error(exc: BaseException) -> str:
    if settings.DEBUG:
        return str(exc)
    return 'Error interno'


def _formatear_calificacion_visible(valor, *, decimals: int = 1, mostrar_na_si_menor: bool = True) -> str:
    """Convierte una calificación guardada a texto visible respetando el mínimo aprobatorio."""
    if valor is None:
        return '—'

    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return '—'

    if mostrar_na_si_menor and numero < MINIMO_APROBATORIO_CALIFICACION:
        return 'NA'

    return f'{numero:.{decimals}f}'


def sesion_roles_permitidas(request, roles: tuple) -> bool:
    role = request.session.get('usuario_rol')
    return role is not None and role in roles


def _verificar_bloqueo_cuenta(usuario):
    """
    Verifica si una cuenta está bloqueada y maneja el desbloqueo automático.
    Returns: (bool, str) - (puede_continuar, mensaje_error)
    """
    if usuario.cuenta_bloqueada:
        from datetime import timedelta
        tiempo_bloqueo = timezone.now() - usuario.fecha_bloqueo
        if tiempo_bloqueo >= timedelta(minutes=30):
            # Desbloquear automáticamente
            usuario.cuenta_bloqueada = False
            usuario.intentos_fallidos_login = 0
            usuario.fecha_bloqueo = None
            usuario.save()
            return True, None
        else:
            minutos_restantes = 30 - int(tiempo_bloqueo.total_seconds() / 60)
            error_msg = f'Cuenta bloqueada. Se desbloqueará en {minutos_restantes} minutos o contacte al administrador.'
            return False, error_msg
    return True, None


def _registrar_intento_fallido(usuario):
    """
    Registra un intento fallido de login y bloquea la cuenta si es necesario.
    Returns: str - mensaje de error
    """
    usuario.intentos_fallidos_login += 1
    
    # Bloquear cuenta después de 5 intentos fallidos
    if usuario.intentos_fallidos_login >= 5:
        usuario.cuenta_bloqueada = True
        usuario.fecha_bloqueo = timezone.now()
        error_msg = 'Cuenta bloqueada por demasiados intentos fallidos. Contacte al administrador.'
    else:
        error_msg = f'Matrícula o contraseña incorrectos. Intentos restantes: {5 - usuario.intentos_fallidos_login}'
    
    usuario.save()
    return error_msg


def _desglosar_direccion(direccion: str | None) -> dict:
    """Convierte la dirección guardada en texto a campos separados para mostrarla en el perfil."""
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


def _parse_fecha_iso(valor: str | None):
    if not valor:
        return None
    return datetime.strptime(valor, '%Y-%m-%d').date()


def _dia_semana_es(fecha_obj: date) -> str:
    dias = {
        0: 'Lunes',
        1: 'Martes',
        2: 'Miércoles',
        3: 'Jueves',
        4: 'Viernes',
        5: 'Sábado',
        6: 'Domingo',
    }
    return dias[fecha_obj.weekday()]


def _periodo_actual() -> str:
    hoy = timezone.now()
    if hoy.month <= 6:
        periodo = 'A'
    elif hoy.month >= 8:
        periodo = 'B'
    else:
        periodo = 'A'
    return f"{hoy.year}-{periodo}"


def _serializar_horario(horario: Horario) -> dict:
    materia = horario.id_asignacion_materia.id_materia
    maestro = horario.id_asignacion_materia.id_maestro.id_usuario
    grupo = horario.id_asignacion_materia.id_grupo
    # Generar segmentos por unidad (p. ej. 13:30-14:30, 14:30-15:30, ...)
    segmentos = _split_horario_en_unidades(horario)
    return {
        'id_horario': horario.id_horario,
        'dia_semana': horario.dia_semana,
        'hora_inicio': horario.hora_inicio.strftime('%H:%M'),
        'hora_fin': horario.hora_fin.strftime('%H:%M'),
        'estatus': horario.estatus,
        'aula': horario.aula or '',
        'materia': {
            'id': materia.id_materia,
            'codigo': materia.clave,
            'nombre': materia.nombre,
        },
        'maestro': {
            'id': maestro.id_usuario,
            'nombre': f'{maestro.nombre} {maestro.apellido}',
        },
        'grupo': {
            'id': grupo.id_grupo,
            'clave': grupo.clave,
            'nombre': grupo.nombre,
        },
        'segmentos': segmentos,
    }


def _split_horario_en_unidades(horario: Horario) -> list:
    """
    Divide un objeto Horario en segmentos de 1 hora (unidades).
    Ej: 13:30-16:30 -> [{unidad:1, hora_inicio:'13:30', hora_fin:'14:30'}, ...]
    Si queda un remanente menor a 1 hora, se añade como último segmento sólo si dura >= 30 minutos.
    """
    from datetime import datetime, timedelta

    fmt = '%H:%M'
    # Normalizar cadenas HH:MM
    inicio_s = str(horario.hora_inicio)[:5]
    fin_s = str(horario.hora_fin)[:5]
    inicio_dt = datetime.strptime(inicio_s, fmt)
    fin_dt = datetime.strptime(fin_s, fmt)

    segmentos = []
    current = inicio_dt
    unidad = 1
    while current + timedelta(hours=1) <= fin_dt:
        seg_inicio = current.strftime(fmt)
        seg_fin_dt = current + timedelta(hours=1)
        seg_fin = seg_fin_dt.strftime(fmt)
        segmentos.append({'unidad': unidad, 'hora_inicio': seg_inicio, 'hora_fin': seg_fin})
        unidad += 1
        current = seg_fin_dt

    # Agregar segmento final si queda tiempo >= 30 minutos
    if current < fin_dt:
        remaining = fin_dt - current
        if remaining >= timedelta(minutes=30):
            segmentos.append({'unidad': unidad, 'hora_inicio': current.strftime(fmt), 'hora_fin': fin_dt.strftime(fmt)})

    return segmentos


def selector_rol(request):
    return render(request, 'selector_rol.html')


def logout_view(request):
    # Limpiar sesión
    request.session.flush()
    return redirect('selector_rol')


def _perfil_alumno(request):
    """Construye el perfil del alumno con datos disponibles en sesión y BDD."""
    alumno = Alumnos.objects.select_related('id_usuario', 'id_carrera').filter(
        pk=request.session.get('alumno_id')
    ).first()

    perfil = {
        'nombre_completo': request.session.get('usuario_nombre', 'Usuario'),
        'matricula': request.session.get('usuario_matricula', 'N/A'),
    }

    if alumno:
        perfil.update({
            'id_usuario': alumno.id_usuario.id_usuario,
            'matricula': alumno.id_usuario.matricula,
            'nombre_completo': f"{alumno.id_usuario.nombre} {alumno.id_usuario.apellido}",
            'carrera': str(alumno.id_carrera) if alumno.id_carrera else '---',
            'semestre': alumno.semestre,
            'estatus': alumno.estatus,
            'periodo_ingreso': alumno.periodo_ingreso,
            'periodo_actual': _periodo_actual(),
        })

    try:
        datos_personales = DatosPersonales.objects.get(id_usuario=alumno.id_usuario) if alumno else None
    except DatosPersonales.DoesNotExist:
        datos_personales = None

    if datos_personales:
        direccion = _desglosar_direccion(datos_personales.direccion)
        perfil.update({
            'telefono': datos_personales.telefono or '',
            'correo_institucional': datos_personales.correo_inst or '',
            'direccion': datos_personales.direccion or '',
            **direccion,
            'curp': datos_personales.curp or '',
        })

    return perfil


def _perfil_maestro(request):
    """Construye el perfil del maestro con datos disponibles en sesión y BDD."""
    maestro = Maestros.objects.select_related('id_usuario').filter(
        pk=request.session.get('maestro_id')
    ).first()

    perfil = {
        'nombre_completo': request.session.get('usuario_nombre', 'Usuario'),
        'nombre': '',
        'apellido': '',
        'matricula': request.session.get('usuario_matricula', 'N/A'),
        'correo_institucional': '',
        'telefono': '',
        'curp': '',
        'fecha_nacimiento': '',
        'genero': '',
        'direccion': '',
        'calle': '',
        'numero_exterior': '',
        'numero_interior': '',
        'colonia': '',
        'ciudad': '',
        'estado': '',
        'cp': '',
        'departamento': '',
        'cubiculo': '',
        'grado_academico': '',
    }

    if maestro:
        usuario = maestro.id_usuario
        perfil.update({
            'nombre_completo': f"{usuario.nombre} {usuario.apellido}",
            'nombre': usuario.nombre,
            'apellido': usuario.apellido,
            'matricula': usuario.matricula,
            'departamento': maestro.departamento,
            'cubiculo': maestro.cubiculo or '',
            'grado_academico': maestro.grado_academico,
        })

        try:
            datos_personales = DatosPersonales.objects.get(id_usuario=usuario)
        except DatosPersonales.DoesNotExist:
            datos_personales = None

        if datos_personales:
            direccion = _desglosar_direccion(datos_personales.direccion)
            perfil.update({
                'correo_institucional': datos_personales.correo_inst or '',
                'telefono': datos_personales.telefono or '',
                'curp': datos_personales.curp or '',
                'fecha_nacimiento': datos_personales.fecha_nacimiento.strftime('%d/%m/%Y') if datos_personales.fecha_nacimiento else '',
                'genero': datos_personales.get_genero_display() if datos_personales.genero else '',
                'direccion': datos_personales.direccion or '',
                'calle': direccion['calle'],
                'numero_exterior': direccion['numero_exterior'],
                'numero_interior': direccion['numero_interior'],
                'colonia': direccion['colonia'],
                'ciudad': direccion['municipio'],
                'estado': direccion['estado'],
                'cp': direccion['cp'],
            })

    return perfil


def _contexto_maestro(request) -> dict:
    """Contexto base para las pantallas del maestro."""
    materias = Materia.objects.filter(activo=True).order_by('nombre')
    maestro = Maestros.objects.select_related('id_usuario').filter(pk=request.session.get('maestro_id')).first()
    asignaciones = []
    if maestro:
        asignaciones = (
            AsignacionMateria.objects.select_related(
                'id_materia',
                'id_grupo',
                'id_ciclo_escolar',
                'id_maestro__id_usuario',
            )
            .filter(id_maestro=maestro, estatus='Activa')
            .order_by('id_grupo__clave', 'id_materia__nombre')
        )
    return {
        'perfil': _perfil_maestro(request),
        'materias_docente': materias,
        'materias': materias,
        'asignaciones_docente': asignaciones,
        'fecha_hoy': timezone.localdate().isoformat(),
        'lista_calificaciones': [],
        'reportes': [],
    }


def _perfil_administrativo(request):
    """Construye el perfil del administrativo con datos disponibles en sesión y BDD."""
    administrativo = Administrativos.objects.select_related('id_usuario').filter(
        pk=request.session.get('administrativo_id')
    ).first()

    perfil = {
        'nombre_completo': request.session.get('usuario_nombre', 'Usuario'),
        'matricula': request.session.get('usuario_matricula', 'N/A'),
        'departamento': '',
        'puesto': '',
    }

    if administrativo:
        perfil.update({
            'nombre_completo': f"{administrativo.id_usuario.nombre} {administrativo.id_usuario.apellido}",
            'matricula': administrativo.id_usuario.matricula,
            'departamento': administrativo.departamento,
            'puesto': administrativo.puesto,
        })

    return perfil


# ==================== VISTAS DE LOGIN POR ROL ====================

def login_alumno(request):
    if request.method == 'POST':
        is_ajax = request.POST.get('ajax') == '1'
        matricula = request.POST.get('usuario', '').strip()
        contrasena = request.POST.get('contrasena', '').strip()
        
        if not matricula or not contrasena:
            error_msg = 'Completa matrícula y contraseña para continuar.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_alumno.html')
        
        try:
            usuario = Usuarios.objects.get(matricula=matricula, rol='alumno')
            
            # Verificar si la cuenta está bloqueada
            puede_continuar, error_bloqueo = _verificar_bloqueo_cuenta(usuario)
            if not puede_continuar:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_bloqueo})
                messages.error(request, error_bloqueo)
                return render(request, 'login_alumno.html')
            
            if usuario.verificar_contrasena(contrasena):
                # Verificar que exista registro en tabla Alumnos
                try:
                    alumno = Alumnos.objects.get(id_usuario=usuario)
                    
                    # Reiniciar intentos fallidos tras login exitoso
                    usuario.intentos_fallidos_login = 0
                    usuario.ultimo_acceso = timezone.now()
                    usuario.save()
                    
                    # Guardar sesión (solo valores serializables)
                    request.session['usuario_id'] = str(usuario.id_usuario)
                    request.session['usuario_matricula'] = str(usuario.matricula)
                    request.session['usuario_nombre'] = f"{usuario.nombre} {usuario.apellido}"
                    request.session['usuario_rol'] = 'alumno'
                    request.session['alumno_id'] = str(alumno.id_usuario_id)
                    
                    if is_ajax:
                        return JsonResponse({'success': True, 'redirect': '/dashboard/alumno/'})
                    return redirect('dashboard_alumno')
                    
                except Alumnos.DoesNotExist:
                    error_msg = 'Usuario no tiene registro de alumno activo.'
                    if is_ajax:
                        return JsonResponse({'success': False, 'error': error_msg})
                    messages.error(request, error_msg)
                    return render(request, 'login_alumno.html')
            else:
                # Registrar intento fallido usando función auxiliar
                error_msg = _registrar_intento_fallido(usuario)
                
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_msg})
                messages.error(request, error_msg)
                return render(request, 'login_alumno.html')
                
        except Usuarios.DoesNotExist:
            error_msg = 'Matrícula o contraseña incorrectos.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_alumno.html')
    
    return render(request, 'login_alumno.html')


def login_maestro(request):
    if request.method == 'POST':
        is_ajax = request.POST.get('ajax') == '1'
        matricula = request.POST.get('usuario', '').strip()
        contrasena = request.POST.get('contrasena', '').strip()
        
        if not matricula or not contrasena:
            error_msg = 'Completa matrícula y contraseña para continuar.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_maestro.html')
        
        try:
            usuario = Usuarios.objects.get(matricula=matricula, rol='maestro')
            
            # Verificar si la cuenta está bloqueada
            puede_continuar, error_bloqueo = _verificar_bloqueo_cuenta(usuario)
            if not puede_continuar:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_bloqueo})
                messages.error(request, error_bloqueo)
                return render(request, 'login_maestro.html')
            
            if usuario.verificar_contrasena(contrasena):
                # Verificar que exista registro en tabla Maestros
                try:
                    maestro = Maestros.objects.get(id_usuario=usuario)
                    
                    # Reiniciar intentos fallidos tras login exitoso
                    usuario.intentos_fallidos_login = 0
                    usuario.ultimo_acceso = timezone.now()
                    usuario.save()
                    
                    # Guardar sesión (solo valores serializables)
                    request.session['usuario_id'] = str(usuario.id_usuario)
                    request.session['usuario_matricula'] = str(usuario.matricula)
                    request.session['usuario_nombre'] = f"{usuario.nombre} {usuario.apellido}"
                    request.session['usuario_rol'] = 'maestro'
                    request.session['maestro_id'] = str(maestro.id_usuario_id)
                    
                    if is_ajax:
                        return JsonResponse({'success': True, 'redirect': '/dashboard/maestro/'})
                    return redirect('dashboard_maestro')
                    
                except Maestros.DoesNotExist:
                    error_msg = 'Usuario no tiene registro de maestro activo.'
                    if is_ajax:
                        return JsonResponse({'success': False, 'error': error_msg})
                    messages.error(request, error_msg)
                    return render(request, 'login_maestro.html')
            else:
                # Registrar intento fallido usando función auxiliar
                error_msg = _registrar_intento_fallido(usuario)
                
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_msg})
                messages.error(request, error_msg)
                return render(request, 'login_maestro.html')
                
        except Usuarios.DoesNotExist:
            error_msg = 'Matrícula o contraseña incorrectos.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_maestro.html')
    
    return render(request, 'login_maestro.html')


def login_administrativo(request):
    if request.method == 'POST':
        is_ajax = request.POST.get('ajax') == '1'
        matricula = request.POST.get('usuario', '').strip()
        contrasena = request.POST.get('contrasena', '').strip()
        
        if not matricula or not contrasena:
            error_msg = 'Completa matrícula y contraseña para continuar.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_administrativo.html')
        
        try:
            usuario = Usuarios.objects.get(matricula=matricula, rol='administrativo')
            
            # Verificar si la cuenta está bloqueada
            puede_continuar, error_bloqueo = _verificar_bloqueo_cuenta(usuario)
            if not puede_continuar:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_bloqueo})
                messages.error(request, error_bloqueo)
                return render(request, 'login_administrativo.html')
            
            if usuario.verificar_contrasena(contrasena):
                # Verificar que exista registro en tabla Administrativos
                try:
                    administrativo = Administrativos.objects.get(id_usuario=usuario)
                    
                    # Reiniciar intentos fallidos tras login exitoso
                    usuario.intentos_fallidos_login = 0
                    usuario.ultimo_acceso = timezone.now()
                    usuario.save()
                    
                    # Guardar sesión (solo valores serializables)
                    request.session['usuario_id'] = str(usuario.id_usuario)
                    request.session['usuario_matricula'] = str(usuario.matricula)
                    request.session['usuario_nombre'] = f"{usuario.nombre} {usuario.apellido}"
                    request.session['usuario_rol'] = 'administrativo'
                    request.session['administrativo_id'] = str(administrativo.id_usuario_id)
                    
                    if is_ajax:
                        return JsonResponse({'success': True, 'redirect': '/dashboard/administrativo/'})
                    return redirect('dashboard_administrativo')
                    
                except Administrativos.DoesNotExist:
                    error_msg = 'Usuario no tiene registro de administrativo activo.'
                    if is_ajax:
                        return JsonResponse({'success': False, 'error': error_msg})
                    messages.error(request, error_msg)
                    return render(request, 'login_administrativo.html')
            else:
                # Registrar intento fallido usando función auxiliar
                error_msg = _registrar_intento_fallido(usuario)
                
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_msg})
                messages.error(request, error_msg)
                return render(request, 'login_administrativo.html')
                
        except Usuarios.DoesNotExist:
            error_msg = 'Matrícula o contraseña incorrectos.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_administrativo.html')
    
    return render(request, 'login_administrativo.html')


def login_administrador(request):
    if request.method == 'POST':
        is_ajax = request.POST.get('ajax') == '1'
        matricula = request.POST.get('usuario', '').strip()
        contrasena = request.POST.get('contrasena', '').strip()
        
        if not matricula or not contrasena:
            error_msg = 'Completa matrícula y contraseña para continuar.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_administrador.html')
        
        try:
            usuario = Usuarios.objects.get(matricula=matricula, rol='admin')
            
            # Verificar si la cuenta está bloqueada
            puede_continuar, error_bloqueo = _verificar_bloqueo_cuenta(usuario)
            if not puede_continuar:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_bloqueo})
                messages.error(request, error_bloqueo)
                return render(request, 'login_administrador.html')
            
            if usuario.verificar_contrasena(contrasena):
                # Verificar que exista registro en tabla Administrador
                try:
                    administrador = Administrador.objects.get(id_usuario=usuario)
                    
                    # Reiniciar intentos fallidos tras login exitoso
                    usuario.intentos_fallidos_login = 0
                    usuario.ultimo_acceso = timezone.now()
                    usuario.save()
                    
                    # Guardar sesión (solo valores serializables)
                    request.session['usuario_id'] = str(usuario.id_usuario)
                    request.session['usuario_matricula'] = str(usuario.matricula)
                    request.session['usuario_nombre'] = f"{usuario.nombre} {usuario.apellido}"
                    request.session['usuario_rol'] = 'admin'
                    request.session['administrador_id'] = str(administrador.id_usuario_id)
                    request.session['nivel_prioridad'] = int(administrador.nivel_prioridad)
                    
                    if is_ajax:
                        return JsonResponse({'success': True, 'redirect': '/dashboard/administrador/'})
                    return redirect('dashboard_administrador')
                    
                except Administrador.DoesNotExist:
                    error_msg = 'Usuario no tiene registro de administrador activo.'
                    if is_ajax:
                        return JsonResponse({'success': False, 'error': error_msg})
                    messages.error(request, error_msg)
                    return render(request, 'login_administrador.html')
            else:
                # Registrar intento fallido usando función auxiliar
                error_msg = _registrar_intento_fallido(usuario)
                
                if is_ajax:
                    return JsonResponse({'success': False, 'error': error_msg})
                messages.error(request, error_msg)
                return render(request, 'login_administrador.html')
                
        except Usuarios.DoesNotExist:
            error_msg = 'Matrícula o contraseña incorrectos.'
            if is_ajax:
                return JsonResponse({'success': False, 'error': error_msg})
            messages.error(request, error_msg)
            return render(request, 'login_administrador.html')
    
    return render(request, 'login_administrador.html')


# ==================== VISTAS DE DASHBOARD ====================

def dashboard_alumno(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    return render(request, 'alumno/alumno.html', {'perfil': perfil})


def inicio_interfaces_alumnos(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    return render(request, 'alumno/alumno.html', {'perfil': perfil})


def consultar_calificaciones(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()
    rows = []
    promedio_general = None

    if alumno:
        inscripciones = list(
            Inscripcion.objects.select_related('id_grupo', 'id_ciclo_escolar')
            .filter(id_alumno=alumno, estatus='Activa')
            .order_by('id_grupo__clave')
        )
        inscripcion_por_grupo = {inscripcion.id_grupo_id: inscripcion for inscripcion in inscripciones}
        grupos_ids = list(inscripcion_por_grupo.keys())

        asignaciones = (
            AsignacionMateria.objects.select_related('id_materia', 'id_grupo', 'id_ciclo_escolar')
            .filter(id_grupo_id__in=grupos_ids, estatus='Activa')
            .order_by('id_materia__nombre')
        )

        calificaciones_qs = Calificacion.objects.filter(
            id_inscripcion__in=[inscripcion.id_inscripcion for inscripcion in inscripciones],
            id_asignacion_materia__in=asignaciones,
        ).select_related('id_inscripcion', 'id_asignacion_materia', 'id_asignacion_materia__id_materia')

        calificaciones_por_asignacion = {}
        for calificacion in calificaciones_qs:
            calificaciones_por_asignacion.setdefault(calificacion.id_asignacion_materia_id, {})[calificacion.unidad] = calificacion

        promedios = []
        for asignacion in asignaciones:
            mapa_unidades = calificaciones_por_asignacion.get(asignacion.id_asignacion_materia, {})
            valores = []
            unidades = []
            for unidad_num in range(1, 7):
                calificacion = mapa_unidades.get(unidad_num)
                if calificacion is not None:
                    valor = Decimal(str(calificacion.calificacion))
                    unidades.append(_formatear_calificacion_visible(valor))
                    valores.append(valor)
                else:
                    unidades.append('—')

            promedio = sum(valores) / len(valores) if valores else None
            tiene_unidad_menor_70 = any(v < MINIMO_APROBATORIO_CALIFICACION for v in valores)

            if promedio is not None and not tiene_unidad_menor_70:
                promedios.append(promedio)

            rows.append({
                'materia': asignacion.id_materia.nombre,
                'codigo': asignacion.id_materia.clave,
                'grupo': asignacion.id_grupo.clave,
                'unidades': unidades,
                'promedio': 'NA' if tiene_unidad_menor_70 else (_formatear_calificacion_visible(promedio) if promedio is not None else '—'),
            })

        if promedios:
            promedio_general = sum(promedios) / len(promedios)

    context = {
        'perfil': perfil,
        'calificaciones_rows': rows,
        'promedio_general': _formatear_calificacion_visible(promedio_general, mostrar_na_si_menor=True) if promedio_general is not None else '0.0',
        'hay_calificaciones': bool(rows),
    }
    return render(request, 'alumno/calificaciones.html', context)


def consultar_asistencias(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()
    rows = []
    total_registros = 0
    total_presentes = 0
    total_ausentes = 0
    total_tarde = 0
    total_justificado = 0

    asignacion_id = request.GET.get('asignacion_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()
    fecha_inicio_str = request.GET.get('fecha_inicio', '').strip()
    fecha_fin_str = request.GET.get('fecha_fin', '').strip()
    reporte_solicitado = bool(request.GET)

    if alumno:
        inscripciones = list(
            Inscripcion.objects.select_related('id_grupo', 'id_ciclo_escolar')
            .filter(id_alumno=alumno, estatus='Activa')
            .order_by('id_grupo__clave')
        )
        grupos_ids = [inscripcion.id_grupo_id for inscripcion in inscripciones]
        asignaciones = (
            AsignacionMateria.objects.select_related('id_materia', 'id_grupo', 'id_ciclo_escolar')
            .filter(id_grupo_id__in=grupos_ids, estatus='Activa')
            .order_by('id_materia__nombre')
        )

        asistencias_qs = (
            Asistencia.objects.select_related(
                'id_inscripcion__id_alumno__id_usuario',
                'id_horario__id_asignacion_materia__id_materia',
                'id_horario__id_asignacion_materia__id_grupo',
                'id_horario__id_asignacion_materia__id_maestro__id_usuario',
            )
            .filter(
                id_inscripcion__in=[inscripcion.id_inscripcion for inscripcion in inscripciones],
                id_horario__id_asignacion_materia__in=asignaciones,
            )
            .order_by('-fecha_asistencia', '-unidad', 'id_horario__id_asignacion_materia__id_materia__nombre')
        )

        if asignacion_id:
            asistencias_qs = asistencias_qs.filter(id_horario__id_asignacion_materia__pk=asignacion_id)
        if unidad_str:
            try:
                asistencias_qs = asistencias_qs.filter(unidad=int(unidad_str))
            except ValueError:
                pass
        fecha_inicio = _parse_fecha_iso(fecha_inicio_str)
        fecha_fin = _parse_fecha_iso(fecha_fin_str)
        if fecha_inicio:
            asistencias_qs = asistencias_qs.filter(fecha_asistencia__gte=fecha_inicio)
        if fecha_fin:
            asistencias_qs = asistencias_qs.filter(fecha_asistencia__lte=fecha_fin)

        for asistencia in asistencias_qs[:250]:
            usuario_alumno = asistencia.id_inscripcion.id_alumno.id_usuario
            asignacion = asistencia.id_horario.id_asignacion_materia
            total_registros += 1
            if asistencia.estatus == 'Presente':
                total_presentes += 1
            elif asistencia.estatus == 'Ausente':
                total_ausentes += 1
            elif asistencia.estatus == 'Tarde':
                total_tarde += 1
            elif asistencia.estatus == 'Justificado':
                total_justificado += 1

            rows.append({
                'fecha': asistencia.fecha_asistencia.strftime('%d/%m/%Y'),
                'unidad': asistencia.unidad,
                'materia': f'{asignacion.id_materia.clave} - {asignacion.id_materia.nombre}',
                'grupo': asignacion.id_grupo.clave,
                'horario': f'{asistencia.id_horario.dia_semana} {asistencia.id_horario.hora_inicio.strftime("%H:%M")} - {asistencia.id_horario.hora_fin.strftime("%H:%M")}',
                'estatus': asistencia.estatus,
                'observaciones': asistencia.observaciones or '',
                'maestro': f'{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}',
            })

    porcentaje = (total_presentes / total_registros * 100) if total_registros else 0
    context = {
        'perfil': perfil,
        'asistencias_rows': rows,
        'hay_asistencias': bool(rows),
        'filtro_asignacion_id': asignacion_id,
        'filtro_unidad': unidad_str,
        'filtro_fecha_inicio': fecha_inicio_str,
        'filtro_fecha_fin': fecha_fin_str,
        'reporte_solicitado': reporte_solicitado,
        'asignaciones_disponibles': asignaciones if alumno else [],
        'resumen_asistencias': {
            'total': total_registros,
            'presentes': total_presentes,
            'ausentes': total_ausentes,
            'tarde': total_tarde,
            'justificado': total_justificado,
            'porcentaje': f'{porcentaje:.1f}',
        },
    }
    return render(request, 'alumno/asistencias.html', context)


def dashboard_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    perfil = _perfil_maestro(request)
    return render(request, 'maestro/maestro.html', {'perfil': perfil})


def registrar_asistencia(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    return render(request, 'maestro/RegistrarAsistencia.html', _contexto_maestro(request))


def registrar_calificaciones(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    return render(request, 'maestro/RegistrarCalificaciones.html', _contexto_maestro(request))


def _obtener_datos_calificaciones_maestro(maestro: Maestros, asignacion_id: str, unidad: int = None) -> dict:
    asignacion = get_object_or_404(
        AsignacionMateria.objects.select_related(
            'id_materia',
            'id_grupo__id_carrera',
            'id_grupo__id_ciclo_escolar',
            'id_maestro__id_usuario',
        ),
        pk=asignacion_id,
        id_maestro=maestro,
        estatus='Activa',
    )

    inscripciones = (
        Inscripcion.objects.select_related('id_alumno__id_usuario')
        .filter(id_grupo=asignacion.id_grupo, estatus='Activa')
        .order_by('id_alumno__id_usuario__apellido', 'id_alumno__id_usuario__nombre')
    )

    # Obtener TODAS las calificaciones de TODAS las unidades para cada alumno
    calificaciones_por_alumno = {}
    for calificacion in Calificacion.objects.filter(
        id_asignacion_materia=asignacion,
    ).select_related('id_inscripcion__id_alumno__id_usuario'):
        id_inscripcion = calificacion.id_inscripcion_id
        if id_inscripcion not in calificaciones_por_alumno:
            calificaciones_por_alumno[id_inscripcion] = []
        
        calificaciones_por_alumno[id_inscripcion].append({
            'unidad': calificacion.unidad,
            'calificacion': str(calificacion.calificacion),
            'observaciones': calificacion.observaciones or '',
        })

    alumnos = []
    for inscripcion in inscripciones:
        usuario = inscripcion.id_alumno.id_usuario
        todas_calificaciones = calificaciones_por_alumno.get(inscripcion.id_inscripcion, [])
        
        alumnos.append({
            'id_inscripcion': inscripcion.id_inscripcion,
            'matricula': usuario.matricula,
            'nombre_completo': f'{usuario.nombre} {usuario.apellido}',
            'estatus_alumno': inscripcion.estatus,
            'todas_calificaciones': todas_calificaciones,
        })

    return {
        'asignacion': {
            'id_asignacion_materia': asignacion.id_asignacion_materia,
            'materia': {
                'id': asignacion.id_materia.id_materia,
                'codigo': asignacion.id_materia.clave,
                'nombre': asignacion.id_materia.nombre,
            },
            'grupo': {
                'id': asignacion.id_grupo.id_grupo,
                'clave': asignacion.id_grupo.clave,
                'nombre': asignacion.id_grupo.nombre,
                'carrera': str(asignacion.id_grupo.id_carrera) if asignacion.id_grupo.id_carrera else '',
                'ciclo': str(asignacion.id_grupo.id_ciclo_escolar) if asignacion.id_grupo.id_ciclo_escolar else '',
            },
        },
        'unidad': unidad or 1,
        'alumnos': alumnos,
    }


def get_datos_calificaciones_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    maestro = Maestros.objects.filter(pk=request.session.get('maestro_id')).first()
    if not maestro:
        return JsonResponse({'success': False, 'error': 'No se encontró el maestro en sesión'}, status=400)

    asignacion_id = request.GET.get('asignacion_id', '').strip()

    if not asignacion_id:
        return JsonResponse({'success': True, 'data': None})

    try:
        # Obtener TODAS las calificaciones de TODAS las unidades
        data = _obtener_datos_calificaciones_maestro(maestro, asignacion_id)
        return JsonResponse({'success': True, 'data': data})
    except Exception as exc:
        return JsonResponse({'success': False, 'error': _safe_server_error(exc)}, status=400)


@require_http_methods(['POST'])
def guardar_calificaciones_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    maestro = Maestros.objects.filter(pk=request.session.get('maestro_id')).first()
    if not maestro:
        return JsonResponse({'success': False, 'error': 'No se encontró el maestro en sesión'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    asignacion_id = str(payload.get('asignacion_id', '')).strip()
    registros = payload.get('registros', [])

    if not asignacion_id:
        return JsonResponse({'success': False, 'error': 'Selecciona una asignación'}, status=400)

    asignacion = get_object_or_404(
        AsignacionMateria.objects.select_related('id_grupo'),
        pk=asignacion_id,
        id_maestro=maestro,
        estatus='Activa',
    )

    inscripciones_validas_qs = (
        Inscripcion.objects.select_related('id_alumno__id_usuario')
        .filter(id_grupo=asignacion.id_grupo, estatus='Activa')
    )
    inscripciones_validas = {
        inscripcion.id_inscripcion: inscripcion
        for inscripcion in inscripciones_validas_qs
    }

    guardadas = 0
    with transaction.atomic():
        for registro in registros:
            id_inscripcion = registro.get('id_inscripcion')
            if id_inscripcion is None:
                continue
            try:
                id_inscripcion = int(id_inscripcion)
            except (TypeError, ValueError):
                return JsonResponse({'success': False, 'error': 'Una inscripción enviada no es válida'}, status=400)

            if id_inscripcion not in inscripciones_validas:
                continue

            # Extraer unidad de cada registro
            unidad_str = str(registro.get('unidad', '1')).strip() or '1'
            try:
                unidad = int(unidad_str)
            except ValueError:
                return JsonResponse({'success': False, 'error': 'La unidad debe ser un número entero'}, status=400)
            
            # Validar que unidad esté entre 1 y 6
            if unidad < 1 or unidad > 6:
                return JsonResponse({'success': False, 'error': 'La unidad debe estar entre 1 y 6'}, status=400)

            valor = str(registro.get('calificacion', '')).strip()
            observaciones = str(registro.get('observaciones', '')).strip() or None

            if not valor:
                continue

            try:
                calificacion_valor = Decimal(valor)
            except (InvalidOperation, ValueError):
                alumno = inscripciones_validas[id_inscripcion].id_alumno.id_usuario
                return JsonResponse({
                    'success': False,
                    'error': f'La calificación de {alumno.nombre} {alumno.apellido} debe ser un número válido'
                }, status=400)

            if calificacion_valor < 0 or calificacion_valor > ESCALA_MAXIMA_CALIFICACION:
                alumno = inscripciones_validas[id_inscripcion].id_alumno.id_usuario
                return JsonResponse({
                    'success': False,
                    'error': f'La calificación de {alumno.nombre} {alumno.apellido} debe estar entre 0 y {ESCALA_MAXIMA_CALIFICACION}'
                }, status=400)

            # Obtener calificación existente si existe
            calificacion_existente = Calificacion.objects.filter(
                id_inscripcion_id=id_inscripcion,
                id_asignacion_materia=asignacion,
                unidad=unidad
            ).first()

            valor_anterior = calificacion_existente.calificacion if calificacion_existente else None
            
            # Solo guardar si el valor cambió o es una nueva calificación
            if valor_anterior is None or valor_anterior != calificacion_valor:
                accion = 'actualizar' if calificacion_existente else 'crear'

                calificacion, created = Calificacion.objects.update_or_create(
                    id_inscripcion_id=id_inscripcion,
                    id_asignacion_materia=asignacion,
                    unidad=unidad,
                    defaults={
                        'calificacion': calificacion_valor,
                        'observaciones': observaciones,
                    }
                )

                # Registrar en log de auditoría solo si hubo cambio
                inscripcion = inscripciones_validas[id_inscripcion]
                LogCalificacion.objects.create(
                    id_calificacion=calificacion,
                    id_usuario_modifico=maestro.id_usuario,
                    id_alumno=inscripcion.id_alumno,
                    id_materia=asignacion.id_materia,
                    accion=accion,
                    valor_anterior=valor_anterior,
                    valor_nuevo=calificacion_valor,
                    unidad=unidad,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    observaciones=f'Materia: {asignacion.id_materia.nombre}, Unidad: {unidad}'
                )

                guardadas += 1

    return JsonResponse({'success': True, 'guardadas': guardadas})


def consultar_reportes(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    contexto = _contexto_maestro(request)
    maestro = contexto.get('maestro')
    asignaciones_docente = contexto.get('asignaciones_docente', [])

    reporte_tipo = request.GET.get('tipo', 'asistencias').strip().lower()
    asignacion_id = request.GET.get('asignacion_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()

    reportes = []
    if reporte_tipo not in ('asistencias', 'calificaciones'):
        reporte_tipo = 'asistencias'

    filtros_aplicados = any([asignacion_id, unidad_str])
    
    unidad = None
    if unidad_str:
        try:
            unidad = int(unidad_str)
        except ValueError:
            unidad = None

    if reporte_tipo == 'asistencias':
        # Base query
        asistencias_qs = Asistencia.objects.select_related(
            'id_inscripcion__id_alumno__id_usuario',
            'id_horario__id_asignacion_materia__id_materia',
            'id_horario__id_asignacion_materia__id_grupo',
            'id_horario__id_asignacion_materia__id_maestro__id_usuario',
        ).filter(id_horario__id_asignacion_materia__id_maestro=maestro)

        # Aplicar filtros
        if asignacion_id:
            asistencias_qs = asistencias_qs.filter(id_horario__id_asignacion_materia__pk=asignacion_id)

        if unidad:
            asistencias_qs = asistencias_qs.filter(unidad=unidad)

        # Ordenar
        asistencias_qs = asistencias_qs.order_by('-fecha_asistencia', '-unidad')

        # Iterar resultados
        for asistencia in asistencias_qs[:500]:
            usuario_alumno = asistencia.id_inscripcion.id_alumno.id_usuario
            asignacion = asistencia.id_horario.id_asignacion_materia
            reportes.append({
                'tipo': 'asistencia',
                'fecha': asistencia.fecha_asistencia.strftime('%d/%m/%Y'),
                'unidad': asistencia.unidad,
                'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
                'matricula': usuario_alumno.matricula,
                'materia': f'{asignacion.id_materia.clave}',
                'grupo': asignacion.id_grupo.clave,
                'detalle': f'{asistencia.id_horario.dia_semana} {asistencia.id_horario.hora_inicio.strftime("%H:%M")}',
                'estado': asistencia.estatus,
                'observaciones': asistencia.observaciones or '',
            })
    else:
        # Base query
        calificaciones_qs = Calificacion.objects.select_related(
            'id_inscripcion__id_alumno__id_usuario',
            'id_asignacion_materia__id_materia',
            'id_asignacion_materia__id_grupo',
            'id_asignacion_materia__id_maestro__id_usuario',
        ).filter(id_asignacion_materia__id_maestro=maestro)

        # Aplicar filtros
        if asignacion_id:
            calificaciones_qs = calificaciones_qs.filter(id_asignacion_materia__pk=asignacion_id)

        if unidad:
            calificaciones_qs = calificaciones_qs.filter(unidad=unidad)

        # Ordenar
        calificaciones_qs = calificaciones_qs.order_by('-fecha_registro', '-unidad')

        # Iterar resultados
        for calificacion in calificaciones_qs[:500]:
            usuario_alumno = calificacion.id_inscripcion.id_alumno.id_usuario
            asignacion = calificacion.id_asignacion_materia
            reportes.append({
                'tipo': 'calificacion',
                'fecha': calificacion.fecha_registro.strftime('%d/%m/%Y'),
                'unidad': calificacion.unidad,
                'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
                'matricula': usuario_alumno.matricula,
                'materia': f'{asignacion.id_materia.clave}',
                'grupo': asignacion.id_grupo.clave,
                'detalle': _formatear_calificacion_visible(calificacion.calificacion),
                'estado': 'Calificación',
                'observaciones': calificacion.observaciones or '',
            })

    context = {
        **contexto,
        'reportes': reportes,
        'filtros_aplicados': filtros_aplicados,
        'reporte_tipo': reporte_tipo,
        'filtro_asignacion_id': asignacion_id,
        'filtro_unidad': unidad_str,
    }
    return render(request, 'maestro/ConsultarReportes.html', context)


def get_materias_por_semestre(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return JsonResponse({'materias': []})

    materias = Materia.objects.filter(activo=True).order_by('nombre')
    data = [{'id': materia.id_materia, 'nombre': materia.nombre} for materia in materias]
    return JsonResponse({'materias': data})


def _obtener_datos_asistencia_maestro(maestro: Maestros, asignacion_id: str, fecha_obj: date, unidad: int, horario_id: str | None = None) -> dict:
    asignacion = get_object_or_404(
        AsignacionMateria.objects.select_related(
            'id_materia',
            'id_grupo__id_carrera',
            'id_grupo__id_ciclo_escolar',
            'id_maestro__id_usuario',
        ),
        pk=asignacion_id,
        id_maestro=maestro,
        estatus='Activa',
    )

    inscripciones = (
        Inscripcion.objects.select_related('id_alumno__id_usuario')
        .filter(id_grupo=asignacion.id_grupo, estatus='Activa')
        .order_by('id_alumno__id_usuario__apellido', 'id_alumno__id_usuario__nombre')
    )

    horarios_qs = (
        Horario.objects.select_related('id_asignacion_materia__id_materia')
        .filter(id_asignacion_materia=asignacion, estatus='Activo')
        .order_by('dia_semana', 'hora_inicio')
    )

    dia_seleccionado = _dia_semana_es(fecha_obj)
    horarios_serializados = []
    horario_sugerido_id = None

    for horario in horarios_qs:
        serializado = _serializar_horario(horario)
        serializado['es_del_dia'] = horario.dia_semana == dia_seleccionado
        horarios_serializados.append(serializado)

    if horario_id:
        horario_sugerido_id = int(horario_id)
    else:
        for horario in horarios_serializados:
            if horario['es_del_dia']:
                horario_sugerido_id = horario['id_horario']
                break

    asistencias_existentes = {}
    if horario_sugerido_id:
        for asistencia in Asistencia.objects.filter(
            id_horario_id=horario_sugerido_id,
            fecha_asistencia=fecha_obj,
            unidad=unidad,
        ):
            asistencias_existentes[asistencia.id_inscripcion_id] = {
                'estatus': asistencia.estatus,
                'observaciones': asistencia.observaciones or '',
            }

    alumnos = []
    for inscripcion in inscripciones:
        usuario = inscripcion.id_alumno.id_usuario
        alumnos.append({
            'id_inscripcion': inscripcion.id_inscripcion,
            'matricula': usuario.matricula,
            'nombre_completo': f'{usuario.nombre} {usuario.apellido}',
            'estatus_alumno': inscripcion.estatus,
            'asistencia': asistencias_existentes.get(inscripcion.id_inscripcion, {'estatus': 'Presente', 'observaciones': ''}),
        })

    return {
        'asignacion': {
            'id_asignacion_materia': asignacion.id_asignacion_materia,
            'materia': {
                'id': asignacion.id_materia.id_materia,
                'codigo': asignacion.id_materia.clave,
                'nombre': asignacion.id_materia.nombre,
            },
            'grupo': {
                'id': asignacion.id_grupo.id_grupo,
                'clave': asignacion.id_grupo.clave,
                'nombre': asignacion.id_grupo.nombre,
                'carrera': str(asignacion.id_grupo.id_carrera) if asignacion.id_grupo.id_carrera else '',
                'ciclo': str(asignacion.id_grupo.id_ciclo_escolar) if asignacion.id_grupo.id_ciclo_escolar else '',
            },
        },
        'dia_semana': dia_seleccionado,
        'unidad': unidad,
        'horario_sugerido_id': horario_sugerido_id,
        'horarios': horarios_serializados,
        'alumnos': alumnos,
    }


def get_datos_asistencia_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    maestro = Maestros.objects.filter(pk=request.session.get('maestro_id')).first()
    if not maestro:
        return JsonResponse({'success': False, 'error': 'No se encontró el maestro en sesión'}, status=400)

    asignacion_id = request.GET.get('asignacion_id', '').strip()
    fecha_str = request.GET.get('fecha', '').strip()
    unidad_str = request.GET.get('unidad', '1').strip() or '1'
    horario_id = request.GET.get('horario_id', '').strip() or None

    if not asignacion_id or not fecha_str:
        return JsonResponse({'success': True, 'data': None})

    try:
        fecha_obj = _parse_fecha_iso(fecha_str)
        if fecha_obj is None:
            raise ValueError('Fecha inválida')
        unidad = int(unidad_str)
        data = _obtener_datos_asistencia_maestro(maestro, asignacion_id, fecha_obj, unidad, horario_id)
        return JsonResponse({'success': True, 'data': data})
    except Exception as exc:
        return JsonResponse({'success': False, 'error': _safe_server_error(exc)}, status=400)


@require_http_methods(['POST'])
def guardar_asistencia_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    maestro = Maestros.objects.filter(pk=request.session.get('maestro_id')).first()
    if not maestro:
        return JsonResponse({'success': False, 'error': 'No se encontró el maestro en sesión'}, status=400)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    asignacion_id = str(payload.get('asignacion_id', '')).strip()
    fecha_str = str(payload.get('fecha', '')).strip()
    unidad_str = str(payload.get('unidad', '1')).strip() or '1'
    horario_id = str(payload.get('horario_id', '')).strip()
    registros = payload.get('registros', [])

    if not asignacion_id or not fecha_str or not horario_id:
        return JsonResponse({'success': False, 'error': 'Completa asignación, fecha y horario'}, status=400)

    try:
        fecha_obj = _parse_fecha_iso(fecha_str)
        if fecha_obj is None:
            raise ValueError('Fecha inválida')
    except Exception:
        return JsonResponse({'success': False, 'error': 'Fecha inválida'}, status=400)

    try:
        unidad = int(unidad_str)
    except ValueError:
        return JsonResponse({'success': False, 'error': 'La unidad debe ser un número entero'}, status=400)

    asignacion = get_object_or_404(
        AsignacionMateria.objects.select_related('id_grupo'),
        pk=asignacion_id,
        id_maestro=maestro,
        estatus='Activa',
    )

    horario = get_object_or_404(
        Horario.objects.select_related('id_asignacion_materia'),
        pk=horario_id,
        id_asignacion_materia=asignacion,
    )

    inscripciones_validas = set(
        Inscripcion.objects.filter(id_grupo=asignacion.id_grupo, estatus='Activa').values_list('id_inscripcion', flat=True)
    )

    guardadas = 0
    with transaction.atomic():
        for registro in registros:
            id_inscripcion = registro.get('id_inscripcion')
            if id_inscripcion is None:
                continue
            id_inscripcion = int(id_inscripcion)
            if id_inscripcion not in inscripciones_validas:
                continue

            estatus = str(registro.get('estatus', 'Presente')).strip() or 'Presente'
            observaciones = str(registro.get('observaciones', '')).strip() or None

            Asistencia.objects.update_or_create(
                id_inscripcion_id=id_inscripcion,
                id_horario=horario,
                fecha_asistencia=fecha_obj,
                unidad=unidad,
                defaults={
                    'estatus': estatus,
                    'observaciones': observaciones,
                }
            )
            guardadas += 1

    return JsonResponse({'success': True, 'guardadas': guardadas})


def dashboard_administrativo(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    # Obtener el periodo actual
    periodo_actual = _periodo_actual()
    
    # Obtener el ciclo escolar actual basado en el periodo actual
    from .models import CicloEscolar
    ciclo_actual = CicloEscolar.objects.filter(
        nombre_ciclo=periodo_actual
    ).first()
    
    fecha_fin_curso = ciclo_actual.fecha_fin if ciclo_actual else None
    
    context = {
        'perfil': _perfil_administrativo(request),
        'avisos': {
            'fin_semestre': fecha_fin_curso.strftime('%d/%m/%Y') if fecha_fin_curso else '---'
        },
        'config': {
            'periodo': periodo_actual
        }
    }
    
    return render(request, 'administrativo/administrativo.html', context)


def admin_reportes(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    context = {
        'perfil': _perfil_administrativo(request),
        'materias': Materia.objects.all().order_by('nombre'),
        'reportes_admin': [],
    }
    return render(request, 'administrativo/ConsultaReportes.html', context)


def _materias_catalogo_admin():
    materias = []
    for materia in Materia.objects.all().order_by('nombre'):
        materias.append({
            'id_materia': materia.id_materia,
            'codigo': materia.clave,
            'nombre': materia.nombre,
            'creditos': materia.creditos,
            'semestre': materia.semestre,
            'get_semestre_display': f'Semestre {materia.semestre}',
            'activa': materia.activo,
        })
    return materias


def admin_horarios(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    # Obtener filtros de ciclo escolar y grupo
    ciclo_filtro = request.GET.get('ciclo', '')
    grupo_filtro = request.GET.get('grupo', '')
    logger.info(f"Ciclo filtro: '{ciclo_filtro}', Grupo filtro: '{grupo_filtro}'")

    # Cargar datos reales de la base de datos
    materias = Materia.objects.filter(activo=True).order_by('nombre')
    maestros = Maestros.objects.select_related('id_usuario').all()
    ciclos_escolares = CicloEscolar.objects.all().order_by('-fecha_inicio')
    
    # Filtrar grupos solo si hay un ciclo escolar seleccionado explícitamente
    ciclo_actual = None
    if ciclo_filtro:
        ciclo_actual = CicloEscolar.objects.filter(nombre_ciclo=ciclo_filtro).first()
        grupos = Grupo.objects.filter(id_ciclo_escolar=ciclo_actual).order_by('clave') if ciclo_actual else Grupo.objects.none()
    else:
        grupos = Grupo.objects.none()
    
    # Cargar aulas únicas de la tabla Horario
    aulas_existentes = Horario.objects.exclude(aula__isnull=True).exclude(aula='').values_list('aula', flat=True).distinct().order_by('aula')
    aulas = list(aulas_existentes) if aulas_existentes else ['A101', 'A102', 'A103', 'B201', 'B202', 'LAB1', 'LAB2']
    
    # Cargar horarios con filtro por grupo
    horarios_query = Horario.objects.all()
    
    logger.info(f"=== DEPURACIÓN ===")
    logger.info(f"Ciclo filtro: '{ciclo_filtro}', Grupo filtro: '{grupo_filtro}'")
    logger.info(f"Horarios totales en BD: {Horario.objects.count()}")
    
    # Solo mostrar horarios si hay ciclo Y grupo seleccionados
    if ciclo_filtro and grupo_filtro:
        # Asegurar que el grupo existe (buscar sin importar mayúsculas/minúsculas)
        grupo_obj = Grupo.objects.filter(clave__iexact=grupo_filtro).first()
        logger.info(f"Buscando grupo: '{grupo_filtro}' - Encontrado: {grupo_obj}")
        if grupo_obj:
            logger.info(f"ID del grupo encontrado: {grupo_obj.id_grupo}")
            # Usar select_related para optimizar la consulta
            horarios_query = horarios_query.select_related(
                'id_asignacion_materia__id_grupo',
                'id_asignacion_materia__id_materia',
                'id_asignacion_materia__id_maestro__id_usuario'
            ).filter(
                id_asignacion_materia__id_grupo=grupo_obj
            )
            logger.info(f"Horarios encontrados para grupo {grupo_filtro}: {horarios_query.count()}")
            
            # Mostrar detalles de los horarios encontrados
            for h in horarios_query:
                logger.info(f"Horario encontrado: {h.dia_semana} {h.hora_inicio}-{h.hora_fin} - Grupo: {h.id_asignacion_materia.id_grupo.clave}")
        else:
            logger.warning(f"Grupo {grupo_filtro} no encontrado en BD")
            # Mostrar todos los grupos disponibles para debug
            todos_grupos = Grupo.objects.all()
            logger.info(f"Grupos disponibles en BD: {[g.clave for g in todos_grupos]}")
            horarios_query = Horario.objects.none()
    else:
        # Si no hay grupo seleccionado, no mostrar horarios
        horarios_query = Horario.objects.none()
        logger.info("Sin grupo seleccionado, mostrando horarios vacíos")
    
    horarios = horarios_query.order_by('dia_semana', 'hora_inicio')
    
    # Contador real de horarios (con filtro)
    total_horarios = horarios_query.count()
    logger.info(f"Total horarios a mostrar: {total_horarios}")

    # Calcular puntos de tiempo únicos de todos los horarios.
    # Usamos segmentos por unidad para forzar filas de 1hora (13:30-14:30, 14:30-15:30, ...)
    puntos_tiempo = set()
    # Precalcular segmentos por horario para reutilizar luego
    segmentos_por_horario = {}
    for horario in horarios:
        segmentos = _split_horario_en_unidades(horario)
        segmentos_por_horario[horario.id_horario] = segmentos
        for seg in segmentos:
            puntos_tiempo.add(seg['hora_inicio'])
            puntos_tiempo.add(seg['hora_fin'])
    
    # Ordenar puntos de tiempo cronológicamente
    puntos_tiempo_ordenados = sorted(list(puntos_tiempo))
    logger.info(f"Puntos de tiempo únicos: {puntos_tiempo_ordenados}")
    
    # Crear intervalos entre puntos consecutivos
    intervalos_hora = []
    for i in range(len(puntos_tiempo_ordenados) - 1):
        inicio = puntos_tiempo_ordenados[i]
        fin = puntos_tiempo_ordenados[i + 1]
        intervalos_hora.append(f"{inicio}-{fin}")
    
    logger.info(f"Intervalos dinámicos: {intervalos_hora}")
    
    # Mapeo inverso de días del modelo al formulario
    dia_map_inverse = {
        'Lunes': 'lunes',
        'Martes': 'martes',
        'Miercoles': 'miercoles',
        'Jueves': 'jueves',
        'Viernes': 'viernes',
        'Sabado': 'sabado',
        'Domingo': 'domingo'
    }
    
    # Crear una lista de diccionarios para cada intervalo con sus horarios por día
    horarios_tabla = []
    for intervalo in intervalos_hora:
        hora_data = {
            'hora': intervalo,
            'lunes': [],
            'martes': [],
            'miercoles': [],
            'jueves': [],
            'viernes': []
        }
        horarios_tabla.append(hora_data)
    
    # Distribuir cada segmento en la fila correspondiente (una fila por segmento)
    for horario in horarios:
        dia_key = dia_map_inverse.get(horario.dia_semana, horario.dia_semana.lower())
        segmentos = segmentos_por_horario.get(horario.id_horario, [])
        asignacion = horario.id_asignacion_materia
        for seg in segmentos:
            intervalo_str = f"{seg['hora_inicio']}-{seg['hora_fin']}"
            # Buscar la fila que corresponde exactamente a este intervalo
            for fila in horarios_tabla:
                if fila['hora'] == intervalo_str:
                    # Añadir un objeto de segmento con la información necesaria para renderizar
                    fila[dia_key].append({
                        'id_horario': horario.id_horario,
                        'unidad': seg['unidad'],
                        'materia': asignacion.id_materia.nombre,
                        'docente': f"{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}",
                        'grupo': asignacion.id_grupo.clave,
                        'aula': horario.aula,
                        'hora_inicio': seg['hora_inicio'],
                        'hora_fin': seg['hora_fin'],
                    })
                    break

    horarios_data = []
    horarios_por_dia = {
        'lunes': [],
        'martes': [],
        'miercoles': [],
        'jueves': [],
        'viernes': []
    }
    
    logger.info(f"Procesando {len(horarios)} horarios filtrados")
    
    for horario in horarios:
        asignacion = horario.id_asignacion_materia
        horario_info = {
            'id_horario': horario.id_horario,
            'dia': dia_map_inverse.get(horario.dia_semana, horario.dia_semana.lower()),
            'materia': asignacion.id_materia.nombre,
            'docente': f"{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}",
            'grupo': asignacion.id_grupo.clave,
            'aula': horario.aula,
            'hora_inicio': str(horario.hora_inicio),
            'hora_fin': str(horario.hora_fin),
            'segmentos': [_ for _ in _split_horario_en_unidades(horario)],
        }
        horarios_data.append(horario_info)
        logger.info(f"Horario procesado: {horario_info}")
        
        # Agregar al diccionario por día
        dia_key = dia_map_inverse.get(horario.dia_semana, horario.dia_semana.lower())
        if dia_key in horarios_por_dia:
            horarios_por_dia[dia_key].append(horario_info)
    
    logger.info(f"Horarios por día: {horarios_por_dia}")
    logger.info(f"=== FINAL ===")
    logger.info(f"Grupo filtro final: '{grupo_filtro}'")
    logger.info(f"Total horarios a mostrar: {total_horarios}")
    logger.info(f"Horarios por día - Lunes: {len(horarios_por_dia['lunes'])}")
    logger.info(f"Horarios por día - Martes: {len(horarios_por_dia['martes'])}")
    logger.info(f"Horarios por día - Miércoles: {len(horarios_por_dia['miercoles'])}")
    logger.info(f"Horarios por día - Jueves: {len(horarios_por_dia['jueves'])}")
    logger.info(f"Horarios por día - Viernes: {len(horarios_por_dia['viernes'])}")

    context = {
        'perfil': _perfil_administrativo(request),
        'materias': materias,
        'maestros': maestros,
        'grupos': grupos,
        'ciclos_escolares': ciclos_escolares,
        'aulas': aulas,
        'total_horarios': total_horarios,
        'horarios': json.dumps(horarios_data),
        'horarios_por_dia': horarios_por_dia,
        'horarios_tabla': horarios_tabla,
        'grupo_filtro': grupo_filtro,
        'ciclo_filtro': ciclo_filtro,
        'horas_del_dia': intervalos_hora,
    }
    logger.info(f"Contexto enviado al template: total_horarios={total_horarios}, grupo_filtro='{grupo_filtro}'")
    logger.info(f"Horarios por día en contexto: {[(k, len(v)) for k, v in horarios_por_dia.items()]}")
    logger.info(f"Horarios tabla: {len(horarios_tabla)} filas")
    logger.info(f"Horarios data: {horarios_data[:3] if horarios_data else 'Sin horarios'}")
    return render(request, 'administrativo/GestionHorarios.html', context)


def admin_materias(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    context = {
        'perfil': _perfil_administrativo(request),
        'materias': _materias_catalogo_admin(),
    }
    return render(request, 'administrativo/AdministrarMaterias.html', context)


def exportar_materias_pdf(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    materias = Materia.objects.all().order_by('nombre')
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 10, 'SchoolTrack - Catalogo de Materias', ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(30, 8, 'Codigo', 1, 0, 'C')
    pdf.cell(80, 8, 'Nombre', 1, 0, 'C')
    pdf.cell(25, 8, 'Semestre', 1, 0, 'C')
    pdf.cell(25, 8, 'Creditos', 1, 0, 'C')
    pdf.cell(30, 8, 'Estado', 1, 1, 'C')
    
    pdf.set_font('Helvetica', '', 9)
    for materia in materias:
        nombre = materia.nombre[:35] if materia.nombre else ''
        estado = 'Activa' if materia.activo else 'Inactiva'
        
        pdf.cell(30, 7, str(materia.clave)[:10], 1, 0, 'L')
        pdf.cell(80, 7, nombre, 1, 0, 'L')
        pdf.cell(25, 7, str(materia.semestre), 1, 0, 'C')
        pdf.cell(25, 7, str(materia.creditos), 1, 0, 'C')
        pdf.cell(30, 7, estado, 1, 1, 'C')
    
    pdf_content = pdf.output(dest='S').encode('latin-1')
    
    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="materias.pdf"'
    return response


def exportar_horario_pdf(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    ciclo_filtro = request.GET.get('ciclo', '')
    grupo_filtro = request.GET.get('grupo', '')

    if not ciclo_filtro or not grupo_filtro:
        return JsonResponse({'success': False, 'error': 'Se requiere ciclo y grupo'}, status=400)

    # Obtener grupo
    grupo_obj = Grupo.objects.filter(clave__iexact=grupo_filtro).first()
    if not grupo_obj:
        return JsonResponse({'success': False, 'error': 'Grupo no encontrado'}, status=404)

    # Obtener horarios del grupo (misma lógica que admin_horarios)
    horarios_query = Horario.objects.select_related(
        'id_asignacion_materia__id_grupo',
        'id_asignacion_materia__id_materia',
        'id_asignacion_materia__id_maestro__id_usuario'
    ).filter(
        id_asignacion_materia__id_grupo=grupo_obj
    ).order_by('dia_semana', 'hora_inicio')
    
    horarios = horarios_query

    # Calcular puntos de tiempo únicos (misma lógica que admin_horarios)
    puntos_tiempo = set()
    segmentos_por_horario = {}
    for horario in horarios:
        segmentos = _split_horario_en_unidades(horario)
        segmentos_por_horario[horario.id_horario] = segmentos
        for seg in segmentos:
            puntos_tiempo.add(seg['hora_inicio'])
            puntos_tiempo.add(seg['hora_fin'])
    
    puntos_tiempo_ordenados = sorted(list(puntos_tiempo))
    
    # Crear intervalos entre puntos consecutivos
    intervalos_hora = []
    for i in range(len(puntos_tiempo_ordenados) - 1):
        inicio = puntos_tiempo_ordenados[i]
        fin = puntos_tiempo_ordenados[i + 1]
        intervalos_hora.append(f"{inicio}-{fin}")
    
    # Mapeo inverso de días (misma lógica que admin_horarios)
    dia_map_inverse = {
        'Lunes': 'lunes',
        'Martes': 'martes',
        'Miercoles': 'miercoles',
        'Jueves': 'jueves',
        'Viernes': 'viernes',
        'Sabado': 'sabado',
        'Domingo': 'domingo'
    }
    
    # Crear horarios_tabla (misma lógica que admin_horarios)
    horarios_tabla = []
    for intervalo in intervalos_hora:
        hora_data = {
            'hora': intervalo,
            'lunes': [],
            'martes': [],
            'miercoles': [],
            'jueves': [],
            'viernes': []
        }
        horarios_tabla.append(hora_data)
    
    # Distribuir cada segmento en la fila correspondiente
    for horario in horarios:
        dia_key = dia_map_inverse.get(horario.dia_semana, horario.dia_semana.lower())
        segmentos = segmentos_por_horario.get(horario.id_horario, [])
        asignacion = horario.id_asignacion_materia
        for seg in segmentos:
            intervalo_str = f"{seg['hora_inicio']}-{seg['hora_fin']}"
            for fila in horarios_tabla:
                if fila['hora'] == intervalo_str:
                    fila[dia_key].append({
                        'id_horario': horario.id_horario,
                        'unidad': seg['unidad'],
                        'materia': asignacion.id_materia.nombre,
                        'docente': f"{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}",
                        'grupo': asignacion.id_grupo.clave,
                        'aula': horario.aula,
                        'hora_inicio': seg['hora_inicio'],
                        'hora_fin': seg['hora_fin'],
                    })
                    break

    # Crear PDF en orientación horizontal
    pdf = FPDF(orientation='L')
    pdf.add_page()
    
    # Título
    pdf.set_font('Helvetica', 'B', 18)
    pdf.cell(0, 12, f'Horario de Clases', ln=True, align='C')
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 8, f'{grupo_obj.clave} - {grupo_obj.nombre}', ln=True, align='C')
    pdf.set_font('Helvetica', '', 11)
    pdf.cell(0, 6, f'Ciclo Escolar: {ciclo_filtro}', ln=True, align='C')
    pdf.ln(8)

    # Encabezados de tabla con fondo gris
    pdf.set_fill_color(200, 200, 200)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(35, 10, 'Hora', 1, 0, 'C', True)
    pdf.cell(50, 10, 'Lunes', 1, 0, 'C', True)
    pdf.cell(50, 10, 'Martes', 1, 0, 'C', True)
    pdf.cell(50, 10, 'Miércoles', 1, 0, 'C', True)
    pdf.cell(50, 10, 'Jueves', 1, 0, 'C', True)
    pdf.cell(50, 10, 'Viernes', 1, 1, 'C', True)

    # Crear filas usando horarios_tabla (misma estructura que la interfaz)
    pdf.set_font('Helvetica', '', 8)
    for fila in horarios_tabla:
        pdf.set_fill_color(245, 245, 245)  # Fondo gris claro para filas alternas
        pdf.cell(35, 35, fila['hora'], 1, 0, 'C', True)

        for dia in ['lunes', 'martes', 'miercoles', 'jueves', 'viernes']:
            segmentos = fila[dia]
            texto = ''
            if segmentos:
                # Si hay múltiples segmentos en la misma celda, concatenarlos
                textos_celda = []
                for seg in segmentos:
                    textos_celda.append(f"{seg['materia'][:25]}")
                    textos_celda.append(f"{seg['docente'][:22]}")
                    textos_celda.append(f"Aula: {seg['aula']}")
                texto = '\n'.join(textos_celda[:6])  # Máximo 6 líneas
            
            # Usar multi_cell para texto con saltos de línea
            x = pdf.get_x()
            y = pdf.get_y()
            pdf.multi_cell(50, 35, texto, 1, 'C', True)
            pdf.set_xy(x + 50, y)

        pdf.ln()

    pdf_content = pdf.output(dest='S').encode('latin-1')

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="horario_{grupo_obj.clave}_{ciclo_filtro}.pdf"'
    return response


def crear_materia(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('admin_materias')

    codigo = request.POST.get('codigo', '').strip()
    nombre = request.POST.get('nombre', '').strip()
    semestre = request.POST.get('semestre', '1').strip()
    creditos = request.POST.get('creditos', '0').strip()
    activa = request.POST.get('activa') == 'on'

    if not codigo or not nombre:
        messages.error(request, 'El código y el nombre de la materia son obligatorios')
        return redirect('admin_materias')

    Materia.objects.update_or_create(
        clave=codigo,
        defaults={
            'nombre': nombre,
            'semestre': int(semestre) if semestre.isdigit() else 1,
            'creditos': int(creditos) if creditos.isdigit() else 0,
            'activo': activa,
        }
    )

    messages.success(request, 'Materia creada correctamente')
    return redirect('admin_materias')


def editar_materia(request, materia_id):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('admin_materias')

    materia = get_object_or_404(Materia, pk=materia_id)
    codigo = request.POST.get('codigo', '').strip()
    nombre = request.POST.get('nombre', '').strip()
    semestre = request.POST.get('semestre', '1').strip()
    creditos = request.POST.get('creditos', '0').strip()
    activa = request.POST.get('activa') == 'on'

    if not codigo or not nombre:
        messages.error(request, 'El código y el nombre de la materia son obligatorios')
        return redirect('admin_materias')

    materia.clave = codigo
    materia.nombre = nombre
    materia.semestre = int(semestre) if semestre.isdigit() else 1
    materia.creditos = int(creditos) if creditos.isdigit() else 0
    materia.activo = activa
    materia.save()

    messages.success(request, 'Materia actualizada correctamente')
    return redirect('admin_materias')


def eliminar_materia(request, materia_id):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('admin_materias')

    materia = get_object_or_404(Materia, pk=materia_id)
    materia.delete()
    messages.success(request, 'Materia eliminada correctamente')
    return redirect('admin_materias')


@transaction.atomic
def agregar_horario(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    # Extraer horarios: Soporta lista de objetos {"dia":..., "hora_inicio":..., "hora_fin":...}
    # o el formato anterior de lista de días con horas globales para retrocompatibilidad.
    horarios_input = payload.get('horarios', [])
    if not horarios_input:
        dias_legacy = payload.get('dias') or []
        h_ini_legacy = payload.get('hora_inicio', '')
        h_fin_legacy = payload.get('hora_fin', '')
        if dias_legacy:
            horarios_input = [
                {'dia': d, 'hora_inicio': h_ini_legacy, 'hora_fin': h_fin_legacy} 
                for d in dias_legacy
            ]

    materia_id = payload.get('materia', '')
    docente_matricula = payload.get('docente', '')
    grupo_clave = payload.get('grupo', '')
    aula = payload.get('aula', '')
    ciclo_escolar_nombre = payload.get('ciclo_escolar', '')

    if not all([materia_id, docente_matricula, grupo_clave, aula, ciclo_escolar_nombre]) or not horarios_input:
        return JsonResponse({'success': False, 'error': 'Faltan datos obligatorios para crear el horario'})

    try:
        # Validar y obtener objetos relacionados
        materia = Materia.objects.get(id_materia=int(materia_id))
        maestro_usuario = Usuarios.objects.get(matricula=docente_matricula)
        maestro = Maestros.objects.get(id_usuario=maestro_usuario)
        grupo = Grupo.objects.get(clave=grupo_clave)
        ciclo_escolar = CicloEscolar.objects.get(nombre_ciclo=ciclo_escolar_nombre)

        # Validar que el ciclo escolar esté activo (dentro de fechas válidas)
        hoy = timezone.now().date()
        if ciclo_escolar.fecha_inicio > hoy or ciclo_escolar.fecha_fin < hoy:
            return JsonResponse({'success': False, 'error': 'El ciclo escolar seleccionado no está activo'})

        # Mapeo de días del formulario al modelo
        dia_map = {
            'lunes': 'Lunes',
            'martes': 'Martes',
            'miercoles': 'Miercoles',
            'jueves': 'Jueves',
            'viernes': 'Viernes',
            'sabado': 'Sabado',
            'domingo': 'Domingo'
        }

        # Lista para almacenar los datos procesados y validados
        horarios_a_crear = []
        from datetime import datetime, timedelta

        # Validar cada entrada de horario individualmente antes de guardar nada
        for item in horarios_input:
            dia_txt = item.get('dia', '').lower()
            h_ini = item.get('hora_inicio', '')
            h_fin = item.get('hora_fin', '')

            if not all([dia_txt, h_ini, h_fin]):
                return JsonResponse({'success': False, 'error': 'Cada día seleccionado debe tener hora de inicio y fin'})

            # Validar lógica de tiempo
            if h_fin <= h_ini:
                return JsonResponse({'success': False, 'error': f'En {dia_txt}, la hora de fin debe ser posterior al inicio'})

            # Validar duración
            inicio_dt = datetime.strptime(h_ini, '%H:%M')
            fin_dt = datetime.strptime(h_fin, '%H:%M')
            duracion = fin_dt - inicio_dt
            if duracion < timedelta(minutes=30) or duracion > timedelta(hours=6):
                return JsonResponse({'success': False, 'error': f'Duración inválida el día {dia_txt} (min 30min, max 6h)'})

            dia_modelo = dia_map.get(dia_txt)
            if not dia_modelo:
                return JsonResponse({'success': False, 'error': f'Día "{dia_txt}" no es válido'})

            # Validar cruce con mismo maestro
            cruces_maestro = Horario.objects.filter(
                id_asignacion_materia__id_maestro=maestro,
                dia_semana=dia_modelo,
                estatus='Activo'
            ).filter(
                models.Q(hora_inicio__lt=h_fin) & models.Q(hora_fin__gt=h_ini)
            )
            if cruces_maestro.exists():
                return JsonResponse({'success': False, 'error': f'Conflicto: El maestro ya tiene clase el {dia_modelo} de {h_ini} a {h_fin}'})

            # Validar cruce con mismo grupo
            cruces_grupo = Horario.objects.filter(
                id_asignacion_materia__id_grupo=grupo,
                dia_semana=dia_modelo,
                estatus='Activo'
            ).filter(
                models.Q(hora_inicio__lt=h_fin) & models.Q(hora_fin__gt=h_ini)
            )
            if cruces_grupo.exists():
                return JsonResponse({'success': False, 'error': f'Conflicto: El grupo ya tiene clase el {dia_modelo} de {h_ini} a {h_fin}'})

            # Validar cruce con misma aula
            cruces_aula = Horario.objects.filter(
                aula=aula,
                dia_semana=dia_modelo,
                estatus='Activo'
            ).filter(
                models.Q(hora_inicio__lt=h_fin) & models.Q(hora_fin__gt=h_ini)
            )
            if cruces_aula.exists():
                return JsonResponse({'success': False, 'error': f'Conflicto: El aula {aula} está ocupada el {dia_modelo} de {h_ini} a {h_fin}'})

            horarios_a_crear.append({'dia': dia_modelo, 'inicio': h_ini, 'fin': h_fin})

        # Buscar o crear AsignacionMateria (DESPUÉS de todas las validaciones)
        asignacion, created = AsignacionMateria.objects.get_or_create(
            id_materia=materia,
            id_maestro=maestro,
            id_grupo=grupo,
            id_ciclo_escolar=ciclo_escolar,
            defaults={'estatus': 'Activa'}
        )

        # Crear horarios para cada día seleccionado
        horarios_creados = []
        # Mapeo inverso de días del modelo al formulario
        dia_map_inverse = {
            'Lunes': 'lunes',
            'Martes': 'martes',
            'Miercoles': 'miercoles',
            'Jueves': 'jueves',
            'Viernes': 'viernes',
            'Sabado': 'sabado',
            'Domingo': 'domingo'
        }
        
        for item_v in horarios_a_crear:
            horario = Horario.objects.create(
                id_asignacion_materia=asignacion,
                dia_semana=item_v['dia'],
                hora_inicio=item_v['inicio'],
                hora_fin=item_v['fin'],
                aula=aula,
                estatus='Activo'
            )
            horarios_creados.append({
                'id_horario': horario.id_horario,
                'dia': dia_map_inverse.get(item_v['dia'], item_v['dia'].lower()),
                'materia': materia.nombre,
                'docente': f"{maestro_usuario.nombre} {maestro_usuario.apellido}",
                'grupo': grupo.clave,
                'aula': aula,
                'hora_inicio': item_v['inicio'],
                'hora_fin': item_v['fin']
            })

        return JsonResponse({'success': True, 'horarios': horarios_creados})

    except Materia.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Materia no encontrada'})
    except Usuarios.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Docente no encontrado'})
    except Maestros.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Docente no registrado como maestro'})
    except Grupo.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Grupo no encontrado'})
    except CicloEscolar.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Ciclo escolar no encontrado'})
    except Exception as e:
        logger.error(f"Error al agregar horario: {str(e)}")
        return JsonResponse({'success': False, 'error': f'Error al crear horario: {str(e)}'})


@transaction.atomic
def eliminar_horario(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    horario_id = payload.get('horario_id')
    if not horario_id:
        return JsonResponse({'success': False, 'error': 'ID de horario requerido'})

    try:
        horario = Horario.objects.get(id_horario=int(horario_id))
        horario.delete()
        return JsonResponse({'success': True, 'message': 'Horario eliminado correctamente'})
    except Horario.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Horario no encontrado'})
    except Exception as e:
        logger.error(f"Error al eliminar horario: {str(e)}")
        return JsonResponse({'success': False, 'error': f'Error al eliminar horario: {str(e)}'})


@transaction.atomic
def editar_horario(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return JsonResponse({'success': False, 'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    horario_id = payload.get('horario_id')
    aula = payload.get('aula', '')
    hora_inicio = payload.get('hora_inicio', '')
    hora_fin = payload.get('hora_fin', '')

    if not horario_id:
        return JsonResponse({'success': False, 'error': 'ID de horario requerido'})

    if not hora_inicio or not hora_fin:
        return JsonResponse({'success': False, 'error': 'Hora de inicio y término son requeridas'})

    if hora_fin <= hora_inicio:
        return JsonResponse({'success': False, 'error': 'La hora de término debe ser mayor a la hora de inicio'})

    try:
        horario = Horario.objects.select_related(
            'id_asignacion_materia__id_maestro',
            'id_asignacion_materia__id_grupo'
        ).get(id_horario=int(horario_id))

        # Validar duración
        from datetime import datetime, timedelta
        inicio_dt = datetime.strptime(hora_inicio, '%H:%M')
        fin_dt = datetime.strptime(hora_fin, '%H:%M')
        duracion = fin_dt - inicio_dt
        if duracion < timedelta(minutes=30) or duracion > timedelta(hours=6):
            return JsonResponse({'success': False, 'error': 'Duración inválida (min 30min, max 6h)'})

        # Validar cruce con mismo maestro (excluyendo este horario)
        cruces_maestro = Horario.objects.filter(
            id_asignacion_materia__id_maestro=horario.id_asignacion_materia.id_maestro,
            dia_semana=horario.dia_semana,
            estatus='Activo'
        ).exclude(id_horario=horario.id_horario).filter(
            models.Q(hora_inicio__lt=hora_fin) & models.Q(hora_fin__gt=hora_inicio)
        )
        if cruces_maestro.exists():
            return JsonResponse({'success': False, 'error': f'Conflicto: El maestro ya tiene clase en ese horario'})

        # Validar cruce con mismo grupo (excluyendo este horario)
        cruces_grupo = Horario.objects.filter(
            id_asignacion_materia__id_grupo=horario.id_asignacion_materia.id_grupo,
            dia_semana=horario.dia_semana,
            estatus='Activo'
        ).exclude(id_horario=horario.id_horario).filter(
            models.Q(hora_inicio__lt=hora_fin) & models.Q(hora_fin__gt=hora_inicio)
        )
        if cruces_grupo.exists():
            return JsonResponse({'success': False, 'error': f'Conflicto: El grupo ya tiene clase en ese horario'})

        # Validar cruce con misma aula (excluyendo este horario)
        if aula:
            cruces_aula = Horario.objects.filter(
                aula=aula,
                dia_semana=horario.dia_semana,
                estatus='Activo'
            ).exclude(id_horario=horario.id_horario).filter(
                models.Q(hora_inicio__lt=hora_fin) & models.Q(hora_fin__gt=hora_inicio)
            )
            if cruces_aula.exists():
                return JsonResponse({'success': False, 'error': f'Conflicto: El aula {aula} está ocupada en ese horario'})

        # Actualizar horario
        horario.aula = aula
        horario.hora_inicio = hora_inicio
        horario.hora_fin = hora_fin
        horario.save()

        return JsonResponse({'success': True, 'message': 'Horario actualizado correctamente'})

    except Horario.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Horario no encontrado'})
    except Exception as e:
        logger.error(f"Error al editar horario: {str(e)}")
        return JsonResponse({'success': False, 'error': f'Error al editar horario: {str(e)}'})


def get_horarios_semanales(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return JsonResponse({'horarios': []})

    # Obtener filtro de grupo si existe
    grupo_filtro = request.GET.get('grupo', '')

    # Cargar horarios (no filtrar por estatus aquí para diagnóstico) con sus relaciones
    horarios = Horario.objects.select_related(
        'id_asignacion_materia__id_materia',
        'id_asignacion_materia__id_maestro__id_usuario',
        'id_asignacion_materia__id_grupo'
    )

    # Filtrar por grupo si está seleccionado (buscar de forma flexible)
    if grupo_filtro:
        # Primero buscar exacto insensible a mayúsculas
        grupo_obj = Grupo.objects.filter(clave__iexact=grupo_filtro).first()
        # Si no hay coincidencia exacta, intentar contains (para claves con formato distinto)
        if not grupo_obj:
            grupo_obj = Grupo.objects.filter(clave__icontains=grupo_filtro).first()

        if grupo_obj:
            horarios = horarios.filter(id_asignacion_materia__id_grupo=grupo_obj)
        else:
            # Intento extra: normalizar quitando caracteres no alfanuméricos y buscar por regex
            clave_normalizada = re.sub(r'[^A-Za-z0-9]', '', grupo_filtro).strip()
            if clave_normalizada:
                try:
                    grupo_obj = Grupo.objects.filter(clave__iregex=rf'{re.escape(clave_normalizada)}').first()
                except Exception:
                    grupo_obj = None
            if grupo_obj:
                horarios = horarios.filter(id_asignacion_materia__id_grupo=grupo_obj)
            else:
                horarios = Horario.objects.none()

    # Debug: contar por estatus para entender por qué no aparecen horarios
    try:
        from collections import Counter
        estatus_counts = Counter(h.estatus for h in horarios)
    except Exception:
        estatus_counts = {}
    logger.info(f"[get_horarios_semanales] Grupo filtro: '{grupo_filtro}' | Estatus counts: {estatus_counts} | Total posibles: {horarios.count()}")

    # Mapeo inverso de días del modelo al formulario
    dia_map_inverse = {
        'Lunes': 'lunes',
        'Martes': 'martes',
        'Miercoles': 'miercoles',
        'Jueves': 'jueves',
        'Viernes': 'viernes',
        'Sabado': 'sabado',
        'Domingo': 'domingo'
    }

    horarios_data = []
    for horario in horarios:
        asignacion = horario.id_asignacion_materia
        horarios_data.append({
            'dia': dia_map_inverse.get(horario.dia_semana, horario.dia_semana.lower()),
            'materia': asignacion.id_materia.nombre,
            'docente': f"{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}",
            'grupo': asignacion.id_grupo.clave,
            'aula': horario.aula,
            'hora_inicio': str(horario.hora_inicio),
            'hora_fin': str(horario.hora_fin),
            'segmentos': [_ for _ in _split_horario_en_unidades(horario)],
        })

    return JsonResponse({'horarios': horarios_data})


def dashboard_administrador(request):
    if not sesion_roles_permitidas(request, ('admin',)):
        return redirect('selector_rol')
    
    # Obtener datos del administrador
    try:
        administrador = Administrador.objects.select_related('id_usuario').get(id_usuario=request.session['administrador_id'])
        perfil = {
            'nombre_completo': request.session['usuario_nombre'],
            'matricula': request.session['usuario_matricula'],
            'puesto': administrador.puesto,
            'nivel_prioridad': administrador.nivel_prioridad
        }
    except:
        perfil = {
            'nombre_completo': request.session.get('usuario_nombre', 'Usuario'),
            'matricula': request.session.get('usuario_matricula', 'N/A')
        }
    
    # Obtener el periodo actual
    periodo_actual = _periodo_actual()
    
    # Obtener el ciclo escolar actual basado en el periodo actual
    from .models import CicloEscolar
    ciclo_actual = CicloEscolar.objects.filter(
        nombre_ciclo=periodo_actual
    ).first()
    
    fecha_fin_curso = ciclo_actual.fecha_fin if ciclo_actual else None
    
    context = {
        'perfil': perfil,
        'avisos': {
            'fin_semestre': fecha_fin_curso.strftime('%d/%m/%Y') if fecha_fin_curso else '---'
        },
        'config': {
            'periodo': periodo_actual
        }
    }
    
    return render(request, 'administrador/administrador.html', context)

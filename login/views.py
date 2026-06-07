import io
import json
import logging
import os
import re
import unicodedata
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
from django.urls import reverse
from django.db import transaction, models
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from urllib.parse import urlencode

from .password_utils import LONGITUD_MINIMA_USUARIO, validar_contrasena_usuario
from .admin_horarios_export import generar_pdf_horario_grupo, obtener_datos_horario_pdf
from .admin_materias_export import generar_excel_catalogo_materias, generar_pdf_catalogo_materias
from .alumno_asistencias_export import generar_pdf_asistencias_alumno
from .alumno_boleta_export import generar_pdf_boleta_alumno
from .maestro_reportes_export import (
    generar_excel_reportes_maestro,
    generar_excel_sesion_asistencia_maestro,
    generar_pdf_reportes_maestro,
    generar_pdf_sesion_asistencia_maestro,
)
from .datos_personales_utils import validar_datos_perfil_usuario
from .periodo_utils import calcular_semestre_desde_ingreso, resolver_semestre_alumno
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
_VALOR_CALIFICACION_VACIA = '---'

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
        return _VALOR_CALIFICACION_VACIA

    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return _VALOR_CALIFICACION_VACIA

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


def _normalizar_hora_hm(valor: str) -> str:
    """Convierte HH:MM o HH:MM:SS a HH:MM."""
    valor = (valor or '').strip()
    if not valor:
        return ''
    partes = valor.split(':')
    if len(partes) < 2:
        return valor
    try:
        horas = int(partes[0])
        minutos = int(partes[1])
    except ValueError:
        return valor
    return f'{horas:02d}:{minutos:02d}'


def _minutos_desde_medianoche(hora_hm: str) -> int | None:
    hora_hm = _normalizar_hora_hm(hora_hm)
    if not hora_hm or ':' not in hora_hm:
        return None
    try:
        horas, minutos = hora_hm.split(':')
        return int(horas) * 60 + int(minutos)
    except ValueError:
        return None


def _hora_fin_posterior(inicio: str, fin: str) -> bool:
    ini = _minutos_desde_medianoche(inicio)
    fin_m = _minutos_desde_medianoche(fin)
    if ini is None or fin_m is None:
        return False
    return fin_m > ini


def _formatear_hora_12h(hora_hm: str) -> str:
    hora_hm = _normalizar_hora_hm(hora_hm)
    if not hora_hm or ':' not in hora_hm:
        return hora_hm
    try:
        horas, minutos = hora_hm.split(':')
        h, m = int(horas), int(minutos)
    except ValueError:
        return hora_hm
    periodo = 'a.m.' if h < 12 else 'p.m.'
    h12 = h % 12 or 12
    return f'{h12}:{m:02d} {periodo}'


def _mensaje_horario_invalido(inicio: str, fin: str, dia: str | None = None) -> str:
    ini = _normalizar_hora_hm(inicio)
    fin_h = _normalizar_hora_hm(fin)
    prefijo = f'En {dia}, l' if dia else 'L'
    msg = (
        f'{prefijo}a hora de término ({_formatear_hora_12h(fin_h)}) debe ser posterior '
        f'a la de inicio ({_formatear_hora_12h(ini)}).'
    )
    ini_min = _minutos_desde_medianoche(ini)
    fin_min = _minutos_desde_medianoche(fin_h)
    if (
        ini_min is not None
        and fin_min is not None
        and fin_min <= ini_min
        and ini_min >= 7 * 60
        and fin_min < 7 * 60
    ):
        msg += (
            ' Revisa que la hora de término no esté en a.m.; '
            'para clases de mañana o tarde usa p.m. (ej. 12:30 p.m.).'
        )
    return msg


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


def _normalizar_dia_semana(valor: str) -> str:
    """Compara días sin depender de acentos (BD: Miercoles vs UI: Miércoles)."""
    if not valor:
        return ''
    texto = unicodedata.normalize('NFD', str(valor))
    texto = ''.join(caracter for caracter in texto if unicodedata.category(caracter) != 'Mn')
    return texto.strip().lower()


def _dias_semana_coinciden(dia_horario: str, dia_fecha: str) -> bool:
    return _normalizar_dia_semana(dia_horario) == _normalizar_dia_semana(dia_fecha)


def _normalizar_orden_texto(texto: str) -> str:
    if not texto:
        return ''
    texto = unicodedata.normalize('NFD', str(texto))
    texto = ''.join(caracter for caracter in texto if unicodedata.category(caracter) != 'Mn')
    return texto.casefold()


def _partes_apellido_alumno(apellido: str) -> tuple[str, str]:
    """Separa primer apellido del resto (p. ej. Arguelles | Ceballos)."""
    partes = (apellido or '').strip().split()
    if not partes:
        return '', ''
    return partes[0], ' '.join(partes[1:])


def _clave_orden_primer_apellido_usuario(usuario) -> tuple[str, str, str]:
    primer, resto = _partes_apellido_alumno(getattr(usuario, 'apellido', '') or '')
    return (
        _normalizar_orden_texto(primer),
        _normalizar_orden_texto(resto),
        _normalizar_orden_texto(getattr(usuario, 'nombre', '') or ''),
    )


def _campos_orden_alumno_usuario(usuario) -> dict:
    primer, resto = _partes_apellido_alumno(usuario.apellido)
    return {
        'primer_apellido': primer,
        'resto_apellido': resto,
        'nombre_orden': usuario.nombre,
    }


def _ordenar_inscripciones_por_primer_apellido(inscripciones) -> list:
    return sorted(
        inscripciones,
        key=lambda inscripcion: _clave_orden_primer_apellido_usuario(inscripcion.id_alumno.id_usuario),
    )


def _ordenar_lista_alumnos_dict(alumnos: list[dict]) -> list[dict]:
    return sorted(
        alumnos,
        key=lambda alumno: (
            _normalizar_orden_texto(alumno.get('primer_apellido', '')),
            _normalizar_orden_texto(alumno.get('resto_apellido', '')),
            _normalizar_orden_texto(alumno.get('nombre_orden', '')),
        ),
    )


def _periodo_actual() -> str:
    hoy = timezone.now()
    if hoy.month <= 6:
        periodo = 'A'
    elif hoy.month >= 8:
        periodo = 'B'
    else:
        periodo = 'A'
    return f"{hoy.year}-{periodo}"


def _contexto_dashboard_ciclo() -> dict:
    """Datos del ciclo escolar para paneles administrador y administrativo."""
    periodo_actual = _periodo_actual()
    ciclo_actual = CicloEscolar.objects.filter(nombre_ciclo=periodo_actual).first()
    periodo_letra = periodo_actual.rsplit('-', 1)[-1] if '-' in periodo_actual else ''
    periodo_descripcion = {
        'A': 'Enero – Junio',
        'B': 'Agosto – Diciembre',
    }.get(periodo_letra, '')

    hoy = timezone.now().date()
    fecha_inicio = ciclo_actual.fecha_inicio if ciclo_actual else None
    fecha_fin = ciclo_actual.fecha_fin if ciclo_actual else None

    dias_restantes = None
    progreso = None
    estado_ciclo = None
    if fecha_inicio and fecha_fin:
        dias_restantes = (fecha_fin - hoy).days
        total_dias = (fecha_fin - fecha_inicio).days
        if total_dias > 0:
            transcurrido = max(0, (hoy - fecha_inicio).days)
            progreso = min(100, round(transcurrido / total_dias * 100))
        if hoy < fecha_inicio:
            estado_ciclo = {'codigo': 'pendiente', 'etiqueta': 'Por iniciar'}
        elif hoy > fecha_fin:
            estado_ciclo = {'codigo': 'finalizado', 'etiqueta': 'Finalizado'}
        else:
            estado_ciclo = {'codigo': 'en_curso', 'etiqueta': 'En curso'}

    return {
        'avisos': {
            'fin_semestre': fecha_fin.strftime('%d/%m/%Y') if fecha_fin else None,
            'inicio_semestre': fecha_inicio.strftime('%d/%m/%Y') if fecha_inicio else None,
            'dias_restantes': dias_restantes,
            'tiene_ciclo': ciclo_actual is not None,
        },
        'config': {
            'periodo': periodo_actual,
            'periodo_letra': periodo_letra,
            'periodo_descripcion': periodo_descripcion,
            'progreso': progreso,
            'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y') if fecha_inicio else None,
            'fecha_fin': fecha_fin.strftime('%d/%m/%Y') if fecha_fin else None,
            'estado_ciclo': estado_ciclo,
        },
    }


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


def cambiar_contrasena_temporal(request):
    """Vista obligatoria para cambiar contraseña temporal antes de acceder al sistema."""
    usuario_id = request.session.get('usuario_id')
    if not usuario_id:
        return redirect('selector_rol')
    
    try:
        usuario = Usuarios.objects.get(id_usuario=usuario_id)
    except Usuarios.DoesNotExist:
        request.session.flush()
        return redirect('selector_rol')
    
    # Si ya no tiene contraseña temporal, redirigir al dashboard correspondiente
    if not usuario.contrasena_temporal:
        rol = request.session.get('usuario_rol')
        dashboards = {
            'alumno': 'dashboard_alumno',
            'maestro': 'dashboard_maestro',
            'administrativo': 'dashboard_administrativo',
            'admin': 'dashboard_administrador',
        }
        return redirect(dashboards.get(rol, 'selector_rol'))
    
    error_msg = None
    errores_lista = None
    
    if request.method == 'POST':
        nueva_contrasena = request.POST.get('nueva_contrasena', '').strip()
        confirmar_contrasena = request.POST.get('confirmar_contrasena', '').strip()
        
        if not nueva_contrasena or not confirmar_contrasena:
            error_msg = 'Debes completar ambos campos.'
        elif nueva_contrasena != confirmar_contrasena:
            error_msg = 'Las contraseñas no coinciden.'
        else:
            valida, errores = validar_contrasena_usuario(nueva_contrasena, usuario)
            if not valida:
                errores_lista = errores
                error_msg = errores[0]
            else:
                usuario.contrasena = nueva_contrasena
                usuario.contrasena_temporal = False
                usuario.save()
                
                messages.success(request, '¡Contraseña actualizada exitosamente! Ya puedes acceder al sistema.')
                
                rol = request.session.get('usuario_rol')
                dashboards = {
                    'alumno': 'dashboard_alumno',
                    'maestro': 'dashboard_maestro',
                    'administrativo': 'dashboard_administrativo',
                    'admin': 'dashboard_administrador',
                }
                return redirect(dashboards.get(rol, 'selector_rol'))
    
    return render(request, 'cambiar_contrasena.html', {
        'usuario': usuario,
        'error_msg': error_msg,
        'errores_lista': errores_lista,
        'min_longitud_contrasena': LONGITUD_MINIMA_USUARIO,
    })


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
        semestre = resolver_semestre_alumno(
            alumno.periodo_ingreso, alumno.semestre, _periodo_actual()
        )

        perfil.update({
            'id_usuario': alumno.id_usuario.id_usuario,
            'matricula': alumno.id_usuario.matricula,
            'nombre_completo': f"{alumno.id_usuario.nombre} {alumno.id_usuario.apellido}",
            'foto_url': alumno.id_usuario.foto.url if alumno.id_usuario.foto else '',
            'carrera': str(alumno.id_carrera) if alumno.id_carrera else '---',
            'semestre': semestre,
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
            'foto_url': usuario.foto.url if usuario.foto else '',
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
                    
                    # Si tiene contraseña temporal, redirigir a cambio obligatorio
                    if usuario.contrasena_temporal:
                        if is_ajax:
                            return JsonResponse({'success': True, 'redirect': '/cambiar-contrasena/'})
                        return redirect('cambiar_contrasena_temporal')
                    
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
                    
                    # Si tiene contraseña temporal, redirigir a cambio obligatorio
                    if usuario.contrasena_temporal:
                        if is_ajax:
                            return JsonResponse({'success': True, 'redirect': '/cambiar-contrasena/'})
                        return redirect('cambiar_contrasena_temporal')
                    
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
                    
                    # Si tiene contraseña temporal, redirigir a cambio obligatorio
                    if usuario.contrasena_temporal:
                        if is_ajax:
                            return JsonResponse({'success': True, 'redirect': '/cambiar-contrasena/'})
                        return redirect('cambiar_contrasena_temporal')
                    
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
                    
                    # Si tiene contraseña temporal, redirigir a cambio obligatorio
                    if usuario.contrasena_temporal:
                        if is_ajax:
                            return JsonResponse({'success': True, 'redirect': '/cambiar-contrasena/'})
                        return redirect('cambiar_contrasena_temporal')
                    
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

    # Procesar POST para guardar datos personales
    if request.method == 'POST':
        try:
            usuario_id = request.session.get('usuario_id')
            usuario = Usuarios.objects.get(id_usuario=usuario_id)
            
            # Obtener o crear registro en DatosPersonales
            datos_personales, _ = DatosPersonales.objects.get_or_create(id_usuario=usuario)
            
            # Obtener datos del formulario
            correo_institucional = request.POST.get('correo_institucional', '').strip()
            telefono = request.POST.get('telefono', '').strip()
            calle = request.POST.get('calle', '').strip()
            numero_exterior = request.POST.get('numero_exterior', '').strip()
            numero_interior = request.POST.get('numero_interior', '').strip()
            colonia = request.POST.get('colonia', '').strip()
            municipio = request.POST.get('municipio', '').strip()
            estado = request.POST.get('estado', '').strip()
            cp = request.POST.get('cp', '').strip()

            errores_campos = validar_datos_perfil_usuario(
                correo_institucional, telefono, cp, usuario.id_usuario
            )
            if errores_campos:
                messages.error(request, 'Corrige los errores en el formulario.')
                perfil_guardado = _perfil_alumno(request)
                perfil = perfil_guardado.copy()
                perfil.update({
                    'correo_institucional': correo_institucional,
                    'telefono': telefono,
                    'calle': calle,
                    'numero_exterior': numero_exterior,
                    'numero_interior': numero_interior,
                    'colonia': colonia,
                    'municipio': municipio,
                    'estado': estado,
                    'cp': cp,
                })
                return render(request, 'alumno/alumno.html', {
                    'perfil': perfil,
                    'perfil_guardado': perfil_guardado,
                    'errores_campos': errores_campos,
                    'modo_edicion': True,
                })
            
            # Construir dirección concatenada (igual que admin_views.construir_direccion)
            partes = [calle, numero_exterior, numero_interior, colonia, municipio, estado, cp]
            partes = [parte for parte in partes if parte]
            direccion = ', '.join(partes) if partes else None
            
            # Actualizar campos
            datos_personales.correo_inst = correo_institucional or None
            datos_personales.telefono = telefono
            datos_personales.direccion = direccion
            datos_personales.save()
            
            messages.success(request, 'Datos guardados correctamente')
        except Exception as e:
            messages.error(request, f'Error al guardar: {str(e)}')

    perfil = _perfil_alumno(request)
    return render(request, 'alumno/alumno.html', {'perfil': perfil})


def _obtener_calificaciones_rows_alumno(alumno) -> list[dict]:
    """Arma las filas de la boleta de calificaciones para un alumno."""
    if not alumno:
        return []

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

    rows = []
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
                unidades.append(_VALOR_CALIFICACION_VACIA)

        promedio = sum(valores) / len(valores) if valores else None
        tiene_unidad_menor_70 = any(v < MINIMO_APROBATORIO_CALIFICACION for v in valores)

        rows.append({
            'materia': asignacion.id_materia.nombre,
            'codigo': asignacion.id_materia.clave,
            'grupo': asignacion.id_grupo.clave,
            'unidades': unidades,
            'promedio': 'NA' if tiene_unidad_menor_70 else (
                _formatear_calificacion_visible(promedio) if promedio is not None else _VALOR_CALIFICACION_VACIA
            ),
        })

    return rows


def _calcular_promedio_general_boleta(rows: list[dict]):
    """Calcula el promedio general ignorando materias con NA (misma lógica que la plantilla)."""
    suma_promedios = 0
    contador_validos = 0

    for row in rows:
        tiene_unidad_menor_70 = False
        for unidad in row.get('unidades', []):
            if unidad not in ('—', _VALOR_CALIFICACION_VACIA):
                try:
                    if float(unidad) < float(MINIMO_APROBATORIO_CALIFICACION):
                        tiene_unidad_menor_70 = True
                        break
                except (ValueError, TypeError):
                    pass

        if not tiene_unidad_menor_70:
            try:
                suma_promedios += float(row.get('promedio', 0))
                contador_validos += 1
            except (ValueError, TypeError):
                pass

    if contador_validos > 0:
        return round(suma_promedios / contador_validos, 2)
    return 0


def _ruta_foto_alumno(alumno) -> str | None:
    if not alumno or not alumno.id_usuario.foto:
        return None
    try:
        ruta = alumno.id_usuario.foto.path
    except Exception:
        return None
    return ruta if os.path.isfile(ruta) else None


def consultar_calificaciones(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()
    rows = _obtener_calificaciones_rows_alumno(alumno)

    context = {
        'perfil': perfil,
        'calificaciones_rows': rows,
        'promedio_general': _formatear_calificacion_visible(
            _calcular_promedio_general_boleta(rows) if rows else None,
            mostrar_na_si_menor=True,
        ) if rows else '0.0',
        'hay_calificaciones': bool(rows),
    }
    return render(request, 'alumno/calificaciones.html', context)


def exportar_boleta_calificaciones_alumno(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()
    rows = _obtener_calificaciones_rows_alumno(alumno)
    promedio_general = str(_calcular_promedio_general_boleta(rows))
    ahora = datetime.now()

    pdf_bytes = generar_pdf_boleta_alumno(
        perfil=perfil,
        rows=rows,
        promedio_general=promedio_general,
        ahora=ahora,
        foto_ruta=_ruta_foto_alumno(alumno),
    )

    matricula = perfil.get('matricula') or 'alumno'
    nombre_archivo = f'boleta_calificaciones_{matricula}_{ahora.strftime("%Y%m%d_%H%M%S")}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def _resumen_asistencias_vacio() -> dict:
    return {
        'total': 0,
        'presentes': 0,
        'ausentes': 0,
        'tarde': 0,
        'justificado': 0,
        'porcentaje': '0.0',
    }


def _obtener_datos_asistencias_alumno(
    alumno,
    *,
    asignacion_id: str = '',
    unidad_str: str = '',
    fecha_inicio_str: str = '',
    fecha_fin_str: str = '',
    limite: int = 250,
) -> tuple[list[dict], dict, list]:
    rows = []
    total_registros = 0
    total_presentes = 0
    total_ausentes = 0
    total_tarde = 0
    total_justificado = 0
    asignaciones = []

    if not alumno:
        return rows, _resumen_asistencias_vacio(), asignaciones

    inscripciones = list(
        Inscripcion.objects.select_related('id_grupo', 'id_ciclo_escolar')
        .filter(id_alumno=alumno, estatus='Activa')
        .order_by('id_grupo__clave')
    )
    grupos_ids = [inscripcion.id_grupo_id for inscripcion in inscripciones]
    asignaciones = list(
        AsignacionMateria.objects.select_related('id_materia', 'id_grupo', 'id_ciclo_escolar', 'id_maestro__id_usuario')
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

    for asistencia in asistencias_qs[:limite]:
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
            'materia': asignacion.id_materia.nombre,
            'materia_clave': asignacion.id_materia.clave,
            'grupo': asignacion.id_grupo.clave,
            'horario': (
                f'{asistencia.id_horario.dia_semana} '
                f'{asistencia.id_horario.hora_inicio.strftime("%H:%M")} - '
                f'{asistencia.id_horario.hora_fin.strftime("%H:%M")}'
            ),
            'estatus': asistencia.estatus,
            'observaciones': asistencia.observaciones or '',
            'maestro': f'{asignacion.id_maestro.id_usuario.nombre} {asignacion.id_maestro.id_usuario.apellido}',
        })

    porcentaje = (total_presentes / total_registros * 100) if total_registros else 0
    resumen = {
        'total': total_registros,
        'presentes': total_presentes,
        'ausentes': total_ausentes,
        'tarde': total_tarde,
        'justificado': total_justificado,
        'porcentaje': f'{porcentaje:.1f}',
    }
    return rows, resumen, asignaciones


def _filtros_texto_asistencias_alumno(
    *,
    asignacion_id: str,
    unidad_str: str,
    fecha_inicio_str: str,
    fecha_fin_str: str,
    asignaciones: list,
) -> list[str]:
    filtros = []
    if asignacion_id:
        asignacion = next(
            (item for item in asignaciones if str(item.id_asignacion_materia) == asignacion_id),
            None,
        )
        if asignacion:
            filtros.append(
                f'Materia: {asignacion.id_materia.clave} - {asignacion.id_materia.nombre}'
            )
    if unidad_str:
        filtros.append(f'Unidad: {unidad_str}')
    if fecha_inicio_str:
        filtros.append(f'Desde: {fecha_inicio_str}')
    if fecha_fin_str:
        filtros.append(f'Hasta: {fecha_fin_str}')
    return filtros


def _query_exportar_asistencias_alumno(request) -> str:
    params = {
        clave: request.GET.get(clave, '').strip()
        for clave in ('asignacion_id', 'unidad', 'fecha_inicio', 'fecha_fin')
        if request.GET.get(clave, '').strip()
    }
    query = urlencode(params)
    base = reverse('exportar_asistencias_alumno')
    return f'{base}?{query}' if query else base


def consultar_asistencias(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()

    asignacion_id = request.GET.get('asignacion_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()
    fecha_inicio_str = request.GET.get('fecha_inicio', '').strip()
    fecha_fin_str = request.GET.get('fecha_fin', '').strip()
    reporte_solicitado = bool(request.GET)

    rows, resumen, asignaciones = _obtener_datos_asistencias_alumno(
        alumno,
        asignacion_id=asignacion_id,
        unidad_str=unidad_str,
        fecha_inicio_str=fecha_inicio_str,
        fecha_fin_str=fecha_fin_str,
    )

    context = {
        'perfil': perfil,
        'asistencias_rows': rows,
        'hay_asistencias': bool(rows),
        'filtro_asignacion_id': asignacion_id,
        'filtro_unidad': unidad_str,
        'filtro_fecha_inicio': fecha_inicio_str,
        'filtro_fecha_fin': fecha_fin_str,
        'reporte_solicitado': reporte_solicitado,
        'asignaciones_disponibles': asignaciones,
        'resumen_asistencias': resumen,
        'exportar_pdf_url': _query_exportar_asistencias_alumno(request),
    }
    return render(request, 'alumno/asistencias.html', context)


def exportar_asistencias_alumno(request):
    if not sesion_roles_permitidas(request, ('alumno',)):
        return redirect('selector_rol')

    perfil = _perfil_alumno(request)
    alumno = Alumnos.objects.select_related('id_usuario').filter(pk=request.session.get('alumno_id')).first()

    asignacion_id = request.GET.get('asignacion_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()
    fecha_inicio_str = request.GET.get('fecha_inicio', '').strip()
    fecha_fin_str = request.GET.get('fecha_fin', '').strip()

    rows, resumen, asignaciones = _obtener_datos_asistencias_alumno(
        alumno,
        asignacion_id=asignacion_id,
        unidad_str=unidad_str,
        fecha_inicio_str=fecha_inicio_str,
        fecha_fin_str=fecha_fin_str,
        limite=500,
    )
    filtros = _filtros_texto_asistencias_alumno(
        asignacion_id=asignacion_id,
        unidad_str=unidad_str,
        fecha_inicio_str=fecha_inicio_str,
        fecha_fin_str=fecha_fin_str,
        asignaciones=asignaciones,
    )
    ahora = datetime.now()

    pdf_bytes = generar_pdf_asistencias_alumno(
        perfil=perfil,
        rows=rows,
        resumen=resumen,
        filtros=filtros or None,
        ahora=ahora,
        foto_ruta=_ruta_foto_alumno(alumno),
    )

    matricula = perfil.get('matricula') or 'alumno'
    nombre_archivo = f'reporte_asistencias_{matricula}_{ahora.strftime("%Y%m%d_%H%M%S")}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def dashboard_maestro(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    # Procesar POST para guardar datos personales
    if request.method == 'POST':
        try:
            usuario_id = request.session.get('usuario_id')
            usuario = Usuarios.objects.get(id_usuario=usuario_id)
            
            # Obtener o crear registro en DatosPersonales
            datos_personales, _ = DatosPersonales.objects.get_or_create(id_usuario=usuario)
            
            # Obtener datos del formulario
            telefono = request.POST.get('telefono', '').strip()
            calle = request.POST.get('calle', '').strip()
            numero_exterior = request.POST.get('numero_exterior', '').strip()
            numero_interior = request.POST.get('numero_interior', '').strip()
            colonia = request.POST.get('colonia', '').strip()
            ciudad = request.POST.get('ciudad', '').strip()
            estado = request.POST.get('estado', '').strip()
            cp = request.POST.get('cp', '').strip()
            correo_institucional = request.POST.get('correo_institucional', '').strip()
            curp = request.POST.get('curp', '').strip()

            errores_campos = validar_datos_perfil_usuario(
                correo_institucional, telefono, cp, usuario.id_usuario
            )
            if errores_campos:
                messages.error(request, 'Corrige los errores en el formulario.')
                perfil_guardado = _perfil_maestro(request)
                perfil = perfil_guardado.copy()
                perfil.update({
                    'correo_institucional': correo_institucional,
                    'telefono': telefono,
                    'calle': calle,
                    'numero_exterior': numero_exterior,
                    'numero_interior': numero_interior,
                    'colonia': colonia,
                    'ciudad': ciudad,
                    'estado': estado,
                    'cp': cp,
                })
                return render(request, 'maestro/maestro.html', {
                    'perfil': perfil,
                    'perfil_guardado': perfil_guardado,
                    'errores_campos': errores_campos,
                    'modo_edicion': True,
                })
            
            # Construir dirección concatenada (igual que admin_views.construir_direccion)
            partes = [calle, numero_exterior, numero_interior, colonia, ciudad, estado, cp]
            partes = [parte for parte in partes if parte]
            direccion = ', '.join(partes) if partes else None
            
            # Actualizar campos
            datos_personales.telefono = telefono
            datos_personales.direccion = direccion
            datos_personales.correo_inst = correo_institucional or None
            if curp:
                datos_personales.curp = curp
            datos_personales.save()
            
            messages.success(request, 'Datos guardados correctamente')
        except Exception as e:
            messages.error(request, f'Error al guardar: {str(e)}')

    perfil = _perfil_maestro(request)
    return render(request, 'maestro/maestro.html', {'perfil': perfil})


def registrar_asistencia(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    return render(request, 'maestro/RegistrarAsistencia.html', _contexto_maestro(request))


def registrar_calificaciones(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return redirect('selector_rol')

    contexto = _contexto_maestro(request)
    asignaciones_docente = contexto.get('asignaciones_docente', [])
    contexto['periodo_actual'] = _periodo_actual()
    contexto['asignaciones_calificaciones'] = _asignaciones_maestro_serializadas(asignaciones_docente)
    return render(request, 'maestro/RegistrarCalificaciones.html', contexto)


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

    inscripciones = _ordenar_inscripciones_por_primer_apellido(
        Inscripcion.objects.select_related('id_alumno__id_usuario').filter(
            id_grupo=asignacion.id_grupo,
            estatus='Activa',
        )
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
            **_campos_orden_alumno_usuario(usuario),
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
        'alumnos': _ordenar_lista_alumnos_dict(alumnos),
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


def _asignaciones_maestro_serializadas(asignaciones_docente) -> list[dict]:
    return [
        {
            'id': asignacion.id_asignacion_materia,
            'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
            'grupo': asignacion.id_grupo.clave,
            'grupo_nombre': asignacion.id_grupo.nombre,
            'materia_id': asignacion.id_materia.id_materia,
            'materia_clave': asignacion.id_materia.clave,
            'materia_nombre': asignacion.id_materia.nombre,
        }
        for asignacion in asignaciones_docente
    ]


def _parse_filtros_reportes_maestro(request) -> tuple[str, str, int | None, bool]:
    reporte_tipo = request.GET.get('tipo', 'asistencias').strip().lower()
    asignacion_id = request.GET.get('asignacion_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()

    if reporte_tipo not in ('asistencias', 'calificaciones'):
        reporte_tipo = 'asistencias'

    if not unidad_str:
        return reporte_tipo, asignacion_id, None, False

    try:
        return reporte_tipo, asignacion_id, int(unidad_str), True
    except ValueError:
        return reporte_tipo, asignacion_id, None, False


def _query_reportes_maestro(request) -> str:
    reporte_tipo, asignacion_id, unidad, valido = _parse_filtros_reportes_maestro(request)
    if not valido:
        return ''
    params = {'tipo': reporte_tipo, 'unidad': str(unidad)}
    if asignacion_id:
        params['asignacion_id'] = asignacion_id
    vista = request.GET.get('vista', '').strip()
    if vista and vista != 'lista':
        params['vista'] = vista
    fecha = request.GET.get('fecha', '').strip()
    if fecha:
        params['fecha'] = fecha
    horario_id = request.GET.get('horario_id', '').strip()
    if horario_id:
        params['horario_id'] = horario_id
    return '?' + urlencode(params)


def _vista_asistencias_reporte(request, reporte_tipo: str) -> str:
    vista = request.GET.get('vista', 'lista').strip().lower()
    if reporte_tipo != 'asistencias' or vista not in ('lista', 'sesion'):
        return 'lista'
    return vista


def _obtener_consulta_asistencia_sesion(
    maestro: Maestros,
    asignacion_id: str,
    fecha_obj: date,
    unidad: int,
    horario_id: str | None = None,
) -> dict | None:
    """Pase de lista por sesión (solo lectura). No asume Presente si no hay registro."""
    asignacion = (
        AsignacionMateria.objects.select_related(
            'id_materia',
            'id_grupo__id_ciclo_escolar',
            'id_maestro__id_usuario',
        )
        .filter(pk=asignacion_id, id_maestro=maestro, estatus='Activa')
        .first()
    )
    if not asignacion:
        return None

    try:
        datos = _obtener_datos_asistencia_maestro(
            maestro,
            asignacion_id,
            fecha_obj,
            unidad,
            horario_id,
        )
    except Exception:
        return None

    horario_sugerido_id = datos.get('horario_sugerido_id')
    asistencias_existentes: dict[int, dict] = {}
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

    horario_sel = None
    for horario in datos.get('horarios', []):
        if horario.get('id_horario') == horario_sugerido_id:
            horario_sel = horario
            break

    horarios_del_dia = [h for h in datos.get('horarios', []) if h.get('es_del_dia')]
    conteos = {
        'Presente': 0,
        'Ausente': 0,
        'Tarde': 0,
        'Justificado': 0,
        'Sin registro': 0,
    }
    alumnos_sesion = []
    for alumno in datos.get('alumnos', []):
        id_inscripcion = alumno['id_inscripcion']
        if id_inscripcion in asistencias_existentes:
            registro = asistencias_existentes[id_inscripcion]
            estatus = registro['estatus']
            observaciones = registro['observaciones']
            registrado = True
        else:
            estatus = 'Sin registro'
            observaciones = ''
            registrado = False

        conteos[estatus] = conteos.get(estatus, 0) + 1
        alumnos_sesion.append({
            'matricula': alumno['matricula'],
            'nombre': alumno['nombre_completo'],
            'estado': estatus,
            'observaciones': observaciones,
            'registrado': registrado,
            'primer_apellido': alumno.get('primer_apellido', ''),
            'resto_apellido': alumno.get('resto_apellido', ''),
            'nombre_orden': alumno.get('nombre_orden', ''),
        })

    alumnos_sesion = _ordenar_lista_alumnos_dict(alumnos_sesion)

    horario_texto = ''
    if horario_sel:
        horario_texto = (
            f"{horario_sel['dia_semana']} "
            f"{horario_sel['hora_inicio']}-{horario_sel['hora_fin']}"
        )

    return {
        'fecha': fecha_obj.strftime('%d/%m/%Y'),
        'fecha_iso': fecha_obj.isoformat(),
        'dia_semana': datos.get('dia_semana', _dia_semana_es(fecha_obj)),
        'unidad': unidad,
        'asignacion': datos.get('asignacion', {}),
        'horario_id': horario_sugerido_id,
        'horario_texto': horario_texto,
        'horarios': horarios_del_dia,
        'alumnos': alumnos_sesion,
        'conteos': conteos,
        'total_alumnos': len(alumnos_sesion),
        'total_registrados': sum(1 for alumno in alumnos_sesion if alumno['registrado']),
        'sin_registro': conteos.get('Sin registro', 0),
    }


def _etiqueta_asignacion_reporte(asignacion_id: str) -> str | None:
    if not asignacion_id:
        return None
    asignacion = (
        AsignacionMateria.objects.select_related(
            'id_materia',
            'id_grupo',
            'id_ciclo_escolar',
        )
        .filter(pk=asignacion_id)
        .first()
    )
    if not asignacion:
        return f'Asignación #{asignacion_id}'
    return (
        f'{asignacion.id_materia.clave} - {asignacion.id_grupo.clave} '
        f'({asignacion.id_ciclo_escolar.nombre_ciclo})'
    )


def _filtros_texto_sesion_asistencia(sesion_consulta: dict) -> list[str]:
    asignacion = sesion_consulta.get('asignacion', {})
    materia = asignacion.get('materia', {})
    grupo = asignacion.get('grupo', {})
    return [
        f"Materia: {materia.get('codigo', '')} - {materia.get('nombre', '')}",
        f"Grupo: {grupo.get('clave', '')}",
        f"Fecha: {sesion_consulta.get('fecha', '')} ({sesion_consulta.get('dia_semana', '')})",
        f"Horario: {sesion_consulta.get('horario_texto', '')}",
        f"Unidad: {sesion_consulta.get('unidad', '')}",
    ]


def _obtener_sesion_exportable(request, maestro: Maestros) -> tuple[dict | None, str | None]:
    reporte_tipo, asignacion_id, unidad, valido = _parse_filtros_reportes_maestro(request)
    if not valido or unidad is None:
        return None, 'Selecciona una unidad válida para exportar.'
    if _vista_asistencias_reporte(request, reporte_tipo) != 'sesion':
        return None, None

    filtro_fecha = request.GET.get('fecha', '').strip()
    filtro_horario_id = request.GET.get('horario_id', '').strip()
    if not asignacion_id:
        return None, 'Selecciona ciclo, grupo y materia para exportar la sesión.'
    if not filtro_fecha:
        return None, 'Selecciona la fecha de la sesión para exportar.'

    try:
        fecha_obj = _parse_fecha_iso(filtro_fecha)
    except ValueError:
        fecha_obj = None
    if fecha_obj is None:
        return None, 'La fecha seleccionada no es válida.'

    sesion_consulta = _obtener_consulta_asistencia_sesion(
        maestro,
        asignacion_id,
        fecha_obj,
        unidad,
        filtro_horario_id or None,
    )
    if sesion_consulta is None:
        return None, 'No se encontró la asignación seleccionada.'
    if not sesion_consulta.get('horarios'):
        return None, (
            f'No hay clase programada el {sesion_consulta.get("dia_semana", "")} '
            f'para esta materia.'
        )
    if not sesion_consulta.get('horario_id'):
        return None, 'Selecciona el horario de la sesión para exportar.'

    return sesion_consulta, None


def _nombre_archivo_sesion_asistencia(sesion_consulta: dict, extension: str, ahora: datetime) -> str:
    materia = sesion_consulta.get('asignacion', {}).get('materia', {})
    codigo = re.sub(r'[^\w\-]+', '_', str(materia.get('codigo', 'materia')))
    fecha_slug = str(sesion_consulta.get('fecha_iso', '')).replace('-', '')
    unidad = sesion_consulta.get('unidad', '')
    return (
        f'pase_lista_{codigo}_{fecha_slug}_u{unidad}_{ahora.strftime("%Y%m%d_%H%M%S")}.{extension}'
    )


def _filtros_texto_reportes_maestro(
    reporte_tipo: str,
    unidad: int | None,
    asignacion_id: str,
) -> list[str]:
    filtros = [
        f'Tipo: {"Asistencias" if reporte_tipo == "asistencias" else "Calificaciones"}',
        f'Unidad: {unidad}',
    ]
    etiqueta_asignacion = _etiqueta_asignacion_reporte(asignacion_id)
    if etiqueta_asignacion:
        filtros.append(f'Asignación: {etiqueta_asignacion}')
    else:
        filtros.append('Asignación: Todas')
    return filtros


def _obtener_reportes_maestro(
    maestro: Maestros,
    reporte_tipo: str,
    asignacion_id: str,
    unidad: int,
    *,
    limite: int | None = 500,
) -> list[dict]:
    reportes: list[dict] = []

    if reporte_tipo == 'asistencias':
        asistencias_qs = Asistencia.objects.select_related(
            'id_inscripcion__id_alumno__id_usuario',
            'id_horario__id_asignacion_materia__id_materia',
            'id_horario__id_asignacion_materia__id_grupo',
            'id_horario__id_asignacion_materia__id_ciclo_escolar',
            'id_horario__id_asignacion_materia__id_maestro__id_usuario',
        ).filter(id_horario__id_asignacion_materia__id_maestro=maestro)

        if asignacion_id:
            asistencias_qs = asistencias_qs.filter(id_horario__id_asignacion_materia__pk=asignacion_id)
        asistencias = list(asistencias_qs.filter(unidad=unidad))
        asistencias.sort(
            key=lambda asistencia: (
                -asistencia.fecha_asistencia.toordinal(),
                -asistencia.unidad,
                *_clave_orden_primer_apellido_usuario(asistencia.id_inscripcion.id_alumno.id_usuario),
            )
        )

        if limite is not None:
            asistencias = asistencias[:limite]

        for asistencia in asistencias:
            usuario_alumno = asistencia.id_inscripcion.id_alumno.id_usuario
            asignacion = asistencia.id_horario.id_asignacion_materia
            horario = asistencia.id_horario
            horario_texto = (
                f'{horario.dia_semana} '
                f'{horario.hora_inicio.strftime("%H:%M")}-{horario.hora_fin.strftime("%H:%M")}'
            )
            materia_texto = f'{asignacion.id_materia.clave} - {asignacion.id_materia.nombre}'
            reportes.append({
                'tipo': 'asistencia',
                'fecha': asistencia.fecha_asistencia.strftime('%d/%m/%Y'),
                'unidad': asistencia.unidad,
                'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
                'matricula': usuario_alumno.matricula,
                'materia': materia_texto,
                'grupo': asignacion.id_grupo.clave,
                'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
                'horario': horario_texto,
                'detalle': horario_texto,
                'estado': asistencia.estatus,
                'observaciones': asistencia.observaciones or '',
            })
        return reportes

    calificaciones_qs = Calificacion.objects.select_related(
        'id_inscripcion__id_alumno__id_usuario',
        'id_asignacion_materia__id_materia',
        'id_asignacion_materia__id_grupo',
        'id_asignacion_materia__id_ciclo_escolar',
        'id_asignacion_materia__id_maestro__id_usuario',
    ).filter(id_asignacion_materia__id_maestro=maestro)

    if asignacion_id:
        calificaciones_qs = calificaciones_qs.filter(id_asignacion_materia__pk=asignacion_id)
    calificaciones = list(calificaciones_qs.filter(unidad=unidad))
    calificaciones.sort(
        key=lambda calificacion: (
            -calificacion.fecha_registro.toordinal() if calificacion.fecha_registro else 0,
            -calificacion.unidad,
            *_clave_orden_primer_apellido_usuario(calificacion.id_inscripcion.id_alumno.id_usuario),
        )
    )

    if limite is not None:
        calificaciones = calificaciones[:limite]

    for calificacion in calificaciones:
        usuario_alumno = calificacion.id_inscripcion.id_alumno.id_usuario
        asignacion = calificacion.id_asignacion_materia
        calificacion_texto = _formatear_calificacion_visible(calificacion.calificacion)
        materia_texto = f'{asignacion.id_materia.clave} - {asignacion.id_materia.nombre}'
        reportes.append({
            'tipo': 'calificacion',
            'fecha': calificacion.fecha_registro.strftime('%d/%m/%Y'),
            'unidad': calificacion.unidad,
            'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
            'matricula': usuario_alumno.matricula,
            'materia': materia_texto,
            'grupo': asignacion.id_grupo.clave,
            'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
            'calificacion': calificacion_texto,
            'detalle': calificacion_texto,
            'estado': 'Calificación',
            'observaciones': calificacion.observaciones or '',
        })

    return reportes


def _maestro_sesion_o_redirect(request):
    if not sesion_roles_permitidas(request, ('maestro',)):
        return None, redirect('selector_rol')
    maestro = Maestros.objects.select_related('id_usuario').filter(pk=request.session.get('maestro_id')).first()
    if not maestro:
        return None, redirect('selector_rol')
    return maestro, None


def consultar_reportes(request):
    maestro, redirect_resp = _maestro_sesion_o_redirect(request)
    if redirect_resp:
        return redirect_resp

    contexto = _contexto_maestro(request)
    asignaciones_docente = contexto.get('asignaciones_docente', [])

    reporte_tipo, asignacion_id, unidad, consulta_solicitada = _parse_filtros_reportes_maestro(request)
    vista_asistencias = _vista_asistencias_reporte(request, reporte_tipo)
    filtro_fecha = request.GET.get('fecha', '').strip()
    filtro_horario_id = request.GET.get('horario_id', '').strip()

    reportes = []
    sesion_consulta = None
    sesion_error = None
    sesion_aviso = None

    if reporte_tipo == 'asistencias' and vista_asistencias == 'sesion':
        filtros_aplicados = consulta_solicitada
        if consulta_solicitada and unidad is not None:
            if not asignacion_id:
                sesion_error = 'Selecciona ciclo, grupo y materia para consultar por sesión.'
            elif not filtro_fecha:
                sesion_error = 'Selecciona la fecha de la sesión.'
            else:
                try:
                    fecha_obj = _parse_fecha_iso(filtro_fecha)
                except ValueError:
                    fecha_obj = None
                if fecha_obj is None:
                    sesion_error = 'La fecha seleccionada no es válida.'
                else:
                    sesion_consulta = _obtener_consulta_asistencia_sesion(
                        maestro,
                        asignacion_id,
                        fecha_obj,
                        unidad,
                        filtro_horario_id or None,
                    )
                    if sesion_consulta is None:
                        sesion_error = 'No se encontró la asignación seleccionada.'
                    elif not sesion_consulta['horarios']:
                        sesion_error = (
                            f'No hay clase programada el {sesion_consulta["dia_semana"]} '
                            f'para esta materia. Elige otra fecha.'
                        )
                    elif not sesion_consulta['horario_id']:
                        sesion_error = 'No se pudo determinar el horario de la sesión.'
                    elif len(sesion_consulta['horarios']) > 1 and not filtro_horario_id:
                        sesion_aviso = (
                            'Hay varios horarios este día. Se muestra el primero; '
                            'cambia el horario arriba y vuelve a consultar si necesitas otro.'
                        )
    else:
        filtros_aplicados = consulta_solicitada
        if consulta_solicitada and unidad is not None:
            reportes = _obtener_reportes_maestro(
                maestro,
                reporte_tipo,
                asignacion_id,
                unidad,
                limite=500,
            )

    context = {
        **contexto,
        'reportes': reportes,
        'sesion_consulta': sesion_consulta,
        'sesion_error': sesion_error,
        'sesion_aviso': sesion_aviso,
        'filtros_aplicados': filtros_aplicados,
        'reporte_tipo': reporte_tipo,
        'vista_asistencias': vista_asistencias,
        'filtro_asignacion_id': asignacion_id,
        'filtro_unidad': request.GET.get('unidad', '').strip(),
        'filtro_fecha': filtro_fecha,
        'filtro_horario_id': filtro_horario_id,
        'periodo_actual': _periodo_actual(),
        'asignaciones_reportes': _asignaciones_maestro_serializadas(asignaciones_docente),
        'query_reportes': _query_reportes_maestro(request),
    }
    return render(request, 'maestro/ConsultarReportes.html', context)


def exportar_reportes_maestro_excel(request):
    maestro, redirect_resp = _maestro_sesion_o_redirect(request)
    if redirect_resp:
        return redirect_resp

    reporte_tipo, asignacion_id, unidad, valido = _parse_filtros_reportes_maestro(request)
    if not valido or unidad is None:
        messages.error(request, 'Selecciona una unidad válida para exportar el reporte.')
        return redirect(reverse('consultar_reportes') + _query_reportes_maestro(request))

    usuario = maestro.id_usuario
    maestro_nombre = f'{usuario.nombre} {usuario.apellido}'.strip()
    ahora = datetime.now()

    sesion_consulta, error_sesion = _obtener_sesion_exportable(request, maestro)
    if error_sesion:
        messages.error(request, error_sesion)
        return redirect(reverse('consultar_reportes') + _query_reportes_maestro(request))

    if sesion_consulta is not None:
        output = generar_excel_sesion_asistencia_maestro(
            sesion_consulta,
            maestro_nombre=maestro_nombre,
            filtros=_filtros_texto_sesion_asistencia(sesion_consulta),
            ahora=ahora,
        )
        nombre_archivo = _nombre_archivo_sesion_asistencia(sesion_consulta, 'xlsx', ahora)
    else:
        reportes = _obtener_reportes_maestro(
            maestro,
            reporte_tipo,
            asignacion_id,
            unidad,
            limite=None,
        )
        output = generar_excel_reportes_maestro(
            reportes,
            reporte_tipo=reporte_tipo,
            maestro_nombre=maestro_nombre,
            filtros=_filtros_texto_reportes_maestro(reporte_tipo, unidad, asignacion_id),
            ahora=ahora,
        )
        prefijo = 'asistencias' if reporte_tipo == 'asistencias' else 'calificaciones'
        nombre_archivo = f'reporte_{prefijo}_u{unidad}_{ahora.strftime("%Y%m%d_%H%M%S")}.xlsx'

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def exportar_reportes_maestro_pdf(request):
    maestro, redirect_resp = _maestro_sesion_o_redirect(request)
    if redirect_resp:
        return redirect_resp

    reporte_tipo, asignacion_id, unidad, valido = _parse_filtros_reportes_maestro(request)
    if not valido or unidad is None:
        messages.error(request, 'Selecciona una unidad válida para exportar el reporte.')
        return redirect(reverse('consultar_reportes') + _query_reportes_maestro(request))

    usuario = maestro.id_usuario
    maestro_nombre = f'{usuario.nombre} {usuario.apellido}'.strip()
    ahora = datetime.now()

    sesion_consulta, error_sesion = _obtener_sesion_exportable(request, maestro)
    if error_sesion:
        messages.error(request, error_sesion)
        return redirect(reverse('consultar_reportes') + _query_reportes_maestro(request))

    if sesion_consulta is not None:
        pdf_bytes = generar_pdf_sesion_asistencia_maestro(
            sesion_consulta,
            maestro_nombre=maestro_nombre,
            filtros=_filtros_texto_sesion_asistencia(sesion_consulta),
            ahora=ahora,
        )
        nombre_archivo = _nombre_archivo_sesion_asistencia(sesion_consulta, 'pdf', ahora)
    else:
        reportes = _obtener_reportes_maestro(
            maestro,
            reporte_tipo,
            asignacion_id,
            unidad,
            limite=None,
        )
        pdf_bytes = generar_pdf_reportes_maestro(
            reportes,
            reporte_tipo=reporte_tipo,
            maestro_nombre=maestro_nombre,
            filtros=_filtros_texto_reportes_maestro(reporte_tipo, unidad, asignacion_id),
            ahora=ahora,
        )
        prefijo = 'asistencias' if reporte_tipo == 'asistencias' else 'calificaciones'
        nombre_archivo = f'reporte_{prefijo}_u{unidad}_{ahora.strftime("%Y%m%d_%H%M%S")}.pdf'

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


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

    inscripciones = _ordenar_inscripciones_por_primer_apellido(
        Inscripcion.objects.select_related('id_alumno__id_usuario').filter(
            id_grupo=asignacion.id_grupo,
            estatus='Activa',
        )
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
        serializado['es_del_dia'] = _dias_semana_coinciden(horario.dia_semana, dia_seleccionado)
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
                'guardada': True,
            }

    alumnos = []
    for inscripcion in inscripciones:
        usuario = inscripcion.id_alumno.id_usuario
        alumnos.append({
            'id_inscripcion': inscripcion.id_inscripcion,
            'matricula': usuario.matricula,
            'nombre_completo': f'{usuario.nombre} {usuario.apellido}',
            'estatus_alumno': inscripcion.estatus,
            'asistencia': asistencias_existentes.get(
                inscripcion.id_inscripcion,
                {'estatus': 'Presente', 'observaciones': '', 'guardada': False},
            ),
            **_campos_orden_alumno_usuario(usuario),
        })

    total_registrados = len(asistencias_existentes)
    hoy = timezone.localdate()
    captura_permitida = fecha_obj <= hoy

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
        'alumnos': _ordenar_lista_alumnos_dict(alumnos),
        'lista_guardada': total_registrados > 0,
        'total_registrados': total_registrados,
        'total_alumnos': len(alumnos),
        'captura_permitida': captura_permitida,
    }


def _validar_fecha_captura_asistencia(fecha_obj: date, **_) -> str | None:
    if fecha_obj > timezone.localdate():
        return 'No puedes registrar asistencia en fechas futuras.'
    return None


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

    error_fecha = _validar_fecha_captura_asistencia(
        fecha_obj,
        horario_id=int(horario_id),
        unidad=unidad,
    )
    if error_fecha:
        return JsonResponse({'success': False, 'error': error_fecha}, status=400)

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

    contexto_ciclo = _contexto_dashboard_ciclo()
    context = {
        'perfil': _perfil_administrativo(request),
        **contexto_ciclo,
    }

    return render(request, 'administrativo/administrativo.html', context)


def _asignaciones_admin_serializadas() -> list[dict]:
    asignaciones = (
        AsignacionMateria.objects.select_related('id_materia', 'id_grupo', 'id_ciclo_escolar')
        .filter(estatus='Activa')
        .order_by('id_grupo__clave', 'id_materia__nombre')
    )
    return [
        {
            'id': asignacion.id_asignacion_materia,
            'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
            'ciclo_id': asignacion.id_ciclo_escolar_id,
            'grupo': asignacion.id_grupo.clave,
            'grupo_id': asignacion.id_grupo_id,
            'grupo_nombre': asignacion.id_grupo.nombre,
            'materia_id': asignacion.id_materia.id_materia,
            'materia_clave': asignacion.id_materia.clave,
            'materia_nombre': asignacion.id_materia.nombre,
        }
        for asignacion in asignaciones
    ]


def _alumnos_admin_reportes_serializados() -> list[dict]:
    inscripciones = (
        Inscripcion.objects.select_related('id_alumno__id_usuario', 'id_grupo')
        .filter(estatus='Activa')
        .order_by('id_grupo__clave', 'id_alumno__id_usuario__apellido', 'id_alumno__id_usuario__nombre')
    )
    return [
        {
            'id': inscripcion.id_alumno_id,
            'grupo_id': inscripcion.id_grupo_id,
            'grupo': inscripcion.id_grupo.clave,
            'nombre': (
                f'{inscripcion.id_alumno.id_usuario.nombre} '
                f'{inscripcion.id_alumno.id_usuario.apellido}'
            ).strip(),
            'matricula': inscripcion.id_alumno.id_usuario.matricula,
        }
        for inscripcion in inscripciones
    ]


def _parse_filtros_reportes_admin(request) -> tuple[str, str, str, str, str, int | None, bool]:
    reporte_tipo = request.GET.get('tipo', 'asistencias').strip().lower()
    asignacion_id = request.GET.get('asignacion_id', '').strip()
    grupo_id = request.GET.get('grupo_id', '').strip()
    alumno_id = request.GET.get('alumno_id', '').strip()
    ciclo_id = request.GET.get('ciclo_id', '').strip()
    unidad_str = request.GET.get('unidad', '').strip()

    if reporte_tipo not in ('asistencias', 'calificaciones'):
        reporte_tipo = 'asistencias'

    if not unidad_str:
        return reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, None, False

    try:
        return reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, int(unidad_str), True
    except ValueError:
        return reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, None, False


def _query_reportes_admin(request) -> str:
    reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, unidad, valido = (
        _parse_filtros_reportes_admin(request)
    )
    if not valido or unidad is None:
        return ''
    params = {'tipo': reporte_tipo, 'unidad': str(unidad)}
    if asignacion_id:
        params['asignacion_id'] = asignacion_id
    if grupo_id:
        params['grupo_id'] = grupo_id
    if alumno_id:
        params['alumno_id'] = alumno_id
    if ciclo_id:
        params['ciclo_id'] = ciclo_id
    return '?' + urlencode(params)


def _filtros_texto_reportes_admin(
    reporte_tipo: str,
    unidad: int | None,
    asignacion_id: str,
    grupo_id: str,
    alumno_id: str,
    ciclo_id: str,
) -> list[str]:
    filtros = [
        f'Tipo: {"Asistencias" if reporte_tipo == "asistencias" else "Calificaciones"}',
        f'Unidad: {unidad}',
    ]
    if ciclo_id:
        ciclo = CicloEscolar.objects.filter(pk=ciclo_id).first()
        filtros.append(f'Ciclo: {ciclo.nombre_ciclo if ciclo else ciclo_id}')
    if grupo_id:
        grupo = Grupo.objects.filter(pk=grupo_id).first()
        filtros.append(f'Grupo: {grupo.clave if grupo else grupo_id}')
    if alumno_id:
        alumno = Alumnos.objects.select_related('id_usuario').filter(pk=alumno_id).first()
        if alumno:
            filtros.append(
                f'Alumno: {alumno.id_usuario.matricula} - '
                f'{alumno.id_usuario.nombre} {alumno.id_usuario.apellido}'
            )
        else:
            filtros.append(f'Alumno: {alumno_id}')
    etiqueta_asignacion = _etiqueta_asignacion_reporte(asignacion_id)
    if etiqueta_asignacion:
        filtros.append(f'Materia: {etiqueta_asignacion}')
    return filtros


def _obtener_reportes_admin(
    reporte_tipo: str,
    unidad: int,
    *,
    asignacion_id: str = '',
    grupo_id: str = '',
    alumno_id: str = '',
    ciclo_id: str = '',
    limite: int | None = 500,
) -> list[dict]:
    reportes: list[dict] = []

    if reporte_tipo == 'asistencias':
        asistencias_qs = Asistencia.objects.select_related(
            'id_inscripcion__id_alumno__id_usuario',
            'id_horario__id_asignacion_materia__id_materia',
            'id_horario__id_asignacion_materia__id_grupo',
            'id_horario__id_asignacion_materia__id_ciclo_escolar',
            'id_horario__id_asignacion_materia__id_maestro__id_usuario',
        ).filter(unidad=unidad)

        if asignacion_id:
            asistencias_qs = asistencias_qs.filter(id_horario__id_asignacion_materia__pk=asignacion_id)
        if grupo_id:
            asistencias_qs = asistencias_qs.filter(id_horario__id_asignacion_materia__id_grupo_id=grupo_id)
        if ciclo_id:
            asistencias_qs = asistencias_qs.filter(
                id_horario__id_asignacion_materia__id_ciclo_escolar_id=ciclo_id
            )
        if alumno_id:
            asistencias_qs = asistencias_qs.filter(id_inscripcion__id_alumno_id=alumno_id)

        asistencias = list(asistencias_qs)
        asistencias.sort(
            key=lambda asistencia: (
                -asistencia.fecha_asistencia.toordinal(),
                -asistencia.unidad,
                *_clave_orden_primer_apellido_usuario(asistencia.id_inscripcion.id_alumno.id_usuario),
            )
        )
        if limite is not None:
            asistencias = asistencias[:limite]

        for asistencia in asistencias:
            usuario_alumno = asistencia.id_inscripcion.id_alumno.id_usuario
            asignacion = asistencia.id_horario.id_asignacion_materia
            horario = asistencia.id_horario
            horario_texto = (
                f'{horario.dia_semana} '
                f'{horario.hora_inicio.strftime("%H:%M")}-{horario.hora_fin.strftime("%H:%M")}'
            )
            materia_texto = f'{asignacion.id_materia.clave} - {asignacion.id_materia.nombre}'
            reportes.append({
                'tipo': 'asistencia',
                'fecha': asistencia.fecha_asistencia.strftime('%d/%m/%Y'),
                'unidad': asistencia.unidad,
                'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
                'matricula': usuario_alumno.matricula,
                'materia': materia_texto,
                'grupo': asignacion.id_grupo.clave,
                'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
                'horario': horario_texto,
                'detalle': horario_texto,
                'estado': asistencia.estatus,
                'observaciones': asistencia.observaciones or '',
            })
        return reportes

    calificaciones_qs = Calificacion.objects.select_related(
        'id_inscripcion__id_alumno__id_usuario',
        'id_asignacion_materia__id_materia',
        'id_asignacion_materia__id_grupo',
        'id_asignacion_materia__id_ciclo_escolar',
        'id_asignacion_materia__id_maestro__id_usuario',
    ).filter(unidad=unidad)

    if asignacion_id:
        calificaciones_qs = calificaciones_qs.filter(id_asignacion_materia__pk=asignacion_id)
    if grupo_id:
        calificaciones_qs = calificaciones_qs.filter(id_asignacion_materia__id_grupo_id=grupo_id)
    if ciclo_id:
        calificaciones_qs = calificaciones_qs.filter(
            id_asignacion_materia__id_ciclo_escolar_id=ciclo_id
        )
    if alumno_id:
        calificaciones_qs = calificaciones_qs.filter(id_inscripcion__id_alumno_id=alumno_id)

    calificaciones = list(calificaciones_qs)
    calificaciones.sort(
        key=lambda calificacion: (
            -calificacion.fecha_registro.toordinal() if calificacion.fecha_registro else 0,
            -calificacion.unidad,
            *_clave_orden_primer_apellido_usuario(calificacion.id_inscripcion.id_alumno.id_usuario),
        )
    )
    if limite is not None:
        calificaciones = calificaciones[:limite]

    for calificacion in calificaciones:
        usuario_alumno = calificacion.id_inscripcion.id_alumno.id_usuario
        asignacion = calificacion.id_asignacion_materia
        calificacion_texto = _formatear_calificacion_visible(calificacion.calificacion)
        materia_texto = f'{asignacion.id_materia.clave} - {asignacion.id_materia.nombre}'
        reportes.append({
            'tipo': 'calificacion',
            'fecha': calificacion.fecha_registro.strftime('%d/%m/%Y'),
            'unidad': calificacion.unidad,
            'alumno': f'{usuario_alumno.nombre} {usuario_alumno.apellido}',
            'matricula': usuario_alumno.matricula,
            'materia': materia_texto,
            'grupo': asignacion.id_grupo.clave,
            'ciclo': asignacion.id_ciclo_escolar.nombre_ciclo,
            'calificacion': calificacion_texto,
            'detalle': calificacion_texto,
            'estado': 'Calificación',
            'observaciones': calificacion.observaciones or '',
        })

    return reportes


def admin_reportes(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, unidad, consulta_solicitada = (
        _parse_filtros_reportes_admin(request)
    )
    reportes = []
    if consulta_solicitada and unidad is not None:
        reportes = _obtener_reportes_admin(
            reporte_tipo,
            unidad,
            asignacion_id=asignacion_id,
            grupo_id=grupo_id,
            alumno_id=alumno_id,
            ciclo_id=ciclo_id,
            limite=500,
        )

    context = {
        'perfil': _perfil_administrativo(request),
        'reportes': reportes,
        'filtros_aplicados': consulta_solicitada and unidad is not None,
        'reporte_tipo': reporte_tipo,
        'filtro_asignacion_id': asignacion_id,
        'filtro_grupo_id': grupo_id,
        'filtro_alumno_id': alumno_id,
        'filtro_ciclo_id': ciclo_id,
        'filtro_unidad': request.GET.get('unidad', '').strip(),
        'periodo_actual': _periodo_actual(),
        'asignaciones_reportes': _asignaciones_admin_serializadas(),
        'alumnos_reportes': _alumnos_admin_reportes_serializados(),
        'query_reportes': _query_reportes_admin(request),
    }
    return render(request, 'administrativo/ConsultaReportes.html', context)


def exportar_reportes_admin_excel(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, unidad, valido = (
        _parse_filtros_reportes_admin(request)
    )
    if not valido or unidad is None:
        messages.error(request, 'Selecciona una unidad válida para exportar el reporte.')
        return redirect(reverse('admin_reportes') + _query_reportes_admin(request))

    perfil = _perfil_administrativo(request)
    ahora = datetime.now()
    reportes = _obtener_reportes_admin(
        reporte_tipo,
        unidad,
        asignacion_id=asignacion_id,
        grupo_id=grupo_id,
        alumno_id=alumno_id,
        ciclo_id=ciclo_id,
        limite=None,
    )
    output = generar_excel_reportes_maestro(
        reportes,
        reporte_tipo=reporte_tipo,
        maestro_nombre=perfil.get('nombre_completo', ''),
        filtros=_filtros_texto_reportes_admin(
            reporte_tipo, unidad, asignacion_id, grupo_id, alumno_id, ciclo_id
        ),
        ahora=ahora,
        responsable_etiqueta='Administrativo',
    )
    prefijo = 'asistencias' if reporte_tipo == 'asistencias' else 'calificaciones'
    nombre_archivo = f'reporte_admin_{prefijo}_u{unidad}_{ahora.strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def exportar_reportes_admin_pdf(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    reporte_tipo, asignacion_id, grupo_id, alumno_id, ciclo_id, unidad, valido = (
        _parse_filtros_reportes_admin(request)
    )
    if not valido or unidad is None:
        messages.error(request, 'Selecciona una unidad válida para exportar el reporte.')
        return redirect(reverse('admin_reportes') + _query_reportes_admin(request))

    perfil = _perfil_administrativo(request)
    ahora = datetime.now()
    reportes = _obtener_reportes_admin(
        reporte_tipo,
        unidad,
        asignacion_id=asignacion_id,
        grupo_id=grupo_id,
        alumno_id=alumno_id,
        ciclo_id=ciclo_id,
        limite=None,
    )
    pdf_bytes = generar_pdf_reportes_maestro(
        reportes,
        reporte_tipo=reporte_tipo,
        maestro_nombre=perfil.get('nombre_completo', ''),
        filtros=_filtros_texto_reportes_admin(
            reporte_tipo, unidad, asignacion_id, grupo_id, alumno_id, ciclo_id
        ),
        ahora=ahora,
        responsable_etiqueta='Administrativo',
    )
    prefijo = 'asistencias' if reporte_tipo == 'asistencias' else 'calificaciones'
    nombre_archivo = f'reporte_admin_{prefijo}_u{unidad}_{ahora.strftime("%Y%m%d_%H%M%S")}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


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

    exportar_horarios_pdf_url = ''
    if ciclo_filtro and grupo_filtro:
        exportar_horarios_pdf_url = (
            reverse('exportar_horarios_pdf')
            + '?'
            + urlencode({'ciclo': ciclo_filtro, 'grupo': grupo_filtro})
        )

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
        'exportar_horarios_pdf_url': exportar_horarios_pdf_url,
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


def exportar_horarios_pdf(request):
    """Genera el horario semanal del grupo en PDF desde el servidor."""
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    ciclo = request.GET.get('ciclo', '').strip()
    grupo = request.GET.get('grupo', '').strip()
    if not ciclo or not grupo:
        messages.error(request, 'Selecciona un ciclo y un grupo para exportar el horario.')
        return redirect('admin_horarios')

    datos = obtener_datos_horario_pdf(ciclo, grupo)
    if datos is None:
        messages.error(request, 'No se encontró el grupo indicado.')
        return redirect(f"{reverse('admin_horarios')}?{urlencode({'ciclo': ciclo, 'grupo': grupo})}")

    perfil = _perfil_administrativo(request)
    ahora = datetime.now()
    pdf_bytes = generar_pdf_horario_grupo(
        datos,
        ahora=ahora,
        generado_por=perfil.get('nombre_completo', ''),
    )

    ciclo_slug = re.sub(r'[^\w\-]+', '_', ciclo, flags=re.UNICODE).strip('_') or 'ciclo'
    grupo_slug = re.sub(r'[^\w\-]+', '_', datos['grupo_clave'], flags=re.UNICODE).strip('_') or 'grupo'
    nombre_archivo = f'carga_academica_{grupo_slug}_{ciclo_slug}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def exportar_materias_pdf(request):
    """Genera el catálogo de materias en PDF desde el servidor."""
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    perfil = _perfil_administrativo(request)
    materias = _materias_catalogo_admin()
    ahora = datetime.now()

    pdf_bytes = generar_pdf_catalogo_materias(
        materias,
        ahora=ahora,
        generado_por=perfil.get('nombre_completo', ''),
    )

    nombre_archivo = f'catalogo_materias_{ahora.strftime("%Y%m%d_%H%M%S")}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def exportar_materias_excel(request):
    """Genera el catálogo de materias en Excel desde el servidor."""
    if not sesion_roles_permitidas(request, ('administrativo',)):
        return redirect('selector_rol')

    perfil = _perfil_administrativo(request)
    materias = _materias_catalogo_admin()
    ahora = datetime.now()

    output = generar_excel_catalogo_materias(
        materias,
        ahora=ahora,
        generado_por=perfil.get('nombre_completo', ''),
    )

    nombre_archivo = f'catalogo_materias_{ahora.strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


def crear_materia(request):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'No autorizado'}, status=403)
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('admin_materias')

    es_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    codigo = request.POST.get('codigo', '').strip()
    nombre = request.POST.get('nombre', '').strip()
    semestre = request.POST.get('semestre', '1').strip()
    creditos = request.POST.get('creditos', '0').strip()
    activa = request.POST.get('activa') == 'on'

    if not codigo or not nombre:
        error_msg = 'El código y el nombre de la materia son obligatorios'
        if es_ajax:
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)
        return redirect(f"{reverse('admin_materias')}?abrir_crear=1")

    if Materia.objects.filter(clave__iexact=codigo).exists():
        error_msg = 'Ya existe una materia con ese código'
        if es_ajax:
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)
        return redirect(f"{reverse('admin_materias')}?abrir_crear=1")

    try:
        Materia.objects.create(
            clave=codigo,
            nombre=nombre,
            semestre=int(semestre) if semestre.isdigit() else 1,
            creditos=int(creditos) if creditos.isdigit() else 0,
            activo=activa,
        )
    except Exception as e:
        error_msg = f'Error al crear la materia: {str(e)}'
        if es_ajax:
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)
        return redirect(f"{reverse('admin_materias')}?abrir_crear=1")

    success_msg = 'Materia creada correctamente'
    if es_ajax:
        return JsonResponse({'success': True, 'message': success_msg})
    messages.success(request, success_msg)
    return redirect('admin_materias')


def editar_materia(request, materia_id):
    if not sesion_roles_permitidas(request, ('administrativo',)):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'No autorizado'}, status=403)
        return redirect('selector_rol')

    if request.method != 'POST':
        return redirect('admin_materias')

    es_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    materia = get_object_or_404(Materia, pk=materia_id)
    codigo = request.POST.get('codigo', '').strip()
    nombre = request.POST.get('nombre', '').strip()
    semestre = request.POST.get('semestre', '1').strip()
    creditos = request.POST.get('creditos', '0').strip()
    activa = request.POST.get('activa') == 'on'

    if not codigo or not nombre:
        error_msg = 'El código y el nombre de la materia son obligatorios'
        if es_ajax:
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)
        return redirect('admin_materias')

    materia.nombre = nombre
    materia.semestre = int(semestre) if semestre.isdigit() else 1
    materia.creditos = int(creditos) if creditos.isdigit() else 0
    materia.activo = activa

    try:
        materia.save()
    except Exception as e:
        error_msg = f'Error al actualizar la materia: {str(e)}'
        if es_ajax:
            return JsonResponse({'success': False, 'error': error_msg})
        messages.error(request, error_msg)
        return redirect('admin_materias')

    success_msg = 'Materia actualizada correctamente'
    if es_ajax:
        return JsonResponse({'success': True, 'message': success_msg})
    messages.success(request, success_msg)
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
        grupo = Grupo.objects.filter(clave__iexact=grupo_clave).first()
        if not grupo:
            return JsonResponse({'success': False, 'error': 'Grupo no encontrado'})
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
            h_ini = _normalizar_hora_hm(item.get('hora_inicio', ''))
            h_fin = _normalizar_hora_hm(item.get('hora_fin', ''))

            if not all([dia_txt, h_ini, h_fin]):
                return JsonResponse({'success': False, 'error': 'Cada día seleccionado debe tener hora de inicio y fin'})

            if not _hora_fin_posterior(h_ini, h_fin):
                return JsonResponse({
                    'success': False,
                    'error': _mensaje_horario_invalido(h_ini, h_fin, dia_txt),
                })

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
    hora_inicio = _normalizar_hora_hm(payload.get('hora_inicio', ''))
    hora_fin = _normalizar_hora_hm(payload.get('hora_fin', ''))

    if not horario_id:
        return JsonResponse({'success': False, 'error': 'ID de horario requerido'})

    if not hora_inicio or not hora_fin:
        return JsonResponse({'success': False, 'error': 'Hora de inicio y término son requeridas'})

    if not _hora_fin_posterior(hora_inicio, hora_fin):
        return JsonResponse({
            'success': False,
            'error': _mensaje_horario_invalido(hora_inicio, hora_fin),
        })

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
    
    contexto_ciclo = _contexto_dashboard_ciclo()
    context = {
        'perfil': perfil,
        **contexto_ciclo,
    }

    return render(request, 'administrador/administrador.html', context)

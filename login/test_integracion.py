"""
Pruebas de integración — archivo separado de login/tests.py (unitarias).

Conectan modelo + base de datos y, en algunos casos, vista + sesión + BD.
Se ejecutan con: python manage.py test login.test_integracion -v 2
"""

import json
import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from login.admin_views import _validar_datos_personales_unicos
from login.datos_personales_utils import validar_correo_institucional
from login.models import (
    Alumnos,
    AsignacionMateria,
    Asistencia,
    Calificacion,
    Carrera,
    CicloEscolar,
    DatosPersonales,
    Grupo,
    Horario,
    Inscripcion,
    LogCalificacion,
    Materia,
    Maestros,
    Usuarios,
)
from login.views import _registrar_intento_fallido


def _crear_carrera(sufijo: str | None = None) -> Carrera:
    sufijo = sufijo or uuid.uuid4().hex[:8]
    return Carrera.objects.create(
        nombre=f'Ingenieria Integracion {sufijo}',
        clave=f'ING{sufijo}'[:10],
    )


def _crear_alumno(
    *,
    contrasena: str = 'MiClaveSegura1!',
    contrasena_temporal: bool = False,
    carrera: Carrera | None = None,
    nombre: str = 'Pedro',
    apellido: str = 'Garcia',
) -> Usuarios:
    carrera = carrera or _crear_carrera()
    usuario = Usuarios(
        nombre=nombre,
        apellido=apellido,
        rol='alumno',
        contrasena=contrasena,
        contrasena_temporal=contrasena_temporal,
    )
    usuario.save()
    Alumnos.objects.create(
        id_usuario=usuario,
        id_carrera=carrera,
        semestre=1,
        periodo_ingreso='2026-A',
        estatus='Activo',
    )
    return usuario


def _crear_maestro(
    *,
    nombre: str = 'Luis',
    apellido: str = 'Ramirez',
    contrasena: str = 'MiClaveSegura1!',
) -> tuple[Usuarios, Maestros]:
    usuario = Usuarios(
        nombre=nombre,
        apellido=apellido,
        rol='maestro',
        contrasena=contrasena,
    )
    usuario.save()
    maestro = Maestros.objects.create(
        id_usuario=usuario,
        departamento='Sistemas',
        grado_academico='Licenciatura',
    )
    return usuario, maestro


def _crear_escenario_docente() -> SimpleNamespace:
    """Arma ciclo, grupo, materia, maestro, alumno inscrito, asignación y horario."""
    sufijo = uuid.uuid4().hex[:6]
    carrera = _crear_carrera(sufijo)
    ciclo = CicloEscolar.objects.create(
        fecha_inicio=date(2026, 1, 15),
        fecha_fin=date(2026, 6, 30),
        periodo='A',
    )
    grupo = Grupo.objects.create(
        clave=f'G{sufijo}'[:20],
        nombre=f'Grupo {sufijo}',
        semestre=1,
        turno='Matutino',
        id_carrera=carrera,
        id_ciclo_escolar=ciclo,
    )
    materia = Materia.objects.create(
        clave=f'M{sufijo}'[:20],
        nombre=f'Materia {sufijo}',
        id_carrera=carrera,
        creditos=3,
        semestre=1,
    )
    alumno_usuario = _crear_alumno(carrera=carrera, nombre='Ana', apellido='Perez')
    alumno = Alumnos.objects.get(id_usuario=alumno_usuario)
    maestro_usuario, maestro = _crear_maestro()
    inscripcion = Inscripcion.objects.create(
        id_alumno=alumno,
        id_grupo=grupo,
        id_ciclo_escolar=ciclo,
        estatus='Activa',
    )
    asignacion = AsignacionMateria.objects.create(
        id_materia=materia,
        id_maestro=maestro,
        id_grupo=grupo,
        id_ciclo_escolar=ciclo,
        estatus='Activa',
    )
    horario = Horario.objects.create(
        id_asignacion_materia=asignacion,
        dia_semana='Lunes',
        hora_inicio=time(8, 0),
        hora_fin=time(10, 0),
        estatus='Activo',
    )
    return SimpleNamespace(
        carrera=carrera,
        ciclo=ciclo,
        grupo=grupo,
        materia=materia,
        alumno_usuario=alumno_usuario,
        alumno=alumno,
        maestro_usuario=maestro_usuario,
        maestro=maestro,
        inscripcion=inscripcion,
        asignacion=asignacion,
        horario=horario,
    )


def _sesion_maestro(client: Client, maestro_usuario: Usuarios, maestro: Maestros) -> None:
    session = client.session
    session['usuario_id'] = str(maestro_usuario.id_usuario)
    session['usuario_rol'] = 'maestro'
    session['maestro_id'] = str(maestro.id_usuario_id)
    session.save()


# =============================================================================
# INTEGRACIÓN: Modelo Usuarios + base de datos
# =============================================================================

class UsuariosModeloIntegracionTests(TestCase):
    def test_save_genera_matricula_y_hashea_contrasena(self):
        usuario = Usuarios(
            nombre='Ana',
            apellido='Lopez',
            rol='maestro',
            contrasena='Temporal123!',
        )
        usuario.save()

        año = timezone.now().year
        self.assertTrue(usuario.matricula.startswith(f'EMP-{año}'))
        self.assertNotEqual(usuario.contrasena, 'Temporal123!')
        self.assertTrue(usuario.verificar_contrasena('Temporal123!'))
        self.assertTrue(Usuarios.objects.filter(pk=usuario.pk).exists())

    def test_verificar_contrasena_despues_de_guardar(self):
        usuario = _crear_alumno(contrasena='MiClaveSegura1!')
        usuario.refresh_from_db()

        self.assertTrue(usuario.verificar_contrasena('MiClaveSegura1!'))
        self.assertFalse(usuario.verificar_contrasena('ClaveIncorrecta1!'))


# =============================================================================
# INTEGRACIÓN: DatosPersonales + Usuarios + base de datos
# =============================================================================

class DatosPersonalesModeloIntegracionTests(TestCase):
    def test_guardar_datos_personales_vinculados_a_usuario(self):
        usuario = _crear_alumno()
        datos = DatosPersonales(
            id_usuario=usuario,
            telefono='5512345678',
            correo_inst='alumno.integracion@escuela.edu.mx',
            curp='GOMC920314HMCRRR08',
            fecha_nacimiento=date(2000, 5, 15),
            genero='M',
        )
        datos.save()

        guardado = DatosPersonales.objects.get(pk=usuario.pk)
        self.assertEqual(guardado.telefono, '5512345678')
        self.assertEqual(guardado.correo_inst, 'alumno.integracion@escuela.edu.mx')

    def test_validar_datos_personales_unicos_detecta_correo_duplicado(self):
        carrera = _crear_carrera()
        usuario1 = _crear_alumno(nombre='Luis', apellido='Ruiz', carrera=carrera)
        DatosPersonales.objects.create(
            id_usuario=usuario1,
            correo_inst='duplicado@escuela.edu.mx',
            fecha_nacimiento=date(1999, 3, 10),
        )

        usuario2 = _crear_alumno(nombre='Mario', apellido='Diaz', carrera=carrera)
        error = _validar_datos_personales_unicos(
            'duplicado@escuela.edu.mx',
            '',
            usuario2.id_usuario,
        )
        self.assertEqual(error, 'El correo ya está registrado.')

    def test_validar_datos_personales_unicos_detecta_curp_duplicada(self):
        carrera = _crear_carrera()
        usuario1 = _crear_alumno(nombre='Sara', apellido='Mendez', carrera=carrera)
        DatosPersonales.objects.create(
            id_usuario=usuario1,
            curp='GOMC920314HMCRRR08',
            fecha_nacimiento=date(1998, 8, 20),
        )

        usuario2 = _crear_alumno(nombre='Elena', apellido='Vega', carrera=carrera)
        error = _validar_datos_personales_unicos(
            '',
            'GOMC920314HMCRRR08',
            usuario2.id_usuario,
        )
        self.assertEqual(error, 'La CURP ya está registrada.')

    def test_validar_correo_institucional_con_bd_detecta_duplicado(self):
        carrera = _crear_carrera()
        usuario = _crear_alumno(nombre='Jorge', apellido='Nunez', carrera=carrera)
        DatosPersonales.objects.create(
            id_usuario=usuario,
            correo_inst='repetido@escuela.edu.mx',
            fecha_nacimiento=date(1997, 1, 1),
        )

        otro = _crear_alumno(nombre='Rosa', apellido='Luna', carrera=carrera)
        error = validar_correo_institucional('repetido@escuela.edu.mx', otro.id_usuario)
        self.assertEqual(
            error,
            'Este correo electrónico ya está registrado en el sistema.',
        )


# =============================================================================
# INTEGRACIÓN: Login alumno — vista + modelo + sesión + BD
# =============================================================================

class LoginAlumnoIntegracionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.usuario = _crear_alumno()

    def test_login_exitoso_crea_sesion_y_redirige(self):
        response = self.client.post(
            reverse('login_alumno'),
            {'usuario': self.usuario.matricula, 'contrasena': 'MiClaveSegura1!'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard_alumno'))
        self.assertEqual(self.client.session['usuario_rol'], 'alumno')
        self.assertEqual(self.client.session['usuario_matricula'], self.usuario.matricula)

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.intentos_fallidos_login, 0)
        self.assertIsNotNone(self.usuario.ultimo_acceso)

    def test_login_con_contrasena_incorrecta_incrementa_intentos(self):
        self.client.post(
            reverse('login_alumno'),
            {'usuario': self.usuario.matricula, 'contrasena': 'ClaveIncorrecta1!'},
        )

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.intentos_fallidos_login, 1)
        self.assertFalse(self.usuario.cuenta_bloqueada)

    def test_cinco_intentos_fallidos_bloquean_cuenta_en_bd(self):
        for _ in range(5):
            _registrar_intento_fallido(self.usuario)
            self.usuario.refresh_from_db()

        self.assertTrue(self.usuario.cuenta_bloqueada)
        self.assertIsNotNone(self.usuario.fecha_bloqueo)


# =============================================================================
# INTEGRACIÓN: Cambiar contraseña temporal — vista + validación + BD
# =============================================================================

class CambiarContrasenaIntegracionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.usuario = _crear_alumno(contrasena_temporal=True)

    def _iniciar_sesion(self):
        session = self.client.session
        session['usuario_id'] = str(self.usuario.id_usuario)
        session['usuario_rol'] = 'alumno'
        session['usuario_matricula'] = self.usuario.matricula
        session.save()

    def test_cambio_contrasena_temporal_actualiza_bd(self):
        self._iniciar_sesion()
        response = self.client.post(
            reverse('cambiar_contrasena_temporal'),
            {
                'nueva_contrasena': 'NuevaClaveSegura2!',
                'confirmar_contrasena': 'NuevaClaveSegura2!',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard_alumno'))

        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.contrasena_temporal)
        self.assertTrue(self.usuario.verificar_contrasena('NuevaClaveSegura2!'))

    def test_cambio_con_contrasena_invalida_no_modifica_bd(self):
        self._iniciar_sesion()
        hash_anterior = self.usuario.contrasena

        response = self.client.post(
            reverse('cambiar_contrasena_temporal'),
            {
                'nueva_contrasena': 'corta',
                'confirmar_contrasena': 'corta',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.contrasena_temporal)
        self.assertEqual(self.usuario.contrasena, hash_anterior)


# =============================================================================
# INTEGRACIÓN: Calificaciones — vista maestro + BD (calificacion, log_calificacion)
# Vista: login/views.py → guardar_calificaciones_maestro
# =============================================================================

class CalificacionesIntegracionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.escenario = _crear_escenario_docente()
        _sesion_maestro(
            self.client,
            self.escenario.maestro_usuario,
            self.escenario.maestro,
        )

    def test_guardar_calificacion_via_vista_maestro(self):
        payload = {
            'asignacion_id': self.escenario.asignacion.id_asignacion_materia,
            'registros': [
                {
                    'id_inscripcion': self.escenario.inscripcion.id_inscripcion,
                    'unidad': 1,
                    'calificacion': '85.50',
                    'observaciones': 'Parcial 1',
                },
            ],
        }
        response = self.client.post(
            reverse('guardar_calificaciones_maestro'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['guardadas'], 1)

        calificacion = Calificacion.objects.get(
            id_inscripcion=self.escenario.inscripcion,
            id_asignacion_materia=self.escenario.asignacion,
            unidad=1,
        )
        self.assertEqual(calificacion.calificacion, Decimal('85.50'))
        self.assertEqual(calificacion.observaciones, 'Parcial 1')
        self.assertTrue(
            LogCalificacion.objects.filter(
                id_calificacion=calificacion,
                accion='crear',
                valor_nuevo=Decimal('85.50'),
            ).exists()
        )

    def test_calificacion_fuera_de_rango_no_se_guarda(self):
        payload = {
            'asignacion_id': self.escenario.asignacion.id_asignacion_materia,
            'registros': [
                {
                    'id_inscripcion': self.escenario.inscripcion.id_inscripcion,
                    'unidad': 1,
                    'calificacion': '150',
                },
            ],
        }
        response = self.client.post(
            reverse('guardar_calificaciones_maestro'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertEqual(Calificacion.objects.count(), 0)


# =============================================================================
# INTEGRACIÓN: Asistencias — vista maestro + BD (asistencia)
# Vista: login/views.py → guardar_asistencia_maestro
# =============================================================================

class AsistenciaIntegracionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.escenario = _crear_escenario_docente()
        _sesion_maestro(
            self.client,
            self.escenario.maestro_usuario,
            self.escenario.maestro,
        )
        self.fecha_hoy = timezone.localdate().isoformat()

    def test_guardar_asistencia_via_vista_maestro(self):
        payload = {
            'asignacion_id': self.escenario.asignacion.id_asignacion_materia,
            'horario_id': self.escenario.horario.id_horario,
            'fecha': self.fecha_hoy,
            'unidad': 1,
            'registros': [
                {
                    'id_inscripcion': self.escenario.inscripcion.id_inscripcion,
                    'estatus': 'Presente',
                    'observaciones': 'Asistió puntual',
                },
            ],
        }
        response = self.client.post(
            reverse('guardar_asistencia_maestro'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['guardadas'], 1)

        asistencia = Asistencia.objects.get(
            id_inscripcion=self.escenario.inscripcion,
            id_horario=self.escenario.horario,
            fecha_asistencia=timezone.localdate(),
            unidad=1,
        )
        self.assertEqual(asistencia.estatus, 'Presente')
        self.assertEqual(asistencia.observaciones, 'Asistió puntual')

    def test_asistencia_fecha_futura_rechazada(self):
        fecha_futura = (timezone.localdate() + timedelta(days=5)).isoformat()
        payload = {
            'asignacion_id': self.escenario.asignacion.id_asignacion_materia,
            'horario_id': self.escenario.horario.id_horario,
            'fecha': fecha_futura,
            'unidad': 1,
            'registros': [
                {
                    'id_inscripcion': self.escenario.inscripcion.id_inscripcion,
                    'estatus': 'Presente',
                },
            ],
        }
        response = self.client.post(
            reverse('guardar_asistencia_maestro'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('futuras', response.json()['error'])
        self.assertEqual(Asistencia.objects.count(), 0)

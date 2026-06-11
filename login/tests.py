from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from login.admin_views import (
    _normalizar_texto,
    _parse_fecha_nacimiento,
    _validar_nombre_carrera_unico,
    construir_direccion,
    desglosar_direccion,
)
from login.datos_personales_utils import (
    validar_correo_institucional,
    validar_cp,
    validar_datos_contacto_perfil,
    validar_datos_perfil_usuario,
    validar_telefono,
)
from login.models import DatosPersonales, Usuarios
from login.password_utils import (
    LONGITUD_MINIMA_USUARIO,
    _AMBIGUOS,
    _SIMBOLOS_SEGUROS,
    generar_contrasena_temporal,
    validar_contrasena_usuario,
)
from login.periodo_utils import (
    parse_periodo,
    periodo_desde_mes,
    validar_periodo_coherente_con_fecha,
)


# =============================================================================
# PANTALLA: Cambiar contraseña / recuperación de contraseña
# Archivo probado: login/password_utils.py
# =============================================================================

class GenerarContrasenaTemporalTests(SimpleTestCase):
    def test_longitud_por_defecto_es_16(self):
        contrasena = generar_contrasena_temporal()
        self.assertEqual(len(contrasena), 16)

    def test_respeta_longitud_minima_de_12(self):
        contrasena = generar_contrasena_temporal(longitud=8)
        self.assertEqual(len(contrasena), 12)

    def test_incluye_mayusculas_minusculas_digitos_y_simbolos(self):
        contrasena = generar_contrasena_temporal()
        self.assertRegex(contrasena, r'[A-Z]')
        self.assertRegex(contrasena, r'[a-z]')
        self.assertRegex(contrasena, r'\d')
        self.assertTrue(any(c in _SIMBOLOS_SEGUROS for c in contrasena))

    def test_no_usa_caracteres_ambiguos(self):
        for _ in range(20):
            contrasena = generar_contrasena_temporal()
            self.assertFalse(any(c in _AMBIGUOS for c in contrasena))


class ValidarContrasenaUsuarioTests(SimpleTestCase):
    def test_contrasena_vacia_falla(self):
        ok, errores = validar_contrasena_usuario('')
        self.assertFalse(ok)
        self.assertEqual(errores, ['La contraseña no puede estar vacía.'])

    def test_contrasena_corta_falla(self):
        ok, errores = validar_contrasena_usuario('Abc1!')
        self.assertFalse(ok)
        self.assertTrue(any('12 caracteres' in e for e in errores))

    def test_contrasena_sin_mayuscula_falla(self):
        ok, errores = validar_contrasena_usuario('minusculas123!')
        self.assertFalse(ok)
        self.assertIn('Debe incluir al menos una letra mayúscula.', errores)

    def test_contrasena_sin_minuscula_falla(self):
        ok, errores = validar_contrasena_usuario('MAYUSCULAS123!')
        self.assertFalse(ok)
        self.assertIn('Debe incluir al menos una letra minúscula.', errores)

    def test_contrasena_sin_numero_falla(self):
        ok, errores = validar_contrasena_usuario('SinNumerosAqui!')
        self.assertFalse(ok)
        self.assertIn('Debe incluir al menos un número.', errores)

    def test_contrasena_sin_simbolo_falla(self):
        ok, errores = validar_contrasena_usuario('SinSimbolos123')
        self.assertFalse(ok)
        self.assertIn('Debe incluir al menos un símbolo (!@#$%&*+-=?).', errores)

    def test_contrasena_comun_falla(self):
        ok, errores = validar_contrasena_usuario('password1234')
        self.assertFalse(ok)
        self.assertIn('Esa contraseña es demasiado común. Elige otra.', errores)

    def test_contrasena_valida_pasa(self):
        ok, errores = validar_contrasena_usuario('MiClaveSegura1!')
        self.assertTrue(ok)
        self.assertEqual(errores, [])

    def test_no_puede_contener_nombre_del_usuario(self):
        usuario = SimpleNamespace(nombre='Carlos', apellido='Lopez', matricula='20260001')
        ok, errores = validar_contrasena_usuario('MiClaveCarlos1!', usuario)
        self.assertFalse(ok)
        self.assertIn('No puede contener tu nombre, apellido o matrícula.', errores)

    def test_no_puede_ser_igual_a_la_actual(self):
        usuario = SimpleNamespace(
            nombre='Ana',
            apellido='Ruiz',
            matricula='20260002',
            contrasena='hash',
            verificar_contrasena=lambda c: c == 'MiClaveActual1!',
        )
        ok, errores = validar_contrasena_usuario('MiClaveActual1!', usuario)
        self.assertFalse(ok)
        self.assertIn('La nueva contraseña debe ser distinta a la actual.', errores)

    def test_contrasena_muy_larga_falla(self):
        contrasena = 'Aa1!' + ('x' * (LONGITUD_MINIMA_USUARIO + 120))
        ok, errores = validar_contrasena_usuario(contrasena)
        self.assertFalse(ok)
        self.assertTrue(any('128 caracteres' in e for e in errores))


# =============================================================================
# PANTALLA: Perfil alumno / Perfil maestro (datos de contacto)
# Archivo probado: login/datos_personales_utils.py
# Usado en: login/views.py (guardar perfil)
# =============================================================================

class ValidarTelefonoTests(SimpleTestCase):
    def test_telefono_vacio_no_genera_error(self):
        self.assertIsNone(validar_telefono(''))

    def test_telefono_con_letras_falla(self):
        error = validar_telefono('55abc12345')
        self.assertEqual(error, 'El teléfono debe contener solo números (sin guiones ni espacios)')

    def test_telefono_corto_falla(self):
        error = validar_telefono('5512345')
        self.assertEqual(error, 'El teléfono debe tener exactamente 10 dígitos')

    def test_telefono_largo_falla(self):
        error = validar_telefono('551234567890')
        self.assertEqual(error, 'El teléfono debe tener exactamente 10 dígitos')

    def test_telefono_valido_con_guiones_pasa(self):
        self.assertIsNone(validar_telefono('55-1234-5678'))

    def test_telefono_valido_10_digitos_pasa(self):
        self.assertIsNone(validar_telefono('5512345678'))


class ValidarCorreoInstitucionalTests(SimpleTestCase):
    def test_correo_vacio_no_genera_error(self):
        self.assertIsNone(validar_correo_institucional(''))

    def test_correo_sin_arroba_falla(self):
        error = validar_correo_institucional('correo-invalido')
        self.assertEqual(error, 'El formato del correo no es válido.')

    def test_correo_sin_dominio_falla(self):
        error = validar_correo_institucional('alumno@')
        self.assertEqual(error, 'El formato del correo no es válido.')

    @patch('login.datos_personales_utils.DatosPersonales.objects')
    def test_correo_valido_no_duplicado_pasa(self, mock_objects):
        mock_objects.filter.return_value.exists.return_value = False
        self.assertIsNone(validar_correo_institucional('alumno@escuela.edu.mx'))

    @patch('login.datos_personales_utils.DatosPersonales.objects')
    def test_correo_duplicado_falla(self, mock_objects):
        mock_objects.filter.return_value.exists.return_value = True
        error = validar_correo_institucional('repetido@escuela.edu.mx')
        self.assertEqual(error, 'Este correo electrónico ya está registrado en el sistema.')


class ValidarCodigoPostalTests(SimpleTestCase):
    def test_cp_vacio_opcional_no_genera_error(self):
        self.assertIsNone(validar_cp(''))

    def test_cp_vacio_obligatorio_falla(self):
        error = validar_cp('', requerido=True)
        self.assertEqual(error, 'Este campo es obligatorio')

    def test_cp_con_letras_falla(self):
        error = validar_cp('12A45')
        self.assertEqual(error, 'El código postal debe contener solo números')

    def test_cp_corto_falla(self):
        error = validar_cp('1234')
        self.assertEqual(error, 'El código postal debe tener exactamente 5 dígitos')

    def test_cp_valido_pasa(self):
        self.assertIsNone(validar_cp('58000'))


class ValidarDatosPerfilUsuarioTests(SimpleTestCase):
    @patch('login.datos_personales_utils.DatosPersonales.objects')
    def test_perfil_con_telefono_y_correo_invalidos(self, mock_objects):
        mock_objects.filter.return_value.exclude.return_value.exists.return_value = False
        errores = validar_datos_perfil_usuario(
            correo='mal-correo',
            telefono='123',
            cp='99',
            usuario_id=1,
        )
        self.assertIn('telefono', errores)
        self.assertIn('correo_institucional', errores)
        self.assertIn('cp', errores)

    @patch('login.datos_personales_utils.DatosPersonales.objects')
    def test_perfil_valido_sin_errores(self, mock_objects):
        mock_objects.filter.return_value.exclude.return_value.exists.return_value = False
        errores = validar_datos_perfil_usuario(
            correo='alumno@escuela.edu.mx',
            telefono='5512345678',
            cp='58000',
            usuario_id=1,
        )
        self.assertEqual(errores, {})

    @patch('login.datos_personales_utils.DatosPersonales.objects')
    def test_contacto_perfil_solo_telefono_invalido(self, mock_objects):
        mock_objects.filter.return_value.exclude.return_value.exists.return_value = False
        errores = validar_datos_contacto_perfil(
            correo='alumno@escuela.edu.mx',
            telefono='abc',
            usuario_id=1,
        )
        self.assertEqual(list(errores.keys()), ['telefono'])


# =============================================================================
# PANTALLA: Agregar usuario / Editar usuario (administrador)
# Archivos probados: login/models.py, login/admin_views.py
# =============================================================================

class ValidarNombreApellidoUsuarioTests(SimpleTestCase):
    def test_nombre_con_numeros_falla(self):
        usuario = Usuarios(nombre='Juan123', apellido='Perez')
        with self.assertRaises(ValidationError) as ctx:
            usuario.clean()
        self.assertIn('nombre', ctx.exception.message_dict)

    def test_apellido_con_simbolos_falla(self):
        usuario = Usuarios(nombre='Juan', apellido='Perez@')
        with self.assertRaises(ValidationError) as ctx:
            usuario.clean()
        self.assertIn('apellido', ctx.exception.message_dict)

    def test_nombre_y_apellido_validos_pasan(self):
        usuario = Usuarios(nombre='María', apellido='García López')
        usuario.clean()


class ValidarCurpTests(SimpleTestCase):
    def test_curp_corta_falla(self):
        datos = DatosPersonales(curp='ROAJ850102')
        with self.assertRaises(ValidationError) as ctx:
            datos.clean()
        self.assertIn('curp', ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict['curp'][0],
            'La CURP debe tener exactamente 18 caracteres',
        )

    def test_curp_formato_invalido_falla(self):
        datos = DatosPersonales(curp='123456789012345678')
        with self.assertRaises(ValidationError) as ctx:
            datos.clean()
        self.assertEqual(
            ctx.exception.message_dict['curp'][0],
            'La CURP tiene un formato inválido',
        )

    def test_curp_valida_pasa(self):
        datos = DatosPersonales(curp='GOMC920314HMCRRR08')
        datos.clean()


class ValidarFechaNacimientoTests(SimpleTestCase):
    def test_fecha_futura_falla(self):
        datos = DatosPersonales(fecha_nacimiento=date(2099, 1, 1))
        with self.assertRaises(ValidationError) as ctx:
            datos.clean()
        self.assertEqual(
            ctx.exception.message_dict['fecha_nacimiento'][0],
            'La fecha de nacimiento no puede ser futura',
        )

    def test_fecha_muy_antigua_falla(self):
        datos = DatosPersonales(fecha_nacimiento=date(1899, 12, 31))
        with self.assertRaises(ValidationError) as ctx:
            datos.clean()
        self.assertIn(
            '1900',
            ctx.exception.message_dict['fecha_nacimiento'][0],
        )


class ConstruirDireccionTests(SimpleTestCase):
    def test_direccion_directa_tiene_prioridad(self):
        data = {'direccion': 'Calle Principal 100', 'calle': 'Otra'}
        self.assertEqual(construir_direccion(data), 'Calle Principal 100')

    def test_une_campos_separados(self):
        data = {
            'calle': 'Av. Universidad',
            'numero_exterior': '1200',
            'colonia': 'Centro',
            'municipio': 'Morelia',
            'estado': 'Michoacán',
            'cp': '58000',
        }
        direccion = construir_direccion(data)
        self.assertEqual(
            direccion,
            'Av. Universidad, 1200, Centro, Morelia, Michoacán, 58000',
        )

    def test_sin_datos_devuelve_none(self):
        self.assertIsNone(construir_direccion({}))


class DesglosarDireccionTests(SimpleTestCase):
    def test_desglosa_texto_guardado(self):
        texto = 'Av. Universidad, 1200, , Centro, Morelia, Michoacán, 58000'
        partes = desglosar_direccion(texto)
        self.assertEqual(partes['calle'], 'Av. Universidad')
        self.assertEqual(partes['cp'], '58000')

    def test_direccion_vacia_devuelve_campos_vacios(self):
        partes = desglosar_direccion(None)
        self.assertEqual(partes['calle'], '')
        self.assertEqual(partes['cp'], '')


class ParseFechaNacimientoTests(SimpleTestCase):
    def test_valor_vacio_devuelve_none(self):
        self.assertIsNone(_parse_fecha_nacimiento(''))

    def test_fecha_iso_se_parsea(self):
        self.assertEqual(_parse_fecha_nacimiento('2010-05-15'), date(2010, 5, 15))


# =============================================================================
# PANTALLA: Gestión de carreras (administrador)
# Archivo probado: login/admin_views.py
# =============================================================================

class NormalizarTextoTests(SimpleTestCase):
    def test_quita_acentos_y_minusculas(self):
        self.assertEqual(_normalizar_texto('Ingeniería'), 'ingenieria')

    def test_espacios_extra_se_eliminan(self):
        self.assertEqual(_normalizar_texto('  Sistemas  '), 'sistemas')


class ValidarNombreCarreraUnicoTests(SimpleTestCase):
    @patch('login.admin_views.Carrera.objects')
    def test_nombre_duplicado_falla(self, mock_carrera_objects):
        carrera_existente = SimpleNamespace(id=1, nombre='Ingeniería')
        mock_carrera_objects.all.return_value = [carrera_existente]
        error = _validar_nombre_carrera_unico('ingenieria')
        self.assertEqual(error, 'Ya existe una carrera con ese nombre.')

    @patch('login.admin_views.Carrera.objects')
    def test_nombre_nuevo_pasa(self, mock_carrera_objects):
        mock_carrera_objects.all.return_value = []
        self.assertIsNone(_validar_nombre_carrera_unico('Medicina'))


# =============================================================================
# PANTALLA: Ciclos escolares / periodos
# Archivo probado: login/periodo_utils.py
# =============================================================================

class PeriodoUtilsTests(SimpleTestCase):
    def test_enero_es_periodo_a(self):
        self.assertEqual(periodo_desde_mes(1), 'A')

    def test_septiembre_es_periodo_b(self):
        self.assertEqual(periodo_desde_mes(9), 'B')

    def test_parse_periodo_valido(self):
        self.assertEqual(parse_periodo('2026-A'), (2026, 0))

    def test_parse_periodo_invalido(self):
        self.assertIsNone(parse_periodo('2026-Z'))

    def test_periodo_incoherente_con_fecha_lanza_error(self):
        with self.assertRaises(ValueError) as ctx:
            validar_periodo_coherente_con_fecha('B', date(2026, 2, 1))
        self.assertIn('periodo debe ser A', str(ctx.exception))

from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from django.db.models import Max, Q, F, Value
from django.db.models.functions import Lower, Concat, Cast, ExtractYear
from django.core.exceptions import ValidationError
from datetime import date
import re


class Usuarios(models.Model):
    ROL_CHOICES = [
        ('admin', 'Administrador'),
        ('maestro', 'Maestro'),
        ('alumno', 'Alumno'),
        ('administrativo', 'Administrativo'),
    ]
    
    id_usuario = models.AutoField(primary_key=True)
    matricula = models.CharField(max_length=25, unique=True, editable=False)
    nombre = models.CharField(max_length=80)
    apellido = models.CharField(max_length=80)
    contrasena = models.CharField(max_length=255)
    rol = models.CharField(max_length=20, choices=ROL_CHOICES)
    ultimo_acceso = models.DateTimeField(null=True, blank=True)
    contrasena_temporal = models.BooleanField(default=False)
    foto = models.ImageField(upload_to='fotos_perfil/', null=True, blank=True)
    
    # Campos de seguridad para bloqueo de cuentas
    intentos_fallidos_login = models.IntegerField(default=0)
    cuenta_bloqueada = models.BooleanField(default=False)
    fecha_bloqueo = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'usuarios'
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
    
    def clean(self):
        """Valida que nombre y apellido solo contengan letras y espacios"""
        errors = {}
        
        # Validar nombre (solo letras y espacios, acentos permitidos)
        if self.nombre:
            nombre_limpio = self.nombre.strip()
            if not nombre_limpio:
                errors['nombre'] = 'El nombre no puede estar vacío'
            elif not re.match(r'^[A-Za-zÁáÉéÍíÓóÚúÑñ\s]+$', nombre_limpio):
                errors['nombre'] = 'El nombre solo puede contener letras y espacios'
        
        # Validar apellido (solo letras y espacios, acentos permitidos)
        if self.apellido:
            apellido_limpio = self.apellido.strip()
            if not apellido_limpio:
                errors['apellido'] = 'El apellido no puede estar vacío'
            elif not re.match(r'^[A-Za-zÁáÉéÍíÓóÚúÑñ\s]+$', apellido_limpio):
                errors['apellido'] = 'El apellido solo puede contener letras y espacios'
        
        if errors:
            raise ValidationError(errors)
    
    def save(self, *args, **kwargs):
        # Se ejecuta en CREATE (agregar_usuario) y UPDATE (editar_usuario, restablecer_contrasena)
        self.full_clean()  # Ejecuta clean() → valida nombre/apellido
        
        # CREATE: si es registro nuevo (sin pk), genera matrícula automática
        if not self.pk and not self.matricula:
            self.matricula = self.generar_matricula()
        
        # Encripta contraseña si llega en texto plano (make_password de Django)
        if self.contrasena and not self.contrasena.startswith('pbkdf2_sha256$'):
            self.contrasena = make_password(self.contrasena)
        
        super().save(*args, **kwargs)  # INSERT o UPDATE en tabla 'usuarios'
    
    def generar_matricula(self):
        """Genera matrícula automática según rol y año actual — llamado desde save() en CREATE"""
        año_actual = timezone.now().year
        
        # Definir prefijos por rol
        prefijos = {
            'admin': 'ADM-',
            'maestro': 'EMP-',
            'alumno': '',
            'administrativo': 'AD-'
        }
        
        prefijo = prefijos.get(self.rol, '')
        patron = f"{prefijo}{año_actual}"
        
        # Buscar última matrícula del mismo rol y año
        ultima_matricula = Usuarios.objects.filter(
            matricula__startswith=patron
        ).order_by('-matricula').first()
        
        if ultima_matricula:
            # Extraer últimos 4 dígitos y sumar 1
            ultimo_numero = int(ultima_matricula.matricula[-4:])
            nuevo_numero = ultimo_numero + 1
        else:
            nuevo_numero = 1
        
        # Formatear con 4 dígitos
        return f"{patron}{nuevo_numero:04d}"
    
    def verificar_contrasena(self, contrasena_plana):
        """Verifica si la contraseña plana coincide con la encriptada"""
        return check_password(contrasena_plana, self.contrasena)
    
    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.matricula})"


class DatosPersonales(models.Model):
    GENERO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('otro', 'Otro'),
    ]
    
    id_usuario = models.OneToOneField(
        Usuarios, 
        on_delete=models.CASCADE, 
        primary_key=True, 
        db_column='id_usuario'
    )
    curp = models.CharField(max_length=18, null=True, blank=True, db_index=True)
    telefono = models.CharField(max_length=15, null=True, blank=True)
    direccion = models.TextField(null=True, blank=True)
    correo_inst = models.EmailField(null=True, blank=True, db_index=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    genero = models.CharField(max_length=10, choices=GENERO_CHOICES, null=True, blank=True)
    
    class Meta:
        db_table = 'datos_personales'
        verbose_name = 'Dato Personal'
        verbose_name_plural = 'Datos Personales'
        constraints = [
            models.UniqueConstraint(
                Lower('correo_inst'),
                name='uniq_datos_personales_correo_lower',
                condition=Q(correo_inst__isnull=False) & ~Q(correo_inst=''),
                violation_error_message='Este correo electrónico ya está registrado en el sistema.',
            ),
            models.UniqueConstraint(
                Lower('curp'),
                name='uniq_datos_personales_curp_lower',
                condition=Q(curp__isnull=False) & ~Q(curp=''),
                violation_error_message='Esta CURP ya está registrada en el sistema.',
            ),
        ]
    
    def clean(self):
        """Valida el formato de los campos de datos personales"""
        errors = {}
        
        # Validar teléfono (solo números, 10 dígitos para México)
        if self.telefono:
            # Eliminar espacios y guiones
            telefono_limpio = re.sub(r'[\s\-]', '', self.telefono)
            if not telefono_limpio.isdigit():
                errors['telefono'] = 'El teléfono debe contener solo números'
            elif len(telefono_limpio) < 10 or len(telefono_limpio) > 15:
                errors['telefono'] = 'El teléfono debe tener entre 10 y 15 dígitos'
        
        # Validar CURP (formato estándar mexicano: 4 letras, 6 números, 6 caracteres alfanuméricos, 2 caracteres)
        if self.curp:
            curp_upper = self.curp.upper().strip()
            if len(curp_upper) != 18:
                errors['curp'] = 'La CURP debe tener exactamente 18 caracteres'
            elif not re.match(r'^[A-Z]{4}\d{6}[A-Z0-9]{6}[A-Z0-9]{2}$', curp_upper):
                errors['curp'] = 'La CURP tiene un formato inválido'
        
        # Validar fecha de nacimiento (no futura, rango razonable)
        if self.fecha_nacimiento:
            hoy = date.today()
            if self.fecha_nacimiento > hoy:
                errors['fecha_nacimiento'] = 'La fecha de nacimiento no puede ser futura'
            elif self.fecha_nacimiento < date(1900, 1, 1):
                errors['fecha_nacimiento'] = 'La fecha de nacimiento no puede ser anterior a 1900'
            elif (hoy - self.fecha_nacimiento).days < 3650:  # Menos de 10 años
                errors['fecha_nacimiento'] = 'La fecha de nacimiento debe corresponder a una persona de al menos 10 años'
        
        # Validar género (que esté en los choices)
        if self.genero:
            generos_validos = [choice[0] for choice in self.GENERO_CHOICES]
            if self.genero not in generos_validos:
                errors['genero'] = f'El género debe ser uno de: {", ".join(generos_validos)}'
        
        if errors:
            raise ValidationError(errors)
    
    def save(self, *args, **kwargs):
        self.full_clean()  # Ejecuta las validaciones antes de guardar
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"Datos de {self.id_usuario.nombre} {self.id_usuario.apellido}"


class Carrera(models.Model):
    nombre = models.CharField(max_length=100)
    clave = models.CharField(max_length=10, default='')
    
    class Meta:
        db_table = 'carrera'
        verbose_name = 'Carrera'
        verbose_name_plural = 'Carreras'
        constraints = [
            models.UniqueConstraint(
                Lower('nombre'),
                name='uniq_carrera_nombre_lower',
                violation_error_message='Ya existe una carrera con ese nombre.',
            ),
            models.UniqueConstraint(
                Lower('clave'),
                name='uniq_carrera_clave_lower',
                condition=~Q(clave=''),
                violation_error_message='Ya existe una carrera con esa clave',
            ),
        ]
    
    def __str__(self):
        return f"{self.clave} - {self.nombre}"


class Materia(models.Model):
    id_materia = models.AutoField(primary_key=True)
    clave = models.CharField(max_length=20)
    nombre = models.CharField(max_length=100)
    id_carrera = models.ForeignKey(
        Carrera,
        on_delete=models.PROTECT,
        db_column='id_carrera',
        related_name='materias',
    )
    creditos = models.IntegerField(default=0)
    semestre = models.IntegerField(default=1)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'materia'
        verbose_name = 'Materia'
        verbose_name_plural = 'Materias'
        constraints = [
            models.UniqueConstraint(
                Lower('clave'),
                name='uniq_materia_clave_lower',
                violation_error_message='Ya existe una materia con ese código',
            ),
        ]

    def __str__(self):
        return f"{self.clave} - {self.nombre}"


class Alumnos(models.Model):
    ESTATUS_CHOICES = [
        ('Activo', 'Activo'),
        ('Baja', 'Baja'),
        ('Egresado', 'Egresado'),
    ]
    
    id_usuario = models.OneToOneField(
        Usuarios, 
        on_delete=models.CASCADE, 
        primary_key=True, 
        db_column='id_usuario'
    )
    id_carrera = models.ForeignKey(Carrera, on_delete=models.PROTECT, db_column='id_carrera')
    semestre = models.IntegerField()
    periodo_ingreso = models.CharField(max_length=10)  # ej: 2026-1
    estatus = models.CharField(max_length=20, choices=ESTATUS_CHOICES, default='Activo')
    
    class Meta:
        db_table = 'alumnos'
        verbose_name = 'Alumno'
        verbose_name_plural = 'Alumnos'
    
    def __str__(self):
        return f"Alumno: {self.id_usuario.matricula} - {self.id_usuario.nombre} {self.id_usuario.apellido}"


class Maestros(models.Model):
    GRADO_CHOICES = [
        ('Licenciatura', 'Licenciatura'),
        ('Maestria', 'Maestría'),
        ('Doctorado', 'Doctorado'),
    ]
    
    id_usuario = models.OneToOneField(
        Usuarios, 
        on_delete=models.CASCADE, 
        primary_key=True, 
        db_column='id_usuario'
    )
    departamento = models.CharField(max_length=100)
    cubiculo = models.CharField(max_length=50, null=True, blank=True)
    grado_academico = models.CharField(max_length=20, choices=GRADO_CHOICES)
    
    class Meta:
        db_table = 'maestros'
        verbose_name = 'Maestro'
        verbose_name_plural = 'Maestros'
    
    def __str__(self):
        return f"Maestro: {self.id_usuario.matricula} - {self.id_usuario.nombre} {self.id_usuario.apellido}"


class Administrativos(models.Model):
    id_usuario = models.OneToOneField(
        Usuarios, 
        on_delete=models.CASCADE, 
        primary_key=True, 
        db_column='id_usuario'
    )
    departamento = models.CharField(max_length=100)
    puesto = models.CharField(max_length=100)
    
    class Meta:
        db_table = 'administrativos'
        verbose_name = 'Administrativo'
        verbose_name_plural = 'Administrativos'
    
    def __str__(self):
        return f"Administrativo: {self.id_usuario.nombre} {self.id_usuario.apellido}"


class CicloEscolar(models.Model):
    PERIODO_CHOICES = [
        ('A', 'A'),
        ('B', 'B'),
    ]

    id_ciclo_escolar = models.AutoField(primary_key=True)
    nombre_ciclo = models.CharField(max_length=50, editable=False, unique=True)
    periodo = models.CharField(max_length=1, choices=PERIODO_CHOICES, default='A')
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    
    class Meta:
        db_table = 'ciclo_escolar'
        verbose_name = 'Ciclo Escolar'
        verbose_name_plural = 'Ciclos Escolares'
        constraints = [
            models.CheckConstraint(
                condition=Q(periodo__in=['A', 'B']),
                name='ciclo_periodo_valido',
                violation_error_message='Selecciona un periodo válido (A o B)',
            ),
            models.CheckConstraint(
                condition=Q(
                    nombre_ciclo=Concat(
                        Cast(ExtractYear('fecha_inicio'), models.CharField(max_length=4)),
                        Value('-'),
                        F('periodo'),
                        output_field=models.CharField(max_length=50),
                    )
                ),
                name='ciclo_nombre_coherente',
                violation_error_message='El nombre del ciclo debe coincidir con el año de inicio y el periodo',
            ),
            models.CheckConstraint(
                condition=Q(fecha_fin__gt=F('fecha_inicio')),
                name='ciclo_fecha_fin_posterior',
                violation_error_message='La fecha de fin debe ser posterior a la de inicio',
            ),
        ]
    
    def __str__(self):
        return f"Ciclo: {self.nombre_ciclo} ({self.fecha_inicio} - {self.fecha_fin})"

    def save(self, *args, **kwargs):
        if self.fecha_inicio:
            self.nombre_ciclo = f"{self.fecha_inicio.year}-{self.periodo}"
        super().save(*args, **kwargs)


class Grupo(models.Model):
    TURNO_CHOICES = [
        ('Matutino', 'Matutino'),
        ('Vespertino', 'Vespertino'),
        ('Nocturno', 'Nocturno'),
    ]

    id_grupo = models.AutoField(primary_key=True)
    clave = models.CharField(max_length=20)
    nombre = models.CharField(max_length=100)
    semestre = models.IntegerField()
    turno = models.CharField(max_length=20, choices=TURNO_CHOICES)
    id_carrera = models.ForeignKey(Carrera, on_delete=models.PROTECT, db_column='id_carrera')
    id_ciclo_escolar = models.ForeignKey(CicloEscolar, on_delete=models.PROTECT, db_column='id_ciclo_escolar')
    cupo_maximo = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'grupo'
        verbose_name = 'Grupo'
        verbose_name_plural = 'Grupos'
        constraints = [
            models.UniqueConstraint(
                fields=['clave', 'id_ciclo_escolar'],
                name='uniq_grupo_clave_ciclo',
            )
        ]

    def __str__(self):
        return f"{self.clave} - {self.nombre}"


class Inscripcion(models.Model):
    ESTATUS_CHOICES = [
        ('Activa', 'Activa'),
        ('Baja', 'Baja'),
        ('Reinscripcion', 'Reinscripcion'),
    ]

    id_inscripcion = models.AutoField(primary_key=True)
    id_alumno = models.ForeignKey(Alumnos, on_delete=models.PROTECT, db_column='id_alumno')
    id_grupo = models.ForeignKey(Grupo, on_delete=models.PROTECT, db_column='id_grupo')
    id_ciclo_escolar = models.ForeignKey(CicloEscolar, on_delete=models.PROTECT, db_column='id_ciclo_escolar')
    fecha_inscripcion = models.DateField(auto_now_add=True)
    estatus = models.CharField(max_length=20, choices=ESTATUS_CHOICES, default='Activa')

    class Meta:
        db_table = 'inscripcion'
        verbose_name = 'Inscripcion'
        verbose_name_plural = 'Inscripciones'
        constraints = [
            models.UniqueConstraint(
                fields=['id_alumno', 'id_grupo', 'id_ciclo_escolar'],
                name='uniq_inscripcion_alumno_grupo_ciclo',
                violation_error_message='El alumno ya está inscrito en ese grupo para ese ciclo',
            ),
        ]

    def __str__(self):
        return f"Inscripcion {self.id_inscripcion} - {self.id_alumno} - {self.id_grupo}"


class AsignacionMateria(models.Model):
    ESTATUS_CHOICES = [
        ('Activa', 'Activa'),
        ('Finalizada', 'Finalizada'),
        ('Suspendida', 'Suspendida'),
    ]

    id_asignacion_materia = models.AutoField(primary_key=True)
    id_materia = models.ForeignKey(Materia, on_delete=models.PROTECT, db_column='id_materia')
    id_maestro = models.ForeignKey(Maestros, on_delete=models.PROTECT, db_column='id_maestro')
    id_grupo = models.ForeignKey(Grupo, on_delete=models.PROTECT, db_column='id_grupo')
    id_ciclo_escolar = models.ForeignKey(CicloEscolar, on_delete=models.PROTECT, db_column='id_ciclo_escolar')
    fecha_asignacion = models.DateField(auto_now_add=True)
    estatus = models.CharField(max_length=20, choices=ESTATUS_CHOICES, default='Activa')

    class Meta:
        db_table = 'asignacion_materia'
        verbose_name = 'Asignacion de Materia'
        verbose_name_plural = 'Asignaciones de Materia'
        constraints = [
            models.UniqueConstraint(
                fields=['id_materia', 'id_maestro', 'id_grupo', 'id_ciclo_escolar'],
                name='uniq_asignacion_materia'
            )
        ]

    def __str__(self):
        return f"{self.id_materia} - {self.id_maestro} - {self.id_grupo}"


class Horario(models.Model):
    ESTATUS_CHOICES = [
        ('Activo', 'Activo'),
        ('Cancelado', 'Cancelado'),
        ('Reprogramado', 'Reprogramado'),
    ]

    DIA_CHOICES = [
        ('Lunes', 'Lunes'),
        ('Martes', 'Martes'),
        ('Miercoles', 'Miércoles'),
        ('Jueves', 'Jueves'),
        ('Viernes', 'Viernes'),
        ('Sabado', 'Sábado'),
        ('Domingo', 'Domingo'),
    ]

    id_horario = models.AutoField(primary_key=True)
    id_asignacion_materia = models.ForeignKey(
        AsignacionMateria,
        on_delete=models.PROTECT,
        db_column='id_asignacion_materia'
    )
    dia_semana = models.CharField(max_length=20, choices=DIA_CHOICES)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    aula = models.CharField(max_length=50, null=True, blank=True)
    estatus = models.CharField(max_length=20, choices=ESTATUS_CHOICES, default='Activo')

    class Meta:
        db_table = 'horario'
        verbose_name = 'Horario'
        verbose_name_plural = 'Horarios'
        constraints = [
            models.UniqueConstraint(
                fields=['id_asignacion_materia', 'dia_semana', 'hora_inicio', 'hora_fin'],
                name='uniq_horario_completo',
                violation_error_message='Ya existe un horario idéntico para esta asignación'
            )
        ]

    def __str__(self):
        return f"{self.dia_semana} {self.hora_inicio}-{self.hora_fin}"


class Asistencia(models.Model):
    ESTATUS_CHOICES = [
        ('Presente', 'Presente'),
        ('Ausente', 'Ausente'),
        ('Tarde', 'Tarde'),
        ('Justificado', 'Justificado'),
    ]

    id_asistencia = models.AutoField(primary_key=True)
    id_inscripcion = models.ForeignKey(Inscripcion, on_delete=models.PROTECT, db_column='id_inscripcion')
    id_horario = models.ForeignKey(Horario, on_delete=models.CASCADE, db_column='id_horario')
    fecha_asistencia = models.DateField()
    unidad = models.PositiveIntegerField(default=1)
    estatus = models.CharField(max_length=20, choices=ESTATUS_CHOICES, default='Presente')
    observaciones = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'asistencia'
        verbose_name = 'Asistencia'
        verbose_name_plural = 'Asistencias'
        constraints = [
            models.UniqueConstraint(
                fields=['id_inscripcion', 'id_horario', 'fecha_asistencia', 'unidad'],
                name='uniq_asistencia_por_clase_fecha_unidad'
            )
        ]

    def __str__(self):
        return f"Asistencia {self.id_asistencia} - {self.id_inscripcion} - {self.fecha_asistencia} - U{self.unidad}"


class Calificacion(models.Model):
    id_calificacion = models.AutoField(primary_key=True)
    id_inscripcion = models.ForeignKey(Inscripcion, on_delete=models.PROTECT, db_column='id_inscripcion')
    id_asignacion_materia = models.ForeignKey(
        AsignacionMateria,
        on_delete=models.PROTECT,
        db_column='id_asignacion_materia'
    )
    unidad = models.PositiveIntegerField(default=1)
    calificacion = models.DecimalField(max_digits=5, decimal_places=2)
    fecha_registro = models.DateField(auto_now_add=True)
    observaciones = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'calificacion'
        verbose_name = 'Calificacion'
        verbose_name_plural = 'Calificaciones'
        constraints = [
            models.UniqueConstraint(
                fields=['id_inscripcion', 'id_asignacion_materia', 'unidad'],
                name='uniq_calificacion_por_unidad'
            )
        ]

    def __str__(self):
        return f"Calificacion {self.id_calificacion} - {self.id_inscripcion}"


class LogCalificacion(models.Model):
    """Auditoría de cambios en calificaciones para seguridad"""
    ACCION_CHOICES = [
        ('crear', 'Crear'),
        ('actualizar', 'Actualizar'),
        ('eliminar', 'Eliminar'),
    ]
    
    id_log = models.AutoField(primary_key=True)
    id_calificacion = models.ForeignKey(
        'Calificacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='id_calificacion',
        related_name='logs'
    )
    id_usuario_modifico = models.ForeignKey(
        Usuarios,
        on_delete=models.SET_NULL,
        null=True,
        db_column='id_usuario_modifico',
        related_name='logs_calificaciones'
    )
    id_alumno = models.ForeignKey(
        'Alumnos',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='id_alumno',
        related_name='logs_calificaciones'
    )
    id_materia = models.ForeignKey(
        'Materia',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='id_materia',
        related_name='logs_calificaciones'
    )
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES)
    valor_anterior = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    valor_nuevo = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    unidad = models.PositiveIntegerField(null=True, blank=True)
    fecha_modificacion = models.DateTimeField(auto_now_add=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'log_calificacion'
        verbose_name = 'Log de Calificación'
        verbose_name_plural = 'Logs de Calificaciones'
        ordering = ['-fecha_modificacion']
    
    def __str__(self):
        return f"Log {self.id_log} - {self.accion} - {self.fecha_modificacion}"


class Administrador(models.Model):
    PUESTO_CHOICES = [
        ('Director', 'Director'),
        ('Subdirector', 'Subdirector'),
        ('Auxiliar', 'Auxiliar'),
    ]
    
    id_usuario = models.OneToOneField(
        Usuarios, 
        on_delete=models.CASCADE, 
        primary_key=True, 
        db_column='id_usuario'
    )
    puesto = models.CharField(max_length=20, choices=PUESTO_CHOICES)
    nivel_prioridad = models.IntegerField()  # 1 = mayor acceso
    
    # Esta es la FK que me pides especificar
    id_ciclo_escolar = models.ForeignKey(
        'CicloEscolar', 
        on_delete=models.SET_NULL, 
        null=True,
        db_column='id_ciclo_escolar' # Forzamos el nombre exacto en SQL
    )
    
    class Meta:
        db_table = 'administrador'
        verbose_name = 'Administrador'
        verbose_name_plural = 'Administradores'
    
    def __str__(self):
        return f"Administrador: {self.id_usuario.matricula} - {self.id_usuario.nombre} {self.id_usuario.apellido}"


class TokenRecuperacionContrasena(models.Model):
    """Token de un solo uso para restablecer contraseña vía correo electrónico."""

    id_token = models.AutoField(primary_key=True)
    id_usuario = models.ForeignKey(
        Usuarios,
        on_delete=models.CASCADE,
        db_column='id_usuario',
        related_name='tokens_recuperacion',
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    correo_destino = models.EmailField()
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField()
    usado_en = models.DateTimeField(null=True, blank=True)
    ip_solicitud = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = 'token_recuperacion_contrasena'
        verbose_name = 'Token de recuperación de contraseña'
        verbose_name_plural = 'Tokens de recuperación de contraseña'
        indexes = [
            models.Index(fields=['id_usuario', 'creado_en']),
        ]

    def __str__(self):
        return f'Recuperación {self.id_usuario.matricula} ({self.creado_en:%Y-%m-%d %H:%M})'

    @property
    def vigente(self) -> bool:
        if self.usado_en:
            return False
        return timezone.now() < self.expira_en

from django.contrib import admin
from .models import (
    Usuarios,
    Materia,
    Grupo,
    Inscripcion,
    AsignacionMateria,
    Horario,
    Asistencia,
    Calificacion,
    CicloEscolar,
    Alumnos,
    Maestros,
)

class UsuariosAdmin(admin.ModelAdmin):
    list_display = ('id_usuario', 'matricula', 'nombre', 'apellido', 'contrasena', 'rol')
    list_filter = ('rol',)

admin.site.register(Usuarios, UsuariosAdmin)


@admin.register(CicloEscolar)
class CicloEscolarAdmin(admin.ModelAdmin):
    list_display = ('id_ciclo_escolar', 'nombre_ciclo', 'periodo', 'fecha_inicio', 'fecha_fin')
    list_filter = ('periodo',)
    search_fields = ('nombre_ciclo',)


@admin.register(Alumnos)
class AlumnosAdmin(admin.ModelAdmin):
    search_fields = ('id_usuario__nombre', 'id_usuario__apellido', 'id_usuario__matricula')


@admin.register(Maestros)
class MaestrosAdmin(admin.ModelAdmin):
    search_fields = ('id_usuario__nombre', 'id_usuario__apellido', 'id_usuario__matricula')


@admin.register(Materia)
class MateriaAdmin(admin.ModelAdmin):
    list_display = ('id_materia', 'clave', 'nombre', 'semestre', 'creditos', 'activo', 'created_at', 'updated_at')
    list_filter = ('activo', 'semestre')
    search_fields = ('clave', 'nombre')


@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ('id_grupo', 'clave', 'nombre', 'semestre', 'turno', 'id_carrera', 'id_ciclo_escolar', 'activo')
    list_filter = ('activo', 'turno', 'semestre')
    search_fields = ('clave', 'nombre')


@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ('id_inscripcion', 'id_alumno', 'id_grupo', 'id_ciclo_escolar', 'fecha_inscripcion', 'estatus')
    list_filter = ('estatus', 'id_ciclo_escolar')
    search_fields = ('id_alumno__id_usuario__matricula', 'id_grupo__clave')
    autocomplete_fields = ('id_alumno', 'id_grupo', 'id_ciclo_escolar')


@admin.register(AsignacionMateria)
class AsignacionMateriaAdmin(admin.ModelAdmin):
    list_display = ('id_asignacion_materia', 'id_materia', 'id_maestro', 'id_grupo', 'id_ciclo_escolar', 'fecha_asignacion', 'estatus')
    list_filter = ('estatus', 'id_ciclo_escolar')
    search_fields = ('id_materia__nombre', 'id_maestro__id_usuario__nombre', 'id_grupo__clave')
    autocomplete_fields = ('id_materia', 'id_maestro', 'id_grupo', 'id_ciclo_escolar')


@admin.register(Horario)
class HorarioAdmin(admin.ModelAdmin):
    list_display = ('id_horario', 'id_asignacion_materia', 'dia_semana', 'hora_inicio', 'hora_fin', 'aula', 'estatus')
    list_filter = ('estatus', 'dia_semana')
    search_fields = ('id_asignacion_materia__id_materia__nombre', 'id_asignacion_materia__id_grupo__clave')
    autocomplete_fields = ('id_asignacion_materia',)


@admin.register(Asistencia)
class AsistenciaAdmin(admin.ModelAdmin):
    list_display = ('id_asistencia', 'id_inscripcion', 'id_horario', 'fecha_asistencia', 'unidad', 'estatus')
    list_filter = ('estatus', 'fecha_asistencia', 'unidad')
    search_fields = ('id_inscripcion__id_alumno__id_usuario__matricula', 'id_horario__id_asignacion_materia__id_grupo__clave')
    autocomplete_fields = ('id_inscripcion', 'id_horario')


@admin.register(Calificacion)
class CalificacionAdmin(admin.ModelAdmin):
    list_display = ('id_calificacion', 'id_inscripcion', 'id_asignacion_materia', 'unidad', 'calificacion', 'fecha_registro')
    list_filter = ('unidad', 'fecha_registro')
    search_fields = ('id_inscripcion__id_alumno__id_usuario__matricula', 'id_asignacion_materia__id_materia__nombre')
    autocomplete_fields = ('id_inscripcion', 'id_asignacion_materia')

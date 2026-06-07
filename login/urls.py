from django.urls import path
from . import views
from . import admin_views
from . import recuperacion_views

urlpatterns = [
    path('', views.selector_rol, name='selector_rol'),
    path('login/alumno/', views.login_alumno, name='login_alumno'),
    path('login/maestro/', views.login_maestro, name='login_maestro'),
    path('login/administrativo/', views.login_administrativo, name='login_administrativo'),
    path('login/administrador/', views.login_administrador, name='login_administrador'),

    # Recuperación de contraseña por correo (matrícula + correo institucional)
    path('recuperar-contrasena/', recuperacion_views.recuperar_contrasena_solicitud, name='recuperar_contrasena_solicitud'),
    path('recuperar-contrasena/enviado/', recuperacion_views.recuperar_contrasena_enviado, name='recuperar_contrasena_enviado'),
    path('recuperar-contrasena/establecer/<str:token>/', recuperacion_views.recuperar_contrasena_establecer, name='recuperar_contrasena_establecer'),
    
    # Dashboards protegidos por rol
    path('dashboard/alumno/', views.dashboard_alumno, name='dashboard_alumno'),
    path('dashboard/maestro/', views.dashboard_maestro, name='dashboard_maestro'),
    path('dashboard/administrativo/', views.dashboard_administrativo, name='dashboard_administrativo'),
    path('dashboard/administrador/', views.dashboard_administrador, name='dashboard_administrador'),

    # Rutas usadas por el módulo del alumno
    path('alumno/', views.inicio_interfaces_alumnos, name='inicio_interfaces_alumnos'),
    path('alumno/calificaciones/', views.consultar_calificaciones, name='consultar_calificaciones'),
    path('alumno/calificaciones/exportar-pdf/', views.exportar_boleta_calificaciones_alumno, name='exportar_boleta_calificaciones_alumno'),
    path('alumno/asistencias/', views.consultar_asistencias, name='consultar_asistencias'),
    path('alumno/asistencias/exportar-pdf/', views.exportar_asistencias_alumno, name='exportar_asistencias_alumno'),

    # Rutas usadas por el módulo del maestro
    path('maestro/asistencia/', views.registrar_asistencia, name='registrar_asistencia'),
    path('maestro/asistencia/datos/', views.get_datos_asistencia_maestro, name='get_datos_asistencia_maestro'),
    path('maestro/asistencia/guardar/', views.guardar_asistencia_maestro, name='guardar_asistencia_maestro'),
    path('maestro/calificaciones/', views.registrar_calificaciones, name='registrar_calificaciones'),
    path('maestro/calificaciones/datos/', views.get_datos_calificaciones_maestro, name='get_datos_calificaciones_maestro'),
    path('maestro/calificaciones/guardar/', views.guardar_calificaciones_maestro, name='guardar_calificaciones_maestro'),
    path('maestro/reportes/', views.consultar_reportes, name='consultar_reportes'),
    path('maestro/reportes/exportar/', views.exportar_reportes_maestro_excel, name='exportar_reportes_maestro_excel'),
    path('maestro/reportes/exportar-pdf/', views.exportar_reportes_maestro_pdf, name='exportar_reportes_maestro_pdf'),
    path('maestro/materias-por-semestre/', views.get_materias_por_semestre, name='get_materias_por_semestre'),
    
    # Vistas de administrador
    path('administrativo/reportes/', views.admin_reportes, name='admin_reportes'),
    path('administrativo/reportes/exportar-excel/', views.exportar_reportes_admin_excel, name='exportar_reportes_admin_excel'),
    path('administrativo/reportes/exportar-pdf/', views.exportar_reportes_admin_pdf, name='exportar_reportes_admin_pdf'),
    path('administrativo/horarios/', views.admin_horarios, name='admin_horarios'),
    path('administrativo/horarios/exportar-pdf/', views.exportar_horarios_pdf, name='exportar_horarios_pdf'),
    path('administrativo/materias/', views.admin_materias, name='admin_materias'),
    path('administrativo/materias/exportar-pdf/', views.exportar_materias_pdf, name='exportar_materias_pdf'),
    path('administrativo/materias/exportar-excel/', views.exportar_materias_excel, name='exportar_materias_excel'),
    path('administrativo/materias/crear/', views.crear_materia, name='crear_materia'),
    path('administrativo/materias/editar/<int:materia_id>/', views.editar_materia, name='editar_materia'),
    path('administrativo/materias/eliminar/<int:materia_id>/', views.eliminar_materia, name='eliminar_materia'),
    path('administrativo/horarios/agregar/', views.agregar_horario, name='agregar_horario'),
    path('administrativo/horarios/editar/', views.editar_horario, name='editar_horario'),
    path('administrativo/horarios/eliminar/', views.eliminar_horario, name='eliminar_horario'),
    path('administrativo/horarios/semanales/', views.get_horarios_semanales, name='get_horarios_semanales'),
    path('administrativo/grupos/', views.gestion_grupos, name='gestion_grupos'),
    path('administrativo/grupos/crear/', views.crear_grupo, name='crear_grupo'),
    path('administrativo/grupos/editar/<int:grupo_id>/', views.editar_grupo, name='editar_grupo'),
    path('administrativo/grupos/reutilizar/<int:grupo_id>/', views.reutilizar_grupo, name='reutilizar_grupo'),
    path('administrativo/grupos/eliminar/<int:grupo_id>/', views.eliminar_grupo, name='eliminar_grupo'),
    path('administrativo/inscripciones/', views.gestion_inscripciones, name='gestion_inscripciones'),
    path('administrativo/inscripciones/crear/', views.crear_inscripcion, name='crear_inscripcion'),
    path('administrativo/inscripciones/crear-generacion/', views.crear_inscripcion_generacion, name='crear_inscripcion_generacion'),
    path('administrativo/inscripciones/editar/<int:inscripcion_id>/', views.editar_inscripcion, name='editar_inscripcion'),
    path('administrativo/inscripciones/eliminar/<int:inscripcion_id>/', views.eliminar_inscripcion, name='eliminar_inscripcion'),
    path('administrativo/asignaciones/', views.gestion_asignaciones, name='gestion_asignaciones'),
    path('administrativo/asignaciones/crear/', views.crear_asignacion, name='crear_asignacion'),
    path('administrativo/asignaciones/editar/<int:asignacion_id>/', views.editar_asignacion, name='editar_asignacion'),
    path('administrativo/asignaciones/eliminar/<int:asignacion_id>/', views.eliminar_asignacion, name='eliminar_asignacion'),
    # READ — listar usuarios (admin_views.gestion_usuarios + GestionUsuarios.html)
    path('administrador/usuarios/', admin_views.gestion_usuarios, name='gestion_usuarios'),
    # READ extendido — exportar lista a Excel/PDF (misma consulta, sin modificar BD)
    path('administrador/usuarios/exportar/', admin_views.exportar_usuarios, name='exportar_usuarios'),
    path('administrador/usuarios/exportar-pdf/', admin_views.exportar_usuarios_pdf, name='exportar_usuarios_pdf'),
    # CREATE — alta de usuario (admin_views.agregar_usuario + AgregarUsuario.html)
    path('administrador/usuarios/agregar/', admin_views.agregar_usuario, name='agregar_usuario'),
    # UPDATE — editar usuario (admin_views.editar_usuario + EditarUsuario.html)
    path('administrador/usuarios/editar/<int:usuario_id>/', admin_views.editar_usuario, name='editar_usuario'),
    # DELETE — eliminar usuario (admin_views.eliminar_usuario; AJAX desde GestionUsuarios.html)
    path('administrador/usuarios/eliminar/<int:usuario_id>/', admin_views.eliminar_usuario, name='eliminar_usuario'),
    # UPDATE parcial — restablecer contraseña temporal
    path('administrador/usuarios/restablecer/<int:usuario_id>/', admin_views.restablecer_contrasena, name='restablecer_contrasena'),
    path('administrador/carreras/', admin_views.gestion_carreras, name='gestion_carreras'),
    path('administrador/carreras/agregar/', admin_views.agregar_carrera, name='agregar_carrera'),
    path('administrador/carreras/editar/<int:carrera_id>/', admin_views.editar_carrera, name='editar_carrera'),
    path('administrador/carreras/eliminar/<int:carrera_id>/', admin_views.eliminar_carrera, name='eliminar_carrera'),
    path('administrador/carreras/verificar-clave/', admin_views.verificar_clave_carrera, name='verificar_clave_carrera'),
    path('administrador/carreras/verificar-nombre/', admin_views.verificar_nombre_carrera, name='verificar_nombre_carrera'),
    path('administrador/ciclos/', admin_views.gestion_ciclos, name='gestion_ciclos'),
    path('administrador/ciclos/agregar/', admin_views.agregar_ciclo, name='agregar_ciclo'),
    path('administrador/ciclos/editar/<int:ciclo_id>/', admin_views.editar_ciclo, name='editar_ciclo'),
    path('administrador/ciclos/eliminar/<int:ciclo_id>/', admin_views.eliminar_ciclo, name='eliminar_ciclo'),
    path('administrador/seguridad/', admin_views.gestion_seguridad, name='gestion_seguridad'),
    path('administrador/seguridad/desbloquear/<int:usuario_id>/', admin_views.desbloquear_cuenta, name='desbloquear_cuenta'),
    path('administrador/respaldo/', admin_views.respaldo_bdd, name='respaldo_bdd'),
    path('administrador/ejecutar-respaldo/', admin_views.ejecutar_respaldo, name='ejecutar_respaldo'),
    path('administrador/descargar-respaldo/<str:filename>/', admin_views.descargar_respaldo_especifico, name='descargar_respaldo_especifico'),
    
    # Cambio obligatorio de contraseña temporal
    path('cambiar-contrasena/', views.cambiar_contrasena_temporal, name='cambiar_contrasena_temporal'),
    
    # Logout
    path('logout/', views.logout_view, name='logout'),
]

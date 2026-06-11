import psycopg2
import sys
import io
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env')
except ImportError:
    pass

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    connection = psycopg2.connect(
        dbname=os.environ.get('DJANGO_DB_NAME', 'Schooltrack_db'),
        user=os.environ.get('DJANGO_DB_USER', 'postgres'),
        password=os.environ.get('DJANGO_DB_PASSWORD', ''),
        host=os.environ.get('DJANGO_DB_HOST', 'localhost'),
        port=os.environ.get('DJANGO_DB_PORT', '5432')
    )
    
    print(" CONEXION EXITOSA")
    print(f" Base de datos: {connection.get_dsn_parameters()['dbname']}")
    print(f" Usuario: {connection.get_dsn_parameters()['user']}")
    print(f" Host: {connection.get_dsn_parameters()['host']}")
    
    connection.close()
    print(" Conexión cerrada")
    
except Exception as e:
    print(f" ERROR: {e}")

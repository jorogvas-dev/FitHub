import psycopg


def connect():
    # PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE supplied by Compose.
    return psycopg.connect(connect_timeout=3)

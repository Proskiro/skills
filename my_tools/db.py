import os

import psycopg2


def get_db_connection():
    """
    Create a psycopg2 connection using env vars:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
    """
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode="verify-full",
        sslrootcert="/Users/eikram/Documents/Stickies/Other/Skills/global-bundle.pem",
    )
    conn.autocommit = False
    return conn

import os

import psycopg2
from dotenv import load_dotenv

# Load .env file
load_dotenv()


def get_db_connection():
    """
    Create a psycopg2 connection using env vars:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, SSL_CERT_PATH.
    """
    ssl_cert = os.getenv("SSL_CERT_PATH", "global-bundle.pem")
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode="verify-full",
        sslrootcert=ssl_cert,
    )
    conn.autocommit = False
    return conn


def occupation_is_fresh(uri: str, days: int = 30) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT 1
        FROM occupations
        WHERE uri = %s
          AND updated_at >= now() - interval %s
        LIMIT 1;
    """
    cursor.execute(query, (uri, f"{days} days"))
    is_fresh = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return is_fresh


def skill_is_fresh(uri: str, days: int = 30) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT 1
        FROM skills
        WHERE uri = %s
          AND updated_at >= now() - interval %s
        LIMIT 1;
    """
    cursor.execute(query, (uri, f"{days} days"))
    is_fresh = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return is_fresh

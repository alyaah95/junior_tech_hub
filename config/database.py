import os
import logging
from contextlib import contextmanager
from dotenv import load_dotenv
import psycopg2
from psycopg2 import pool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

db_pool = None


def init_db_pool():
    """
    Initializes PostgreSQL SimpleConnectionPool with Keepalives settings to prevent SSL drops.
    """
    global db_pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            sslmode="require",
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        logger.info("Database Connection Pool successfully initialized with TCP Keepalives.")
    except Exception as e:
        logger.error(f"Failed to initialize database connection pool: {e}")
        raise e


@contextmanager
def get_db_connection():
    """
    Context manager with automatic re-connection logic (pre-ping check) to handle broken SSL connections.
    """
    global db_pool
    if db_pool is None:
        init_db_pool()
        
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1;")
    except Exception:
        logger.warning("Stale or closed connection detected in pool. Reconnecting...")
        try:
            db_pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = db_pool.getconn()

    try:
        yield conn
    finally:
        db_pool.putconn(conn)


def create_tables():
    create_cache_table_query = """
        CREATE TABLE IF NOT EXISTS processed_jobs_cache (
            source VARCHAR(50) NOT NULL,
            job_id VARCHAR(100) NOT NULL,
            processed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, job_id)
        );
    """

    create_jobs_table_query = """
    CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY,
        source VARCHAR(50) NOT NULL,
        job_id VARCHAR(100) NOT NULL,
        title VARCHAR(255) NOT NULL,
        company VARCHAR(255) NOT NULL,
        location VARCHAR(255) NOT NULL,
        job_type VARCHAR(100),
        job_link TEXT NOT NULL,
        category VARCHAR(100),
        description TEXT,
        summary TEXT,
        posted_at VARCHAR(100),
        scraped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT unique_source_job_id UNIQUE (source, job_id)
    );
    """

    create_published_jobs_cache_query = """
    CREATE TABLE IF NOT EXISTS published_jobs_cache (
    source VARCHAR(50) NOT NULL,
    job_id VARCHAR(255) NOT NULL,
    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, job_id)
);
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(create_cache_table_query)
                cursor.execute(create_jobs_table_query)
                cursor.execute(create_published_jobs_cache_query)
                conn.commit()
                logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise e


def close_db_pool():
    global db_pool
    if db_pool:
        db_pool.closeall()
        logger.info("Database Connection Pool successfully closed.")


if __name__ == "__main__":
    try:
        create_tables()
        close_db_pool()
        print("Test Passed: Database connection and schema setup successful!")
    except Exception as err:
        print(f"Test Failed: {err}")
import sqlite3
import contextlib

@contextlib.contextmanager
def get_db_connection():
    conn = sqlite3.connect("vpn.db")
    try:
        yield conn
    finally:
        conn.close()

@contextlib.contextmanager
def get_db_cursor(commit=False):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        finally:
            cursor.close() 
import sqlite3
import contextlib
import threading
import queue
from config import DATABASE_PATH

# Connection pool для SQLite
class SQLiteConnectionPool:
    """Пул соединений для переиспользования подключений к БД"""
    
    def __init__(self, db_path, max_connections=5):
        self.db_path = db_path
        self.max_connections = max_connections
        self.pool = queue.Queue(maxsize=max_connections)
        self._lock = threading.Lock()
        self._initialized = False
        self._init_pool()
    
    def _init_pool(self):
        """Инициализация пула соединений"""
        if self._initialized:
            return
        
        with self._lock:
            if self._initialized:
                return
            
            for _ in range(self.max_connections):
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                # Применяем оптимизации SQLite
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.row_factory = sqlite3.Row
                self.pool.put(conn)
            
            self._initialized = True
    
    def get_connection(self):
        """Получить соединение из пула"""
        try:
            conn = self.pool.get(timeout=5)
            # Проверяем, что соединение еще валидно
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error:
                # Если соединение невалидно, создаем новое
                conn.close()
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.row_factory = sqlite3.Row
            return conn
        except queue.Empty:
            # Если пул пуст, создаем новое соединение (fallback)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            return conn
    
    def return_connection(self, conn):
        """Вернуть соединение в пул"""
        try:
            self.pool.put_nowait(conn)
        except queue.Full:
            # Если пул переполнен, закрываем соединение
            conn.close()

# Глобальный пул соединений
_db_pool = None
_pool_lock = threading.Lock()

def _get_db_pool():
    """Получить или создать глобальный пул соединений"""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = SQLiteConnectionPool(DATABASE_PATH)
    return _db_pool

@contextlib.contextmanager
def get_db_connection():
    """Получить соединение с БД из пула"""
    pool = _get_db_pool()
    conn = pool.get_connection()
    try:
        yield conn
    finally:
        pool.return_connection(conn)

@contextlib.contextmanager
def get_db_cursor(commit=False):
    """Получить курсор для работы с БД"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        finally:
            cursor.close() 
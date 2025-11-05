import sqlite3
import logging
from config import DATABASE_PATH

def init_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=5)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        api_url TEXT,
        cert_sha256 TEXT,
        max_keys INTEGER DEFAULT 100,
        active INTEGER DEFAULT 1,
        country TEXT,
        protocol TEXT DEFAULT 'outline',
        domain TEXT,
        v2ray_path TEXT,
        api_key TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        duration_sec INTEGER,
        traffic_limit_mb INTEGER,
        price_rub INTEGER
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        user_id INTEGER,
        access_url TEXT,
        expiry_at INTEGER,
        traffic_limit_mb INTEGER,
        notified INTEGER DEFAULT 0,
        key_id TEXT,
        email TEXT,
        tariff_id INTEGER
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS v2ray_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        user_id INTEGER,
        v2ray_uuid TEXT,
        email TEXT,
        created_at INTEGER,
        expiry_at INTEGER,
        tariff_id INTEGER
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        tariff_id INTEGER,
        payment_id TEXT,
        status TEXT DEFAULT 'pending',
        email TEXT,
        revoked INTEGER DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        created_at INTEGER,
        bonus_issued INTEGER DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS free_key_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        protocol TEXT NOT NULL,
        country TEXT,
        created_at INTEGER,
        UNIQUE(user_id, protocol, country)
    )""")

    # Webhook logs
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            event TEXT,
            payload TEXT,
            result TEXT,
            status_code INTEGER,
            ip TEXT,
            created_at INTEGER
        )
        """
    )
    # Helpful indexes
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_created_at ON webhook_logs(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_provider ON webhook_logs(provider)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_event ON webhook_logs(event)")
    except sqlite3.OperationalError:
        pass

    # Ensure extended payments schema exists (for tests and new deployments)
    try:
        # Check existing columns
        c.execute("PRAGMA table_info(payments)")
        cols = {row[1] for row in c.fetchall()}
        if 'currency' not in cols:
            migrate_extend_payments_schema()
    except Exception as e:
        logging.warning(f"Failed to ensure extended payments schema: {e}")

    # PRAGMA tuning for better performance and durability
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=30000000000")  # ~30GB if supported, harmless fallback otherwise
        c.execute("PRAGMA busy_timeout=5000")
    except sqlite3.DatabaseError as e:
        logging.warning(f"SQLite PRAGMA setup failed: {e}")

    conn.commit()
    conn.close()

def migrate_add_key_id():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN key_id TEXT")
        conn.commit()
        logging.info("Поле key_id успешно добавлено.")
    except sqlite3.OperationalError:
        logging.info("Поле key_id уже существует.")
    conn.close()

def migrate_add_email():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN email TEXT")
        conn.commit()
        logging.info("Поле email успешно добавлено в таблицу keys.")
    except sqlite3.OperationalError:
        logging.info("Поле email уже существует в таблице keys.")
    conn.close()

def migrate_add_payment_email():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE payments ADD COLUMN email TEXT")
        conn.commit()
        logging.info("Поле email успешно добавлено в таблицу payments.")
    except sqlite3.OperationalError:
        logging.info("Поле email уже существует в таблице payments.")
    conn.close()

def migrate_add_tariff_id_to_keys():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN tariff_id INTEGER")
        conn.commit()
        logging.info("Поле tariff_id успешно добавлено в таблицу keys.")
    except sqlite3.OperationalError:
        logging.info("Поле tariff_id уже существует в таблице keys.")
    conn.close()

def migrate_add_country_to_servers():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN country TEXT")
        conn.commit()
        logging.info("Поле country успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        logging.info("Поле country уже существует в таблице servers.")
    conn.close()

def migrate_add_revoked_to_payments():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE payments ADD COLUMN revoked INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле revoked успешно добавлено в таблицу payments.")
    except sqlite3.OperationalError:
        logging.info("Поле revoked уже существует в таблице payments.")
    conn.close()

def migrate_extend_payments_schema():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # Получаем существующие колонки
        cursor.execute("PRAGMA table_info(payments)")
        cols = {row[1] for row in cursor.fetchall()}

        # Требуемые дополнительные поля по новой схеме
        required_columns = [
            ("amount", "INTEGER DEFAULT 0"),
            ("currency", "TEXT DEFAULT 'RUB'"),
            ("country", "TEXT"),
            ("protocol", "TEXT DEFAULT 'outline'"),
            ("provider", "TEXT DEFAULT 'yookassa'"),
            ("method", "TEXT"),
            ("description", "TEXT"),
            ("created_at", "INTEGER"),
            ("updated_at", "INTEGER"),
            ("paid_at", "INTEGER"),
            ("metadata", "TEXT")
        ]

        added = 0
        for name, decl in required_columns:
            if name not in cols:
                try:
                    cursor.execute(f"ALTER TABLE payments ADD COLUMN {name} {decl}")
                    added += 1
                except sqlite3.OperationalError:
                    pass

        # Уникальный индекс на payment_id
        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_payment_id ON payments(payment_id)")
        except sqlite3.OperationalError:
            pass

        # Полезные индексы для запросов
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at)")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        logging.info(f"Схема payments обновлена, добавлено полей: {added}")
    finally:
        conn.close()

def migrate_add_free_key_usage():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS free_key_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                protocol TEXT NOT NULL,
                country TEXT,
                created_at INTEGER,
                UNIQUE(user_id, protocol, country)
            )
        """)
        conn.commit()
        logging.info("Таблица free_key_usage успешно создана.")
    except sqlite3.OperationalError:
        logging.info("Таблица free_key_usage уже существует.")
    conn.close()

def migrate_add_protocol_fields():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN protocol TEXT DEFAULT 'outline'")
        conn.commit()
        logging.info("Поле protocol успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        logging.info("Поле protocol уже существует в таблице servers.")
    
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN domain TEXT")
        conn.commit()
        logging.info("Поле domain успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        logging.info("Поле domain уже существует в таблице servers.")
    
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN v2ray_path TEXT")
        conn.commit()
        logging.info("Поле v2ray_path успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        logging.info("Поле v2ray_path уже существует в таблице servers.")
    
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN api_key TEXT")
        conn.commit()
        logging.info("Поле api_key успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        logging.info("Поле api_key уже существует в таблице servers.")
    
    conn.close()

def migrate_add_tariff_id_to_v2ray_keys():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN tariff_id INTEGER")
        conn.commit()
        logging.info("Поле tariff_id успешно добавлено в таблицу v2ray_keys.")
    except sqlite3.OperationalError:
        logging.info("Поле tariff_id уже существует в таблице v2ray_keys.")
    conn.close()

def migrate_add_common_indexes():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # keys indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_user_id ON keys(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_expiry_at ON keys(expiry_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_server_id ON keys(server_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_tariff_id ON keys(tariff_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_email ON keys(email) WHERE email IS NOT NULL")
        # v2ray_keys indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_user_id ON v2ray_keys(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_expiry_at ON v2ray_keys(expiry_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_server_id ON v2ray_keys(server_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_tariff_id ON v2ray_keys(tariff_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_created_at ON v2ray_keys(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_email ON v2ray_keys(email) WHERE email IS NOT NULL")
        # servers indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_servers_active ON servers(active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_servers_protocol ON servers(protocol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_servers_country ON servers(country)")
        # tariffs helpful index
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tariffs_price_rub ON tariffs(price_rub)")
        # payments indexes для улучшения производительности
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_tariff_id ON payments(tariff_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_payment_id ON payments(payment_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_email ON payments(email) WHERE email IS NOT NULL")
        conn.commit()
        logging.info("Созданы индексы для основных таблиц (если отсутствовали)")
    finally:
        conn.close()

def migrate_add_unique_key_indexes():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_keys_user_keyid ON keys(user_id, key_id)")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v2ray_user_uuid ON v2ray_keys(user_id, v2ray_uuid)")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        logging.info("Установлены уникальные индексы для keys и v2ray_keys (если отсутствовали)")
    finally:
        conn.close()
def migrate_create_users_table():
    """Create users table for storing all bot users"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at INTEGER,
                last_active_at INTEGER,
                blocked INTEGER DEFAULT 0
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON users(blocked)")
        conn.commit()
        logging.info("Таблица users создана/проверена")
    except sqlite3.OperationalError as e:
        logging.info(f"Ошибка создания таблицы users: {e}")
    finally:
        conn.close()

def migrate_backfill_users():
    """Fill users table with existing user_id from keys and v2ray_keys"""
    import time
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # Get all unique user_ids
        cursor.execute("""
            SELECT DISTINCT user_id, MIN(created_at) as earliest, MAX(COALESCE(expiry_at, created_at)) as latest
            FROM (
                SELECT user_id, created_at, expiry_at FROM keys
                UNION ALL
                SELECT user_id, created_at, expiry_at FROM v2ray_keys
            )
            GROUP BY user_id
        """)
        
        existing_users = []
        for row in cursor.fetchall():
            user_id, earliest, latest = row
            if user_id not in existing_users:
                cursor.execute("""
                    INSERT OR IGNORE INTO users (user_id, created_at, last_active_at, blocked)
                    VALUES (?, ?, ?, 0)
                """, (user_id, earliest or int(time.time()), latest or int(time.time())))
                existing_users.append(user_id)
        
        conn.commit()
        logging.info(f"Backfill users: добавлено {len(existing_users)} пользователей")
    except sqlite3.OperationalError as e:
        logging.info(f"Ошибка backfill users: {e}")
    finally:
        conn.close()

def migrate_create_webhook_logs():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT,
                event TEXT,
                payload TEXT,
                result TEXT,
                status_code INTEGER,
                ip TEXT,
                created_at INTEGER
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_created_at ON webhook_logs(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_provider ON webhook_logs(provider)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_logs_event ON webhook_logs(event)")
        conn.commit()
        logging.info("Таблица webhook_logs создана/проверена")
    finally:
        conn.close()

def migrate_add_crypto_pricing():
    """Добавление поддержки крипто-цен в тарифах"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE tariffs ADD COLUMN price_crypto_usd REAL DEFAULT NULL")
        conn.commit()
        logging.info("Поле price_crypto_usd добавлено в tariffs")
    except sqlite3.OperationalError:
        logging.info("Поле price_crypto_usd уже существует в tariffs")
    finally:
        conn.close()

def migrate_add_crypto_payment_fields():
    """Добавление полей для криптоплатежей в payments"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # Получаем существующие колонки
        cursor.execute("PRAGMA table_info(payments)")
        cols = {row[1] for row in cursor.fetchall()}
        
        # Добавляем поля для криптоплатежей
        crypto_fields = [
            ("crypto_invoice_id", "TEXT"),
            ("crypto_amount", "REAL"),
            ("crypto_currency", "TEXT"),
            ("crypto_network", "TEXT"),
            ("crypto_tx_hash", "TEXT"),
        ]
        
        added = 0
        for name, decl in crypto_fields:
            if name not in cols:
                try:
                    cursor.execute(f"ALTER TABLE payments ADD COLUMN {name} {decl}")
                    added += 1
                except sqlite3.OperationalError:
                    pass
        
        conn.commit()
        logging.info(f"Добавлено полей для криптоплатежей: {added}")
    except Exception as e:
        logging.error(f"Ошибка при добавлении полей для криптоплатежей: {e}")
    finally:
        conn.close()

def migrate_add_client_config_to_v2ray_keys():
    """Добавление поля client_config в v2ray_keys для хранения конфигурации"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN client_config TEXT DEFAULT NULL")
        conn.commit()
        logging.info("Поле client_config добавлено в v2ray_keys")
    except sqlite3.OperationalError:
        logging.info("Поле client_config уже существует в v2ray_keys")
    finally:
        conn.close()

if __name__ == "__main__":
    import time
    init_db()
    migrate_add_key_id()
    migrate_add_email()
    migrate_add_payment_email()
    migrate_add_tariff_id_to_keys()
    migrate_add_country_to_servers()
    migrate_add_revoked_to_payments()
    migrate_add_free_key_usage()
    migrate_add_protocol_fields()
    migrate_add_tariff_id_to_v2ray_keys()
    migrate_extend_payments_schema()
    migrate_add_common_indexes()
    migrate_add_unique_key_indexes()
    migrate_create_webhook_logs()
    migrate_create_users_table()
    migrate_backfill_users()
    migrate_add_crypto_pricing()
    migrate_add_crypto_payment_fields()
    migrate_add_client_config_to_v2ray_keys()


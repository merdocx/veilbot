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
        created_at INTEGER,
        email TEXT,
        tariff_id INTEGER,
        protocol TEXT DEFAULT 'outline',
        FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS v2ray_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id INTEGER,
        user_id INTEGER,
        v2ray_uuid TEXT UNIQUE,
        email TEXT,
        level INTEGER DEFAULT 0,
        created_at INTEGER,
        expiry_at INTEGER,
        tariff_id INTEGER,
        client_config TEXT DEFAULT NULL,
        notified INTEGER DEFAULT 0,
        traffic_limit_mb INTEGER DEFAULT 0,
        traffic_usage_bytes INTEGER DEFAULT 0,
        subscription_id INTEGER,
        FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        subscription_token TEXT UNIQUE NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        tariff_id INTEGER,
        is_active INTEGER DEFAULT 1,
        last_updated_at INTEGER,
        notified INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
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

    # Extended payments schema будет добавлена через миграции

    # PRAGMA tuning for better performance and durability
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=30000000000")  # ~30GB if supported, harmless fallback otherwise
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA foreign_keys=ON")
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

def migrate_extend_payments_schema():
    """Расширение схемы таблицы payments для поддержки новых полей"""
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
    except Exception as e:
        logging.error(f"Ошибка при расширении схемы payments: {e}")
    finally:
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
        
        # Композитные индексы для частых комбинаций запросов
        # Индексы для запросов по user_id и expiry_at (очень частый паттерн)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_user_expiry ON keys(user_id, expiry_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_user_expiry ON v2ray_keys(user_id, expiry_at)")
        
        # Индекс для запросов по user_id и status в payments
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments(user_id, status)")
        
        # Индекс для запросов по server_id и expiry_at (для фоновых задач)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keys_server_expiry ON keys(server_id, expiry_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_server_expiry ON v2ray_keys(server_id, expiry_at)")
        
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
def migrate_create_dashboard_metrics_table():
    """Создание таблицы для хранения ежедневных метрик дашборда"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                active_keys INTEGER NOT NULL DEFAULT 0,
                started_users INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_metrics_date ON dashboard_metrics(date)")
        conn.commit()
        logging.info("Таблица dashboard_metrics создана/проверена")
    except Exception as e:
        logging.error(f"Ошибка создания таблицы dashboard_metrics: {e}")
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

def migrate_add_notified_to_v2ray_keys():
    """Добавление поля notified в v2ray_keys для отслеживания отправленных уведомлений"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN notified INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле notified добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: notified" in str(e):
            logging.info("Поле notified уже существует в v2ray_keys")
        else:
            raise
    finally:
        conn.close()

def migrate_add_traffic_monitoring_to_v2ray_keys():
    """Добавление полей для контроля превышения трафикового лимита в v2ray_keys"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN traffic_limit_mb INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле traffic_limit_mb добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: traffic_limit_mb" in str(e):
            logging.info("Поле traffic_limit_mb уже существует в v2ray_keys")
        else:
            raise
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN traffic_usage_bytes INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле traffic_usage_bytes добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: traffic_usage_bytes" in str(e):
            logging.info("Поле traffic_usage_bytes уже существует в v2ray_keys")
        else:
            raise
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN traffic_over_limit_at INTEGER")
        conn.commit()
        logging.info("Поле traffic_over_limit_at добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: traffic_over_limit_at" in str(e):
            logging.info("Поле traffic_over_limit_at уже существует в v2ray_keys")
        else:
            raise
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN traffic_over_limit_notified INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле traffic_over_limit_notified добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: traffic_over_limit_notified" in str(e):
            logging.info("Поле traffic_over_limit_notified уже существует в v2ray_keys")
        else:
            raise
    try:
        cursor.execute(
            """
            UPDATE v2ray_keys
            SET traffic_limit_mb = COALESCE(
                (SELECT traffic_limit_mb FROM tariffs WHERE tariffs.id = v2ray_keys.tariff_id),
                traffic_limit_mb,
                0
            )
            """
        )
        conn.commit()
        logging.info("Данные traffic_limit_mb в v2ray_keys синхронизированы с тарифами")
    except Exception as e:
        logging.warning(f"Не удалось выполнить бэкфилл traffic_limit_mb для v2ray_keys: {e}")
    try:
        cursor.execute(
            """
            UPDATE keys
            SET traffic_limit_mb = COALESCE(
                (SELECT traffic_limit_mb FROM tariffs WHERE tariffs.id = keys.tariff_id),
                traffic_limit_mb,
                0
            )
            """
        )
        conn.commit()
        logging.info("Данные traffic_limit_mb в keys синхронизированы с тарифами")
    except Exception as e:
        logging.warning(f"Не удалось выполнить бэкфилл traffic_limit_mb для keys: {e}")
    finally:
        conn.close()

def migrate_add_available_for_purchase_to_servers():
    """Добавление поля available_for_purchase в servers для управления доступностью серверов к покупке"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN available_for_purchase INTEGER DEFAULT 1")
        conn.commit()
        logging.info("Поле available_for_purchase добавлено в servers")
    except sqlite3.OperationalError:
        logging.info("Поле available_for_purchase уже существует в servers")
    finally:
        conn.close()


def migrate_add_server_cascade_to_keys():
    """Обеспечить каскадное удаление outline-ключей при удалении сервера."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_key_list(keys)")
        fk_list = cursor.fetchall()
        needs_rebuild = True
        for fk in fk_list:
            table, from_col, to_col, on_delete = fk[2], fk[3], fk[4], fk[6]
            if table == 'servers' and from_col == 'server_id':
                needs_rebuild = (on_delete or '').upper() != 'CASCADE'
                break
        else:
            needs_rebuild = True

        if not needs_rebuild:
            logging.info("Таблица keys уже поддерживает каскадное удаление по server_id")
            return

        logging.info("Пересоздаем таблицу keys для добавления каскадного foreign key...")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            """
            CREATE TABLE keys_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                access_url TEXT,
                expiry_at INTEGER,
                traffic_limit_mb INTEGER,
                notified INTEGER DEFAULT 0,
                key_id TEXT,
                created_at INTEGER,
                email TEXT,
                tariff_id INTEGER,
                protocol TEXT DEFAULT 'outline',
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO keys_new (
                id, server_id, user_id, access_url, expiry_at, traffic_limit_mb,
                notified, key_id, created_at, email, tariff_id, protocol
            )
            SELECT
                id, server_id, user_id, access_url, expiry_at, traffic_limit_mb,
                notified, key_id, created_at, email, tariff_id, protocol
            FROM keys
            """
        )
        cursor.execute("DROP TABLE keys")
        cursor.execute("ALTER TABLE keys_new RENAME TO keys")

        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_keys_user_id ON keys(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_keys_expiry_at ON keys(expiry_at)",
            "CREATE INDEX IF NOT EXISTS idx_keys_server_id ON keys(server_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_keys_user_keyid ON keys(user_id, key_id)",
            "CREATE INDEX IF NOT EXISTS idx_keys_created_at ON keys(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_keys_email_created ON keys(email, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_keys_tariff_id ON keys(tariff_id)",
            "CREATE INDEX IF NOT EXISTS idx_keys_email ON keys(email) WHERE email IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_keys_user_expiry ON keys(user_id, expiry_at)",
            "CREATE INDEX IF NOT EXISTS idx_keys_server_expiry ON keys(server_id, expiry_at)",
        ]
        for stmt in index_statements:
            cursor.execute(stmt)

        conn.commit()
        logging.info("Таблица keys успешно обновлена для поддержки каскадного удаления")
    except Exception as e:
        logging.error("Ошибка при добавлении каскадного удаления для keys: %s", e, exc_info=True)
        conn.rollback()
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        conn.close()

def migrate_add_subscriptions_table():
    """Создание таблицы subscriptions для подписок V2Ray"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subscription_token TEXT UNIQUE NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                tariff_id INTEGER,
                is_active INTEGER DEFAULT 1,
                last_updated_at INTEGER,
                notified INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
            )
        """)
        conn.commit()
        logging.info("Таблица subscriptions создана")
    except sqlite3.OperationalError as e:
        if "already exists" in str(e).lower():
            logging.info("Таблица subscriptions уже существует")
        else:
            raise
    finally:
        conn.close()

def migrate_add_subscription_id_to_v2ray_keys():
    """Добавление поля subscription_id в v2ray_keys для связи с подписками"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE v2ray_keys ADD COLUMN subscription_id INTEGER")
        conn.commit()
        logging.info("Поле subscription_id добавлено в v2ray_keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: subscription_id" in str(e).lower():
            logging.info("Поле subscription_id уже существует в v2ray_keys")
        else:
            raise
    finally:
        conn.close()

def migrate_add_subscription_id_to_keys():
    """Добавление поля subscription_id в keys для связи с подписками (outline ключи)"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN subscription_id INTEGER")
        conn.commit()
        logging.info("Поле subscription_id добавлено в keys")
    except sqlite3.OperationalError as e:
        if "duplicate column name: subscription_id" in str(e).lower():
            logging.info("Поле subscription_id уже существует в keys")
        else:
            raise
    finally:
        conn.close()

def migrate_add_subscription_traffic_limits():
    """Добавление полей для контроля трафика подписок"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # Проверяем существующие колонки
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if 'traffic_usage_bytes' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_usage_bytes INTEGER DEFAULT 0")
            conn.commit()
            logging.info("Поле traffic_usage_bytes добавлено в subscriptions")
        
        if 'traffic_over_limit_at' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_at INTEGER")
            conn.commit()
            logging.info("Поле traffic_over_limit_at добавлено в subscriptions")
        
        if 'traffic_over_limit_notified' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_notified INTEGER DEFAULT 0")
            conn.commit()
            logging.info("Поле traffic_over_limit_notified добавлено в subscriptions")
        
        if 'traffic_limit_mb' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_limit_mb INTEGER DEFAULT 0")
            conn.commit()
            logging.info("Поле traffic_limit_mb добавлено в subscriptions")
        
        # Создать таблицу snapshots для отслеживания дельт
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_traffic_snapshots (
                subscription_id INTEGER PRIMARY KEY,
                total_bytes INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            )
        """)
        conn.commit()
        logging.info("Таблица subscription_traffic_snapshots создана")
        
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            logging.info("Поля для трафика подписок уже существуют")
        else:
            logging.error(f"Ошибка миграции subscription_traffic_limits: {e}")
            raise
    finally:
        conn.close()

def migrate_remove_traffic_limit_fields_from_v2ray_keys():
    """Удалить поля traffic_over_limit_at и traffic_over_limit_notified из v2ray_keys"""
    import logging
    logging.info("Миграция: удаление полей traffic_over_limit_at и traffic_over_limit_notified из v2ray_keys")
    
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    cursor = conn.cursor()
    
    try:
        # Проверяем, существуют ли поля
        cursor.execute("PRAGMA table_info(v2ray_keys)")
        columns = [row[1] for row in cursor.fetchall()]
        
        has_over_limit_at = 'traffic_over_limit_at' in columns
        has_over_limit_notified = 'traffic_over_limit_notified' in columns
        
        if not has_over_limit_at and not has_over_limit_notified:
            logging.info("Поля traffic_over_limit_at и traffic_over_limit_notified уже отсутствуют в v2ray_keys")
            conn.close()
            return
        
        # SQLite не поддерживает DROP COLUMN до версии 3.35.0
        # Пересоздаем таблицу без этих полей
        logging.info("Пересоздание таблицы v2ray_keys без полей traffic_over_limit_at и traffic_over_limit_notified")
        
        # Создаем временную таблицу с новой структурой
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS v2ray_keys_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                v2ray_uuid TEXT UNIQUE,
                email TEXT,
                level INTEGER DEFAULT 0,
                created_at INTEGER,
                expiry_at INTEGER,
                tariff_id INTEGER,
                client_config TEXT DEFAULT NULL,
                notified INTEGER DEFAULT 0,
                traffic_limit_mb INTEGER DEFAULT 0,
                traffic_usage_bytes INTEGER DEFAULT 0,
                subscription_id INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
            )
        """)
        
        # Копируем данные (исключаем удаляемые поля)
        cursor.execute("""
            INSERT INTO v2ray_keys_new (
                id, server_id, user_id, v2ray_uuid, email, level, created_at, expiry_at,
                tariff_id, client_config, notified, traffic_limit_mb, traffic_usage_bytes, subscription_id
            )
            SELECT 
                id, server_id, user_id, v2ray_uuid, email, level, created_at, expiry_at,
                tariff_id, client_config, notified, traffic_limit_mb, traffic_usage_bytes, subscription_id
            FROM v2ray_keys
        """)
        
        # Удаляем старую таблицу
        cursor.execute("DROP TABLE v2ray_keys")
        
        # Переименовываем новую таблицу
        cursor.execute("ALTER TABLE v2ray_keys_new RENAME TO v2ray_keys")
        
        # Восстанавливаем индексы
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_user_id ON v2ray_keys(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_server_id ON v2ray_keys(server_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_subscription_id ON v2ray_keys(subscription_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_expiry_at ON v2ray_keys(expiry_at)")
        
        conn.commit()
        logging.info("Поля traffic_over_limit_at и traffic_over_limit_notified успешно удалены из v2ray_keys")
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка миграции удаления полей из v2ray_keys: {e}")
        raise
    finally:
        conn.close()


def migrate_remove_traffic_snapshot_tables():
    """Удалить таблицы snapshots для трафика"""
    import logging
    logging.info("Миграция: удаление таблиц snapshots для трафика")
    
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    cursor = conn.cursor()
    
    try:
        # Удаляем таблицу v2ray_usage_snapshots
        cursor.execute("DROP TABLE IF EXISTS v2ray_usage_snapshots")
        logging.info("Таблица v2ray_usage_snapshots удалена")
        
        # Удаляем таблицу subscription_traffic_snapshots
        cursor.execute("DROP TABLE IF EXISTS subscription_traffic_snapshots")
        logging.info("Таблица subscription_traffic_snapshots удалена")
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка миграции удаления таблиц snapshots: {e}")
        raise
    finally:
        conn.close()


def migrate_add_purchase_notification_sent():
    """Добавление поля purchase_notification_sent для отслеживания отправки уведомлений о покупке подписки"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE subscriptions ADD COLUMN purchase_notification_sent INTEGER DEFAULT 0")
        conn.commit()
        logging.info("Поле purchase_notification_sent добавлено в subscriptions")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower() or "duplicate column name" in str(e).lower():
            logging.info("Поле purchase_notification_sent уже существует в subscriptions")
        else:
            logging.error(f"Ошибка миграции purchase_notification_sent: {e}")
            raise
    finally:
        conn.close()


def migrate_add_subscription_indexes():
    """Создание индексов для таблицы subscriptions"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_token ON subscriptions(subscription_token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_expires_at ON subscriptions(expires_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_is_active ON subscriptions(is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_v2ray_keys_subscription_id ON v2ray_keys(subscription_id)")
        conn.commit()
        logging.info("Индексы для подписок созданы")
    except sqlite3.OperationalError as e:
        logging.warning(f"Ошибка при создании индексов для подписок: {e}")
    finally:
        conn.close()

def migrate_fix_v2ray_keys_foreign_keys():
    """Исправление foreign keys в v2ray_keys: каскад по server_id и связь с users(user_id)."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_key_list(v2ray_keys)")
        fk_list = cursor.fetchall()

        def _find_fk(table_name: str, column: str):
            for fk in fk_list:
                if fk[2] == table_name and fk[3] == column:
                    return fk
            return None

        server_fk = _find_fk('servers', 'server_id')
        user_fk = _find_fk('users', 'user_id')

        needs_rebuild = False
        if server_fk is None or (server_fk[6] or '').upper() != 'CASCADE':
            needs_rebuild = True
        if user_fk is None or user_fk[4] != 'user_id':
            needs_rebuild = True

        if not needs_rebuild:
            logging.info("Foreign key constraints в v2ray_keys уже корректны")
            return

        logging.info("Пересоздаем таблицу v2ray_keys для корректных foreign keys...")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            """
            CREATE TABLE v2ray_keys_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                v2ray_uuid TEXT UNIQUE,
                email TEXT,
                level INTEGER DEFAULT 0,
                created_at INTEGER,
                expiry_at INTEGER,
                tariff_id INTEGER,
                client_config TEXT DEFAULT NULL,
                notified INTEGER DEFAULT 0,
                traffic_limit_mb INTEGER DEFAULT 0,
                traffic_usage_bytes INTEGER DEFAULT 0,
                traffic_over_limit_at INTEGER,
                traffic_over_limit_notified INTEGER DEFAULT 0,
                subscription_id INTEGER,
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO v2ray_keys_new (
                id, server_id, user_id, v2ray_uuid, email, level,
                created_at, expiry_at, tariff_id, client_config, notified,
                traffic_limit_mb, traffic_usage_bytes, traffic_over_limit_at,
                traffic_over_limit_notified, subscription_id
            )
            SELECT
                id, server_id, user_id, v2ray_uuid, email, level,
                created_at, expiry_at, tariff_id, client_config, notified,
                traffic_limit_mb, traffic_usage_bytes, traffic_over_limit_at,
                traffic_over_limit_notified, subscription_id
            FROM v2ray_keys
            """
        )
        cursor.execute("DROP TABLE v2ray_keys")
        cursor.execute("ALTER TABLE v2ray_keys_new RENAME TO v2ray_keys")

        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_user_id ON v2ray_keys(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_expiry_at ON v2ray_keys(expiry_at)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_server_id ON v2ray_keys(server_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_v2ray_user_uuid ON v2ray_keys(user_id, v2ray_uuid)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_created_at ON v2ray_keys(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_email ON v2ray_keys(email) WHERE email IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_email_created ON v2ray_keys(email, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_tariff_id ON v2ray_keys(tariff_id)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_user_expiry ON v2ray_keys(user_id, expiry_at)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_server_expiry ON v2ray_keys(server_id, expiry_at)",
            "CREATE INDEX IF NOT EXISTS idx_v2ray_keys_subscription_id ON v2ray_keys(subscription_id)",
        ]
        for stmt in index_statements:
            cursor.execute(stmt)

        conn.commit()
        logging.info("Таблица v2ray_keys успешно обновлена с корректными foreign keys")
    except Exception as e:
        logging.error("Ошибка при исправлении foreign keys в v2ray_keys: %s", e, exc_info=True)
        conn.rollback()
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        conn.close()

def migrate_fix_traffic_stats_foreign_keys():
    """Исправление foreign keys в traffic_stats: каскад по server_id и правильная ссылка на users(user_id)."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        # Check if traffic_stats exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='traffic_stats'")
        if not cursor.fetchone():
            logging.info("Таблица traffic_stats не существует, пропускаем миграцию")
            return

        cursor.execute("PRAGMA foreign_key_list(traffic_stats)")
        fk_list = cursor.fetchall()

        def _find_fk(table_name: str, column: str):
            for fk in fk_list:
                if fk[2] == table_name and fk[3] == column:
                    return fk
            return None

        server_fk = _find_fk('servers', 'server_id')
        user_fk = _find_fk('users', 'user_id')

        needs_rebuild = False
        # Check if server_id FK needs CASCADE
        if server_fk is None or (server_fk[6] or '').upper() != 'CASCADE':
            needs_rebuild = True
        # Check if user_id FK references correct column (user_id, not id)
        if user_fk is None or user_fk[4] != 'user_id':
            needs_rebuild = True

        if not needs_rebuild:
            logging.info("Foreign key constraints в traffic_stats уже корректны")
            return

        logging.info("Пересоздаем таблицу traffic_stats для корректных foreign keys...")
        cursor.execute("PRAGMA foreign_keys=OFF")
        
        # Get all data to preserve
        cursor.execute("SELECT * FROM traffic_stats")
        data = cursor.fetchall()
        column_count = len([c[0] for c in cursor.description]) if cursor.description else 0
        
        cursor.execute(
            """
            CREATE TABLE traffic_stats_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                server_id INTEGER,
                protocol TEXT NOT NULL,
                key_id TEXT,
                upload_bytes BIGINT DEFAULT 0,
                download_bytes BIGINT DEFAULT 0,
                connections INTEGER DEFAULT 0,
                last_seen INTEGER,
                updated_at INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
            )
            """
        )
        
        # Restore data if any
        if data and column_count > 0:
            placeholders = ",".join(["?"] * column_count)
            cursor.executemany(
                f"INSERT INTO traffic_stats_new VALUES ({placeholders})",
                data
            )
        
        cursor.execute("DROP TABLE traffic_stats")
        cursor.execute("ALTER TABLE traffic_stats_new RENAME TO traffic_stats")

        conn.commit()
        logging.info("Таблица traffic_stats успешно обновлена с корректными foreign keys")
    except Exception as e:
        logging.error("Ошибка при исправлении foreign keys в traffic_stats: %s", e, exc_info=True)
        conn.rollback()
    finally:
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        conn.close()

def _run_all_migrations():
    """Выполнить все миграции базы данных"""
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
    migrate_create_dashboard_metrics_table()
    migrate_create_webhook_logs()
    migrate_create_users_table()
    migrate_backfill_users()
    migrate_add_crypto_pricing()
    migrate_add_crypto_payment_fields()
    migrate_add_client_config_to_v2ray_keys()
    migrate_add_notified_to_v2ray_keys()
    migrate_add_traffic_monitoring_to_v2ray_keys()
    migrate_add_available_for_purchase_to_servers()
    migrate_add_server_cascade_to_keys()
    migrate_fix_traffic_stats_foreign_keys()
    migrate_add_subscriptions_table()
    migrate_add_subscription_id_to_v2ray_keys()
    migrate_add_subscription_id_to_keys()
    migrate_add_subscription_indexes()
    migrate_add_subscription_traffic_limits()
    migrate_add_purchase_notification_sent()
    migrate_remove_traffic_limit_fields_from_v2ray_keys()
    migrate_fix_v2ray_keys_foreign_keys()  # Исправляем foreign keys после всех миграций, изменяющих структуру v2ray_keys
    migrate_remove_traffic_snapshot_tables()

# Выполняем миграции после определения всех функций
# Это нужно для того, чтобы init_db() могла вызывать миграции
def init_db_with_migrations():
    """Инициализация БД с выполнением всех миграций"""
    init_db()
    _run_all_migrations()

if __name__ == "__main__":
    import time
    init_db_with_migrations()

import sqlite3

def init_db():
    conn = sqlite3.connect("vpn.db")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS servers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        api_url TEXT,
        cert_sha256 TEXT,
        max_keys INTEGER DEFAULT 100,
        active INTEGER DEFAULT 1
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
        email TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        tariff_id INTEGER,
        payment_id TEXT,
        status TEXT DEFAULT 'pending',
        email TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        created_at INTEGER,
        bonus_issued INTEGER DEFAULT 0
    )""")

    conn.commit()
    conn.close()

def migrate_add_key_id():
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN key_id TEXT")
        conn.commit()
        print("Поле key_id успешно добавлено.")
    except sqlite3.OperationalError:
        print("Поле key_id уже существует.")
    conn.close()

def migrate_add_email():
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN email TEXT")
        conn.commit()
        print("Поле email успешно добавлено в таблицу keys.")
    except sqlite3.OperationalError:
        print("Поле email уже существует в таблице keys.")
    conn.close()

def migrate_add_payment_email():
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE payments ADD COLUMN email TEXT")
        conn.commit()
        print("Поле email успешно добавлено в таблицу payments.")
    except sqlite3.OperationalError:
        print("Поле email уже существует в таблице payments.")
    conn.close()

def migrate_add_tariff_id_to_keys():
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE keys ADD COLUMN tariff_id INTEGER")
        conn.commit()
        print("Поле tariff_id успешно добавлено в таблицу keys.")
    except sqlite3.OperationalError:
        print("Поле tariff_id уже существует в таблице keys.")
    conn.close()

def migrate_add_country_to_servers():
    conn = sqlite3.connect("vpn.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE servers ADD COLUMN country TEXT")
        conn.commit()
        print("Поле country успешно добавлено в таблицу servers.")
    except sqlite3.OperationalError:
        print("Поле country уже существует в таблице servers.")
    conn.close()

if __name__ == "__main__":
    init_db()
    migrate_add_key_id()
    migrate_add_email()
    migrate_add_payment_email()
    migrate_add_tariff_id_to_keys()
    migrate_add_country_to_servers()


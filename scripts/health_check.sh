#!/bin/bash
# Скрипт для проверки здоровья системы VeilBot

# Переменные
BOT_SERVICE="veilbot"
ADMIN_SERVICE="veilbot-admin"
NGINX_SERVICE="nginx"
DB_PATH="/root/veilbot/vpn.db"
LOG_DIR="${VEILBOT_LOG_DIR:-/var/log/veilbot}"
mkdir -p "$LOG_DIR" >/dev/null 2>&1 || true
LOG_FILE="$LOG_DIR/health_check.log"

exec >> "$LOG_FILE" 2>&1

overall_status=0 # 0 = HEALTHY, 1 = ISSUES DETECTED

echo "=== VeilBot Health Check ==="
echo "Time: $(date)"
echo "Hostname: $(hostname)"
echo "Uptime: $(uptime | awk '{print $3,$4,$5,$6,$7,$8,$9}')"
echo ""

# Функция для проверки статуса службы
check_service() {
    SERVICE_NAME=$1
    echo -n "--- Service Status ---"
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "✅ $SERVICE_NAME is running"
    else
        echo "❌ $SERVICE_NAME is NOT running"
        overall_status=1
    fi
}

# Функция для проверки порта
check_port() {
    PORT=$1
    if ss -tuln | grep -q ":$PORT "; then
        echo "✅ Port $PORT is listening"
    else
        echo "❌ Port $PORT is not listening"
        overall_status=1
    fi
}

# Функция для проверки использования диска
check_disk() {
    DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ "$DISK_USAGE" -lt 90 ]; then
        echo "✅ Disk usage: $DISK_USAGE%"
    else
        echo "❌ Disk usage: $DISK_USAGE% (HIGH)"
        overall_status=1
    fi
}

# Функция для проверки использования памяти
check_memory() {
    MEMORY_USAGE=$(free | awk '/Mem:/ {printf("%d%%\n", $3/$2*100)}')
    MEMORY_PERCENT=$(echo "$MEMORY_USAGE" | sed 's/%//')
    if [ "$MEMORY_PERCENT" -lt 90 ]; then
        echo "✅ Memory usage: $MEMORY_USAGE"
    else
        echo "❌ Memory usage: $MEMORY_USAGE (HIGH)"
        overall_status=1
    fi
}

# Функция для проверки размера логов
check_log_size() {
    LOG_FILE=$1
    MAX_SIZE_MB=$2
    if [ -f "$LOG_FILE" ]; then
        SIZE_KB=$(du -k "$LOG_FILE" | awk '{print $1}')
        SIZE_MB=$((SIZE_KB / 1024))
        if [ "$SIZE_MB" -lt "$MAX_SIZE_MB" ]; then
            echo "✅ $(basename "$LOG_FILE") size OK ($SIZE_KB bytes)"
        else
            echo "⚠️ $(basename "$LOG_FILE") size HIGH ($SIZE_MB MB)"
            overall_status=1
        fi
    else
        echo "❌ $(basename "$LOG_FILE") not found"
        overall_status=1
    fi
}

# Функция для проверки количества процессов
check_process_count() {
    PROCESS_NAME=$1
    MIN_COUNT=$2
    COUNT=$(pgrep -f "$PROCESS_NAME" | wc -l)
    if [ "$COUNT" -ge "$MIN_COUNT" ]; then
        echo "✅ $PROCESS_NAME processes running: $COUNT"
    else
        echo "❌ $PROCESS_NAME processes running: $COUNT (LOW)"
        overall_status=1
    fi
}

# Выполнение проверок
echo "--- Service Status ---"
check_service "$BOT_SERVICE" || overall_status=1
check_service "$ADMIN_SERVICE" || overall_status=1
check_service "$NGINX_SERVICE" || overall_status=1
echo ""

# Проверка портов
echo "--- Port Status ---"
check_port 80 || overall_status=1
check_port 443 || overall_status=1
check_port 8001 || overall_status=1
echo ""

# Проверка ресурсов
echo "--- Resource Status ---"
check_disk || overall_status=1
check_memory || overall_status=1
echo ""

# Проверка данных
echo "--- Data Status ---"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -b "$DB_PATH" | awk '{print $1}')
    echo "✅ Database exists and has data ($DB_SIZE bytes)"
else
    echo "❌ Database not found at $DB_PATH"
    overall_status=1
fi
check_log_size "$LOG_DIR/bot.log" 100 || overall_status=1
check_log_size "$LOG_DIR/admin_audit.log" 10 || overall_status=1
check_log_size "$LOG_DIR/veilbot_security.log" 50 || overall_status=1
echo ""

# Проверка health-check эндпоинта админки
echo "--- HTTP Health Check ---"
if curl -sf "http://127.0.0.1:8001/healthz" >/dev/null; then
    echo "✅ Admin health endpoint responded"
else
    echo "❌ Admin health endpoint is not responding"
    overall_status=1
fi
echo ""

# Проверка процессов
echo "--- Process Status ---"
check_process_count "bot.main" 1 || overall_status=1
check_process_count "admin.main:app" 1 || overall_status=1
echo ""

# Итоговый статус
if [ "$overall_status" -eq 0 ]; then
    echo "🎉 Overall Status: HEALTHY"
else
    echo "⚠️ Overall Status: ISSUES DETECTED"
fi
echo "=== End Health Check ==="

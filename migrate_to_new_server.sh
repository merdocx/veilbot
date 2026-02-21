#!/bin/bash
# Скрипт для копирования проекта VeilBot на новый сервер
# Использование: ./migrate_to_new_server.sh user@new-server-ip

set -e

if [ -z "$1" ]; then
    echo "Использование: $0 user@new-server-ip"
    echo "Пример: $0 root@46.151.31.105"
    exit 1
fi

NEW_SERVER="$1"
PROJECT_DIR="/root/veilbot"

echo "=========================================="
echo "Миграция VeilBot на новый сервер"
echo "Новый сервер: $NEW_SERVER"
echo "=========================================="
echo ""

# Проверка подключения к новому серверу
echo "[1/5] Проверка подключения к новому серверу..."
if ssh -o ConnectTimeout=5 "$NEW_SERVER" "echo 'Connection OK'" 2>/dev/null; then
    echo "✓ Подключение успешно"
else
    echo "✗ Не удалось подключиться к $NEW_SERVER"
    echo "Проверьте:"
    echo "  - SSH доступ настроен"
    echo "  - IP адрес правильный"
    echo "  - Пользователь имеет доступ"
    exit 1
fi

# Создание директории на новом сервере
echo ""
echo "[2/5] Создание директории на новом сервере..."
ssh "$NEW_SERVER" "sudo mkdir -p $PROJECT_DIR && sudo chown \$(whoami):\$(whoami) $PROJECT_DIR" || {
    echo "✗ Не удалось создать директорию"
    exit 1
}
echo "✓ Директория создана"

# Определение пути к БД
DB_PATH=$(cd "$PROJECT_DIR" && python3 -c "from app.settings import settings; print(settings.DATABASE_PATH)" 2>/dev/null || echo "vpn.db")
echo ""
echo "[3/5] Информация о проекте:"
echo "  - Путь к БД: $DB_PATH"
echo "  - Размер проекта: $(du -sh "$PROJECT_DIR" | cut -f1)"
echo "  - Размер БД: $(du -h "$PROJECT_DIR/$DB_PATH" 2>/dev/null | cut -f1 || echo 'не найдена')"

# Копирование проекта через rsync
echo ""
echo "[4/5] Копирование проекта через rsync..."
echo "Это может занять несколько минут..."
rsync -avz --progress \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='htmlcov' \
  --exclude='.coverage' \
  --exclude='*.log' \
  "$PROJECT_DIR/" "$NEW_SERVER:$PROJECT_DIR/"

if [ $? -eq 0 ]; then
    echo "✓ Проект скопирован успешно"
else
    echo "✗ Ошибка при копировании"
    exit 1
fi

# Проверка скопированных файлов
echo ""
echo "[5/5] Проверка скопированных файлов на новом сервере..."
ssh "$NEW_SERVER" "cd $PROJECT_DIR && \
  if [ -f .env ]; then echo '✓ .env скопирован'; else echo '✗ .env НЕ найден!'; fi && \
  if [ -f '$DB_PATH' ]; then echo '✓ БД скопирована: $DB_PATH'; else echo '⚠ БД не найдена (будет скопирована на этапе финальной синхронизации)'; fi && \
  echo 'Размер проекта: ' && du -sh $PROJECT_DIR"

echo ""
echo "=========================================="
echo "Копирование завершено!"
echo "=========================================="
echo ""
echo "Следующие шаги на новом сервере:"
echo "  1. cd $PROJECT_DIR"
echo "  2. python3.11 -m pip install -r requirements.txt"
echo "  3. Настроить systemd сервисы (см. инструкцию)"
echo "  4. На этапе финальной синхронизации скопировать финальный бэкап БД"

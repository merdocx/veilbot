#!/bin/bash

BOT_NAME="VeilBot"
BOT_PROCESS="python3 bot.py"
LOG_DIR="logs"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода с цветом
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# Функция для получения PID бота
get_bot_pid() {
    pgrep -f "$BOT_PROCESS"
}

# Функция для проверки статуса
status() {
    echo -e "${BLUE}🔍 Проверка состояния $BOT_NAME...${NC}"
    
    BOT_PID=$(get_bot_pid)
    
    if [ -z "$BOT_PID" ]; then
        print_error "$BOT_NAME не запущен"
        return 1
    fi
    
    print_status "$BOT_NAME запущен (PID: $BOT_PID)"
    
    # Информация о процессе
    PROCESS_INFO=$(ps -o pid,ppid,cmd,%mem,%cpu --no-headers -p $BOT_PID)
    echo "💾 Использование ресурсов:"
    echo "$PROCESS_INFO"
    
    # Последние логи
    if [ -d "$LOG_DIR" ]; then
        LATEST_LOG=$(ls -t $LOG_DIR/bot_*.log 2>/dev/null | head -1)
        if [ -n "$LATEST_LOG" ]; then
            echo ""
            print_info "Последние логи:"
            echo "📁 $LATEST_LOG"
            echo "--- Последние 5 строк ---"
            tail -5 "$LATEST_LOG"
        fi
    fi
    
    return 0
}

# Функция для запуска
start() {
    echo -e "${BLUE}🚀 Запуск $BOT_NAME...${NC}"
    
    # Проверка, не запущен ли уже бот
    if get_bot_pid > /dev/null; then
        print_warning "$BOT_NAME уже запущен"
        status
        return 1
    fi
    
    # Проверка файлов
    if [ ! -f "bot.py" ]; then
        print_error "Файл bot.py не найден!"
        return 1
    fi
    
    if [ ! -f ".env" ]; then
        print_error "Файл .env не найден!"
        return 1
    fi
    
    # Создание директории для логов
    mkdir -p $LOG_DIR
    
    # Запуск бота
    nohup $BOT_PROCESS > $LOG_DIR/bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &
    BOT_PID=$!
    
    # Проверка запуска
    sleep 3
    if ps -p $BOT_PID > /dev/null; then
        print_status "$BOT_NAME успешно запущен (PID: $BOT_PID)"
        return 0
    else
        print_error "Ошибка запуска $BOT_NAME"
        return 1
    fi
}

# Функция для остановки
stop() {
    echo -e "${BLUE}🛑 Остановка $BOT_NAME...${NC}"
    
    BOT_PID=$(get_bot_pid)
    
    if [ -z "$BOT_PID" ]; then
        print_warning "$BOT_NAME не запущен"
        return 0
    fi
    
    print_info "Останавливаю процесс PID: $BOT_PID"
    kill -TERM $BOT_PID
    
    # Ждем завершения
    sleep 2
    
    # Принудительная остановка, если нужно
    if ps -p $BOT_PID > /dev/null; then
        print_warning "Принудительная остановка..."
        kill -KILL $BOT_PID
        sleep 1
    fi
    
    if ps -p $BOT_PID > /dev/null; then
        print_error "Не удалось остановить $BOT_NAME"
        return 1
    else
        print_status "$BOT_NAME успешно остановлен"
        return 0
    fi
}

# Функция для перезапуска
restart() {
    echo -e "${BLUE}🔄 Перезапуск $BOT_NAME...${NC}"
    
    stop
    sleep 2
    start
}

# Функция для просмотра логов
logs() {
    if [ -d "$LOG_DIR" ]; then
        LATEST_LOG=$(ls -t $LOG_DIR/bot_*.log 2>/dev/null | head -1)
        if [ -n "$LATEST_LOG" ]; then
            echo -e "${BLUE}📋 Просмотр логов $BOT_NAME:${NC}"
            echo "📁 $LATEST_LOG"
            echo "--- Нажмите Ctrl+C для выхода ---"
            tail -f "$LATEST_LOG"
        else
            print_error "Логи не найдены"
        fi
    else
        print_error "Директория логов не найдена"
    fi
}

# Функция для очистки логов
clean_logs() {
    echo -e "${BLUE}🧹 Очистка старых логов...${NC}"
    
    if [ -d "$LOG_DIR" ]; then
        # Удаляем логи старше 7 дней
        find $LOG_DIR -name "bot_*.log" -mtime +7 -delete
        print_status "Старые логи очищены"
    else
        print_warning "Директория логов не найдена"
    fi
}

# Функция для помощи
help() {
    echo -e "${BLUE}📖 Управление $BOT_NAME${NC}"
    echo ""
    echo "Использование: $0 [команда]"
    echo ""
    echo "Команды:"
    echo "  start     - Запустить бота"
    echo "  stop      - Остановить бота"
    echo "  restart   - Перезапустить бота"
    echo "  status    - Показать статус бота"
    echo "  logs      - Показать логи в реальном времени"
    echo "  clean     - Очистить старые логи"
    echo "  help      - Показать эту справку"
    echo ""
}

# Основная логика
case "${1:-help}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    clean)
        clean_logs
        ;;
    help|*)
        help
        ;;
esac


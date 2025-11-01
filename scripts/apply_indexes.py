#!/usr/bin/env python3
"""
Скрипт для применения индексов к существующей базе данных.
Выполняет migrate_add_common_indexes() для улучшения производительности запросов.
"""
import sys
import os
import logging

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import migrate_add_common_indexes
from config import DATABASE_PATH

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def main():
    """Применить индексы к базе данных"""
    if not os.path.exists(DATABASE_PATH):
        logging.error(f"База данных не найдена: {DATABASE_PATH}")
        return 1
    
    logging.info(f"Применение индексов к базе данных: {DATABASE_PATH}")
    try:
        migrate_add_common_indexes()
        logging.info("Индексы успешно применены!")
        return 0
    except Exception as e:
        logging.error(f"Ошибка при применении индексов: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())


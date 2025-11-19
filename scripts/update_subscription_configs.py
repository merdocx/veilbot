#!/usr/bin/env python3
"""
Скрипт для обновления конфигураций подписок - удаление фрагментов (email) и применение названий серверов
"""
import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.subscription_service import update_subscription_configs_remove_fragments
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Обновить конфигурации подписок"""
    logger.info("Starting subscription configs update...")
    
    try:
        update_subscription_configs_remove_fragments()
        logger.info("✅ Successfully updated subscription configs")
        print("\n✅ Конфигурации подписок успешно обновлены!")
        print("   Все активные подписки теперь будут использовать названия серверов из админки.")
        print("   Пользователям нужно обновить подписку в боте (кнопка '🔄 Обновить подписку').")
    except Exception as e:
        logger.error(f"❌ Error updating subscription configs: {e}", exc_info=True)
        print(f"\n❌ Ошибка при обновлении конфигураций: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


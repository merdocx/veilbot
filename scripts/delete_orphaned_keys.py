#!/usr/bin/env python3
"""Удалить с серверов все ключи, которые отсутствуют в базе данных."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.compare_keys import compare_servers, extract_v2ray_uuid  # noqa: E402
from vpn_protocols import V2RayProtocol  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def delete_orphaned_keys(dry_run: bool = False) -> Dict[str, Any]:
    """
    Удалить с серверов все ключи, которые отсутствуют в БД.
    
    Args:
        dry_run: Если True, только показать что будет удалено, не удалять реально
    
    Returns:
        Словарь со статистикой удаления
    """
    logger.info("Начинаю сравнение ключей между БД и серверами...")
    results = await compare_servers()
    
    total_deleted = 0
    total_failed = 0
    total_skipped = 0
    deleted_by_server: Dict[str, int] = {}
    failed_by_server: Dict[str, int] = {}
    
    for result in results:
        server_name = result.server.name
        server_id = result.server.id
        
        if result.errors:
            logger.warning(f"Сервер {server_name} (ID {server_id}): пропущен из-за ошибок: {result.errors}")
            total_skipped += 1
            continue
        
        if not result.missing_in_db:
            logger.info(f"Сервер {server_name} (ID {server_id}): нет ключей для удаления")
            continue
        
        logger.info(f"Сервер {server_name} (ID {server_id}): найдено {len(result.missing_in_db)} ключей для удаления")
        
        deleted_count = 0
        failed_count = 0
        
        if result.server.protocol != "v2ray":
            logger.warning(
                f"Сервер {server_name}: ожидается v2ray, получено {result.server.protocol}"
            )
            continue

        client = V2RayProtocol(result.server.api_url, result.server.api_key or "")

        try:
            for item in result.missing_in_db:
                remote_key = item.get("remote_key", {})
                hint = item.get("matching_hint", {})

                uuid = hint.get("uuid") or extract_v2ray_uuid(remote_key)
                key_id = uuid or remote_key.get("id")
                key_display = f"UUID: {uuid[:20]}..." if uuid else f"ID: {key_id[:20]}..."
                
                if not key_id:
                    logger.warning(f"Сервер {server_name}: не удалось определить ID ключа для удаления")
                    failed_count += 1
                    continue
                
                if dry_run:
                    logger.info(f"  [DRY RUN] Будет удален ключ: {key_display}")
                    deleted_count += 1
                else:
                    try:
                        logger.info(f"  Удаляю ключ: {key_display}")
                        success = await client.delete_user(str(key_id))
                        if success:
                            logger.info(f"  ✓ Успешно удален: {key_display}")
                            deleted_count += 1
                        else:
                            logger.warning(f"  ✗ Не удалось удалить: {key_display}")
                            failed_count += 1
                    except Exception as e:
                        logger.error(f"  ✗ Ошибка при удалении {key_display}: {e}", exc_info=True)
                        failed_count += 1
        
        finally:
            try:
                await client.close()
            except Exception:
                pass
        
        deleted_by_server[server_name] = deleted_count
        if failed_count > 0:
            failed_by_server[server_name] = failed_count
        
        total_deleted += deleted_count
        total_failed += failed_count
        
        logger.info(f"Сервер {server_name}: удалено {deleted_count}, ошибок {failed_count}")
    
    summary = {
        "total_deleted": total_deleted,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
        "deleted_by_server": deleted_by_server,
        "failed_by_server": failed_by_server,
        "dry_run": dry_run,
    }
    
    return summary


def print_summary(summary: Dict[str, Any]) -> None:
    """Вывести итоговый отчет"""
    print("=" * 80)
    print("ИТОГОВЫЙ ОТЧЕТ ОБ УДАЛЕНИИ КЛЮЧЕЙ")
    print("=" * 80)
    print()
    
    if summary["dry_run"]:
        print("⚠️  РЕЖИМ ПРОВЕРКИ (DRY RUN) - ключи не были удалены")
        print()
    
    print(f"📊 Статистика:")
    print(f"   Удалено ключей: {summary['total_deleted']}")
    print(f"   Ошибок при удалении: {summary['total_failed']}")
    print(f"   Серверов пропущено: {summary['total_skipped']}")
    print()
    
    if summary["deleted_by_server"]:
        print("✅ Удалено по серверам:")
        for server_name, count in summary["deleted_by_server"].items():
            print(f"   {server_name}: {count} ключей")
        print()
    
    if summary["failed_by_server"]:
        print("❌ Ошибки по серверам:")
        for server_name, count in summary["failed_by_server"].items():
            print(f"   {server_name}: {count} ошибок")
        print()
    
    print("=" * 80)


async def main() -> None:
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Удалить с серверов ключи, отсутствующие в БД")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Режим проверки: показать что будет удалено, но не удалять реально"
    )
    
    args = parser.parse_args()
    
    if args.dry_run:
        print("⚠️  РЕЖИМ ПРОВЕРКИ (DRY RUN)")
        print("Ключи не будут удалены, только показано что будет сделано")
        print()
    
    try:
        summary = await delete_orphaned_keys(dry_run=args.dry_run)
        print_summary(summary)
        
        if not args.dry_run and summary["total_deleted"] > 0:
            print("\n✅ Удаление завершено успешно!")
        elif args.dry_run:
            print("\n💡 Для реального удаления запустите скрипт без флага --dry-run")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())






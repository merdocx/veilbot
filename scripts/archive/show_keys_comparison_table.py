#!/usr/bin/env python3
"""Показать сравнительную таблицу ключей между БД и серверами в табличном формате."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.compare_keys import compare_servers, ComparisonResult  # noqa: E402


def format_table(results: List[ComparisonResult]) -> None:
    """Вывести сравнительную таблицу в табличном формате"""
    print("\n" + "=" * 120)
    print("СРАВНИТЕЛЬНАЯ ТАБЛИЦА КЛЮЧЕЙ: БАЗА ДАННЫХ vs СЕРВЕРЫ")
    print("=" * 120)
    print()
    
    # Заголовок таблицы
    header = f"{'Сервер':<25} | {'Протокол':<10} | {'В БД':<8} | {'На сервере':<12} | {'Отсутствует на сервере':<25} | {'Отсутствует в БД':<20} | {'Статус':<15}"
    print(header)
    print("-" * 120)
    
    total_db = 0
    total_remote = 0
    total_missing_on_server = 0
    total_missing_in_db = 0
    synced_count = 0
    
    for res in results:
        server_name = res.server.name[:24]
        protocol = res.server.protocol.upper()
        db_count = res.db_count
        remote_count = res.remote_count
        missing_on_server = len(res.missing_on_server)
        missing_in_db = len(res.missing_in_db)
        
        total_db += db_count
        total_remote += remote_count
        total_missing_on_server += missing_on_server
        total_missing_in_db += missing_in_db
        
        # Определяем статус
        if res.errors:
            status = "❌ ОШИБКА"
        elif missing_on_server == 0 and missing_in_db == 0 and len(res.db_without_remote_id) == 0:
            status = "✅ Синхронизировано"
            synced_count += 1
        elif missing_on_server > 0 or missing_in_db > 0:
            status = "⚠️  Не синхронизировано"
        else:
            status = "⚠️  Проблемы"
        
        row = f"{server_name:<25} | {protocol:<10} | {db_count:<8} | {remote_count:<12} | {missing_on_server:<25} | {missing_in_db:<20} | {status:<15}"
        print(row)
    
    print("-" * 120)
    footer = f"{'ИТОГО':<25} | {'':<10} | {total_db:<8} | {total_remote:<12} | {total_missing_on_server:<25} | {total_missing_in_db:<20} | {synced_count}/{len(results)} синхр."
    print(footer)
    print("=" * 120)
    print()
    
    # Детальная информация по проблемным серверам
    has_issues = any(
        r.missing_on_server or r.missing_in_db or r.db_without_remote_id or r.errors
        for r in results
    )
    
    if has_issues:
        print("\n" + "=" * 120)
        print("ДЕТАЛЬНАЯ ИНФОРМАЦИЯ ПО ПРОБЛЕМАМ")
        print("=" * 120)
        print()
        
        for res in results:
            if res.errors:
                print(f"🔴 Сервер: {res.server.name} (ID: {res.server.id}, {res.server.protocol.upper()})")
                print(f"   Ошибки:")
                for err in res.errors:
                    print(f"      • {err}")
                print()
                continue
            
            if res.missing_on_server:
                print(f"⚠️  Сервер: {res.server.name} (ID: {res.server.id}, {res.server.protocol.upper()})")
                print(f"   Отсутствует на сервере ({len(res.missing_on_server)} ключей):")
                for idx, item in enumerate(res.missing_on_server[:5], 1):
                    db_entry = item.get("db_entry", {})
                    hint = item.get("matching_hint", {})
                    key_id = hint.get("key_id") or hint.get("uuid") or "N/A"
                    email = db_entry.get("email") or hint.get("email") or "N/A"
                    user_id = db_entry.get("user_id", "N/A")
                    print(f"      {idx}. БД ID: {db_entry.get('id', 'N/A')}, User ID: {user_id}, "
                          f"Email: {email[:40]}, Ключ: {str(key_id)[:30]}...")
                if len(res.missing_on_server) > 5:
                    print(f"      ... и еще {len(res.missing_on_server) - 5} ключей")
                print()
            
            if res.missing_in_db:
                print(f"⚠️  Сервер: {res.server.name} (ID: {res.server.id}, {res.server.protocol.upper()})")
                print(f"   Отсутствует в БД ({len(res.missing_in_db)} ключей):")
                for idx, item in enumerate(res.missing_in_db[:5], 1):
                    remote_key = item.get("remote_key", {})
                    hint = item.get("matching_hint", {})
                    uuid = hint.get("uuid") or remote_key.get("uuid") or remote_key.get("id") or "N/A"
                    name = remote_key.get("name") or hint.get("name") or "N/A"
                    print(f"      {idx}. UUID/ID: {str(uuid)[:40]}..., Имя: {name[:40]}")
                if len(res.missing_in_db) > 5:
                    print(f"      ... и еще {len(res.missing_in_db) - 5} ключей")
                print()
            
            if res.db_without_remote_id:
                print(f"⚠️  Сервер: {res.server.name} (ID: {res.server.id}, {res.server.protocol.upper()})")
                print(f"   Ключи в БД без ID сервера ({len(res.db_without_remote_id)} ключей):")
                for idx, item in enumerate(res.db_without_remote_id[:5], 1):
                    key_id = item.get("key_id") or item.get("v2ray_uuid") or "N/A"
                    email = item.get("email") or "N/A"
                    user_id = item.get("user_id", "N/A")
                    print(f"      {idx}. БД ID: {item.get('id', 'N/A')}, User ID: {user_id}, "
                          f"Email: {email[:40]}, Ключ: {str(key_id)[:30]}...")
                if len(res.db_without_remote_id) > 5:
                    print(f"      ... и еще {len(res.db_without_remote_id) - 5} ключей")
                print()


async def main() -> None:
    results = await compare_servers()
    format_table(results)


if __name__ == "__main__":
    asyncio.run(main())


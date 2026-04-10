#!/usr/bin/env python3
"""Compare keys stored in the database with keys present on VPN servers."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.infra.sqlite_utils import get_db_cursor  # noqa: E402
from vpn_protocols import V2RayProtocol  # noqa: E402


@dataclass
class ServerInfo:
    id: int
    name: str
    protocol: str
    api_url: str
    cert_sha256: Optional[str] = None
    api_key: Optional[str] = None
    country: Optional[str] = None
    domain: Optional[str] = None


@dataclass
class ComparisonResult:
    server: ServerInfo
    db_count: int
    remote_count: int
    missing_on_server: List[Dict[str, Any]] = field(default_factory=list)
    missing_in_db: List[Dict[str, Any]] = field(default_factory=list)
    db_without_remote_id: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def load_servers() -> List[ServerInfo]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, protocol, api_url, cert_sha256, api_key, country, domain
            FROM servers
            WHERE active = 1
            """
        )
        rows = cursor.fetchall()

    servers: List[ServerInfo] = []
    for row in rows:
        row_dict = dict(row)
        servers.append(
            ServerInfo(
                id=row_dict["id"],
                name=row_dict["name"],
                protocol=(row_dict.get("protocol") or "").lower(),
                api_url=row_dict.get("api_url") or "",
                cert_sha256=row_dict.get("cert_sha256"),
                api_key=row_dict.get("api_key"),
                country=row_dict.get("country"),
                domain=row_dict.get("domain"),
            )
        )
    return servers


def load_db_keys(server: ServerInfo) -> List[Dict[str, Any]]:
    with get_db_cursor() as cursor:
        if server.protocol != "v2ray":
            return []
        cursor.execute(
            """
            SELECT id, user_id, email, v2ray_uuid, level, created_at
            FROM v2ray_keys
            WHERE server_id = ?
            """,
            (server.id,),
        )
        rows = cursor.fetchall()

    return [dict(row) for row in rows]


def serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert SQLite row (dict) to JSON serializable values."""
    serialized = {}
    for key, value in row.items():
        if isinstance(value, (bytes, bytearray)):
            serialized[key] = value.hex()
        else:
            serialized[key] = value
    return serialized


def extract_v2ray_uuid(remote_entry: Dict[str, Any]) -> Optional[str]:
    uuid = remote_entry.get("uuid")
    if not uuid:
        key_info = remote_entry.get("key") or {}
        uuid = key_info.get("uuid")
    if not uuid:
        uuid = remote_entry.get("id")
    if isinstance(uuid, str) and uuid.strip():
        return uuid.strip()
    return None


async def compare_v2ray(server: ServerInfo, db_keys: List[Dict[str, Any]]) -> ComparisonResult:
    result = ComparisonResult(server=server, db_count=len(db_keys), remote_count=0)

    client = V2RayProtocol(server.api_url, server.api_key or "")
    try:
        remote_keys = await client.get_all_keys()
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"V2Ray API error: {exc}")
        await client.close()
        return result
    finally:
        try:
            await client.close()
        except Exception:
            pass

    remote_dict: Dict[str, Dict[str, Any]] = {}
    remote_names: Dict[str, Dict[str, Any]] = {}

    for entry in remote_keys or []:
        uuid = extract_v2ray_uuid(entry)
        name = entry.get("name")
        if uuid:
            remote_dict[uuid] = entry
        if isinstance(name, str) and name:
            remote_names[name.lower()] = entry

    result.remote_count = len(remote_keys or [])

    for row in db_keys:
        uuid = (row.get("v2ray_uuid") or "").strip()
        email = (row.get("email") or "").lower()
        row_serialized = serialize_row(row)

        if uuid:
            if uuid not in remote_dict:
                result.missing_on_server.append(
                    {
                        "db_entry": row_serialized,
                        "matching_hint": {"uuid": uuid, "email": row.get("email")},
                    }
                )
            continue

        if email and email in remote_names:
            continue

        result.db_without_remote_id.append(row_serialized)

    db_uuid_set = {(row.get("v2ray_uuid") or "").strip() for row in db_keys if row.get("v2ray_uuid")}
    db_email_set = {(row.get("email") or "").lower() for row in db_keys if row.get("email")}

    for uuid, remote_entry in remote_dict.items():
        name = (remote_entry.get("name") or "").lower()
        if uuid not in db_uuid_set and name not in db_email_set:
            result.missing_in_db.append(
                {
                    "remote_key": remote_entry,
                    "matching_hint": {"uuid": uuid, "name": remote_entry.get("name")},
                }
            )

    return result


async def compare_servers() -> List[ComparisonResult]:
    servers = load_servers()
    results: List[ComparisonResult] = []

    for server in servers:
        db_keys = load_db_keys(server)
        if server.protocol == "v2ray":
            results.append(await compare_v2ray(server, db_keys))
        else:
            result = ComparisonResult(server=server, db_count=len(db_keys), remote_count=0)
            result.errors.append(f"Unsupported protocol (expected v2ray): {server.protocol}")
            results.append(result)

    return results


def print_report(results: List[ComparisonResult]) -> None:
    """Вывести детальный отчет о сравнении ключей"""
    print("=" * 80)
    print("ОТЧЕТ О СРАВНЕНИИ КЛЮЧЕЙ: БАЗА ДАННЫХ vs СЕРВЕРЫ")
    print("=" * 80)
    print()
    
    total_servers = len(results)
    synced_servers = sum(1 for r in results if not r.errors and not r.missing_on_server 
                         and not r.missing_in_db and not r.db_without_remote_id)
    total_db_keys = sum(r.db_count for r in results)
    total_remote_keys = sum(r.remote_count for r in results)
    total_missing_on_server = sum(len(r.missing_on_server) for r in results)
    total_missing_in_db = sum(len(r.missing_in_db) for r in results)
    
    print(f"📊 ОБЩАЯ СТАТИСТИКА:")
    print(f"   Всего серверов: {total_servers}")
    print(f"   Синхронизировано: {synced_servers}")
    print(f"   Всего ключей в БД: {total_db_keys}")
    print(f"   Всего ключей на серверах: {total_remote_keys}")
    print(f"   Отсутствует на серверах: {total_missing_on_server}")
    print(f"   Отсутствует в БД: {total_missing_in_db}")
    print()
    print("=" * 80)
    print()
    
    for res in results:
        header = f"🔹 Сервер: {res.server.name}"
        print(header)
        print(f"   ID: {res.server.id} | Протокол: {res.server.protocol.upper()}")
        if res.server.country:
            print(f"   Страна: {res.server.country}")
        print(f"   Ключей в БД: {res.db_count}")
        print(f"   Ключей на сервере: {res.remote_count}")

        if res.errors:
            print(f"   ⚠️  ОШИБКИ ({len(res.errors)}):")
            for err in res.errors:
                print(f"      • {err}")
            print()
            continue

        status_icon = "✅" if (not res.missing_on_server and not res.missing_in_db 
                              and not res.db_without_remote_id) else "⚠️"
        
        if res.missing_on_server:
            print(f"   {status_icon} Отсутствует на сервере ({len(res.missing_on_server)}):")
            for idx, item in enumerate(res.missing_on_server[:10], 1):
                db_entry = item.get("db_entry", {})
                hint = item.get("matching_hint", {})
                key_id = hint.get("key_id") or hint.get("uuid") or "N/A"
                email = db_entry.get("email") or hint.get("email") or "N/A"
                print(f"      {idx}. ID в БД: {db_entry.get('id', 'N/A')}, "
                      f"Ключ: {key_id[:20]}..., Email: {email}")
            if len(res.missing_on_server) > 10:
                print(f"      ... и еще {len(res.missing_on_server) - 10} ключей")
            print()

        if res.missing_in_db:
            print(f"   {status_icon} Отсутствует в БД ({len(res.missing_in_db)}):")
            for idx, item in enumerate(res.missing_in_db[:10], 1):
                remote_key = item.get("remote_key", {})
                hint = item.get("matching_hint", {})
                uuid = hint.get("uuid") or extract_v2ray_uuid(remote_key) or "N/A"
                name = remote_key.get("name") or hint.get("name") or "N/A"
                key_id = remote_key.get("id", "N/A")
                print(f"      {idx}. UUID: {uuid[:20]}..., "
                      f"ID: {key_id[:20]}..., Имя: {name}")
            if len(res.missing_in_db) > 10:
                print(f"      ... и еще {len(res.missing_in_db) - 10} ключей")
            print()

        if res.db_without_remote_id:
            print(f"   ⚠️  Ключи в БД без ID сервера ({len(res.db_without_remote_id)}):")
            for idx, item in enumerate(res.db_without_remote_id[:10], 1):
                key_id = item.get("key_id") or item.get("v2ray_uuid") or "N/A"
                email = item.get("email") or "N/A"
                print(f"      {idx}. ID в БД: {item.get('id', 'N/A')}, "
                      f"Ключ: {key_id[:20]}..., Email: {email}")
            if len(res.db_without_remote_id) > 10:
                print(f"      ... и еще {len(res.db_without_remote_id) - 10} ключей")
            print()

        if (
            not res.missing_on_server
            and not res.missing_in_db
            and not res.db_without_remote_id
        ):
            print("   ✅ Все ключи синхронизированы")

        print("-" * 80)
        print()


async def main() -> None:
    results = await compare_servers()
    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())


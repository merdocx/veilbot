#!/usr/bin/env python3
"""Compare keys stored in the database with keys present on VPN servers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import get_db_cursor  # noqa: E402
from vpn_protocols import OutlineProtocol, V2RayProtocol  # noqa: E402


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
        if server.protocol == "outline":
            cursor.execute(
                """
                SELECT id, user_id, email, key_id, access_url, expiry_at
                FROM keys
                WHERE server_id = ?
                """,
                (server.id,),
            )
        elif server.protocol == "v2ray":
            cursor.execute(
                """
                SELECT id, user_id, email, v2ray_uuid, level, created_at, expiry_at
                FROM v2ray_keys
                WHERE server_id = ?
                """,
                (server.id,),
            )
        else:
            return []

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


async def compare_outline(server: ServerInfo, db_keys: List[Dict[str, Any]]) -> ComparisonResult:
    result = ComparisonResult(server=server, db_count=len(db_keys), remote_count=0)

    client = OutlineProtocol(server.api_url, server.cert_sha256 or "")
    try:
        remote_keys = await client.get_all_keys()
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Outline API error: {exc}")
        return result

    remote_dict: Dict[str, Dict[str, Any]] = {}
    remote_names: Dict[str, Dict[str, Any]] = {}

    for key in remote_keys or []:
        key_id = key.get("id")
        name = key.get("name")
        if key_id is not None:
            remote_dict[str(key_id)] = key
        if isinstance(name, str) and name:
            remote_names[name.lower()] = key

    result.remote_count = len(remote_keys or [])

    for row in db_keys:
        key_id = row.get("key_id")
        email = (row.get("email") or "").lower()
        row_serialized = serialize_row(row)

        if key_id:
            remote_entry = remote_dict.get(str(key_id))
            if not remote_entry:
                result.missing_on_server.append(
                    {
                        "db_entry": row_serialized,
                        "matching_hint": {"key_id": key_id, "email": row.get("email")},
                    }
                )
            continue

        # No remote id stored, try to match by email/name
        if email and email in remote_names:
            continue

        result.db_without_remote_id.append(row_serialized)

    # Remote keys missing in DB
    db_key_ids = {str(row["key_id"]) for row in db_keys if row.get("key_id")}
    db_emails = {(row.get("email") or "").lower() for row in db_keys if row.get("email")}

    for key_id, remote_entry in remote_dict.items():
        name = (remote_entry.get("name") or "").lower()
        if key_id not in db_key_ids and name not in db_emails:
            result.missing_in_db.append(
                {
                    "remote_key": remote_entry,
                    "matching_hint": {"key_id": key_id, "name": remote_entry.get("name")},
                }
            )

    return result


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
        if server.protocol == "outline":
            results.append(await compare_outline(server, db_keys))
        elif server.protocol == "v2ray":
            results.append(await compare_v2ray(server, db_keys))
        else:
            result = ComparisonResult(server=server, db_count=len(db_keys), remote_count=0)
            result.errors.append(f"Unsupported protocol: {server.protocol}")
            results.append(result)

    return results


def print_report(results: List[ComparisonResult]) -> None:
    for res in results:
        header = f"Server {res.server.name} (ID {res.server.id}, protocol {res.server.protocol}"
        if res.server.country:
            header += f", country {res.server.country}"
        header += ")"
        print(header)
        print(f"  DB keys: {res.db_count}")
        print(f"  Server keys: {res.remote_count}")

        if res.errors:
            for err in res.errors:
                print(f"  ERROR: {err}")
            print()
            continue

        if res.missing_on_server:
            print(f"  Missing on server ({len(res.missing_on_server)}):")
            for item in res.missing_on_server:
                print("    - DB entry:", json.dumps(item, ensure_ascii=False))

        if res.missing_in_db:
            print(f"  Missing in DB ({len(res.missing_in_db)}):")
            for item in res.missing_in_db:
                print("    - Remote key:", json.dumps(item, ensure_ascii=False))

        if res.db_without_remote_id:
            print(f"  DB entries without remote id ({len(res.db_without_remote_id)}):")
            for item in res.db_without_remote_id:
                print("    -", json.dumps(item, ensure_ascii=False))

        if (
            not res.missing_on_server
            and not res.missing_in_db
            and not res.db_without_remote_id
        ):
            print("  ✔ Everything matches")

        print()


async def main() -> None:
    results = await compare_servers()
    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())


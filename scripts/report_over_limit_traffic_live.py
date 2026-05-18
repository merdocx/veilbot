#!/usr/bin/env python3
"""
Отчёт: активные подписки с превышением трафикового лимита + свежие цифры с панелей.

Учёт: used = max(0, S - B), S = сумма panel_total_bytes_observed по ключам,
B = subscriptions.traffic_baseline_bytes. Для «live» по каждому ключу:
merge max(db_observed, panel_total). Не изменяет БД.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Optional

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from app.repositories.subscription_repository import SubscriptionRepository
from app.settings import settings
from vpn_protocols import ProtocolFactory


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.2f} KiB"
    if n < 1024**3:
        return f"{n / (1024**2):.2f} MiB"
    return f"{n / (1024**3):.3f} GiB"


@dataclass
class SubRow:
    sub_id: int
    user_id: int
    limit_mb: int
    limit_bytes: int
    sum_usage_db: int
    baseline_b: int
    traffic_over_limit_at: Optional[int]
    tariff_name: Optional[str]


@dataclass
class KeyRow:
    key_id: int
    server_id: int
    v2ray_uuid: str
    api_url: str
    api_key: str
    observed_db: int


async def _fetch_panel_total(api_url: str, api_key: str, v2ray_uuid: str) -> tuple[Optional[int], Optional[str]]:
    if not api_url or not api_key or not v2ray_uuid:
        return None, "no credentials"
    proto = ProtocolFactory.create_protocol("v2ray", {"api_url": api_url, "api_key": api_key})
    try:
        _ident, stats = await proto.get_v2ray_key_traffic_resolved(v2ray_uuid.strip())
        if not stats:
            return None, "no stats"
        tb = stats.get("total_bytes") if isinstance(stats, dict) else None
        if isinstance(tb, (int, float)) and tb >= 0:
            return int(tb), None
        return None, "invalid total_bytes"
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:120]
    finally:
        try:
            await proto.close()
        except Exception:  # noqa: BLE001
            pass


async def main() -> int:
    db_path = settings.DATABASE_PATH
    now = int(time.time())
    repo = SubscriptionRepository(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            s.id AS sub_id,
            s.user_id,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) AS limit_mb,
            s.traffic_over_limit_at,
            t.name AS tariff_name,
            COALESCE(s.traffic_baseline_bytes, 0) AS baseline_b
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND s.expires_at > ?
          AND COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) > 0
        ORDER BY s.id
        """,
        (now,),
    )
    raw_subs = [dict(r) for r in cur.fetchall()]

    exceeded: list[SubRow] = []
    for r in raw_subs:
        lim_mb = int(r["limit_mb"] or 0)
        lim_b = lim_mb * 1024 * 1024
        sid = int(r["sub_id"])
        su = repo.get_subscription_traffic_sum(sid)
        if su > lim_b:
            exceeded.append(
                SubRow(
                    sub_id=sid,
                    user_id=int(r["user_id"]),
                    limit_mb=lim_mb,
                    limit_bytes=lim_b,
                    sum_usage_db=su,
                    baseline_b=int(r["baseline_b"] or 0),
                    traffic_over_limit_at=r["traffic_over_limit_at"],
                    tariff_name=r["tariff_name"],
                )
            )

    if not exceeded:
        print(
            "Нет активных подписок с лимитом трафика, у которых used=max(0,S-B) превышает лимит."
        )
        conn.close()
        return 0

    print(f"Подписок с превышением (used > лимит): {len(exceeded)}\n")

    sem = asyncio.Semaphore(20)

    async def gated_fetch(api_url: str, api_key: str, uuid: str) -> tuple[Optional[int], Optional[str]]:
        async with sem:
            return await _fetch_panel_total(api_url, api_key, uuid)

    for sub in exceeded:
        cur.execute(
            """
            SELECT k.id, k.server_id, k.v2ray_uuid,
                   IFNULL(s.api_url, '') AS api_url,
                   IFNULL(s.api_key, '') AS api_key,
                   IFNULL(k.panel_total_bytes_observed, 0) AS observed_db
            FROM v2ray_keys k
            JOIN servers s ON s.id = k.server_id
            WHERE k.subscription_id = ?
              AND s.protocol = 'v2ray'
            ORDER BY k.id
            """,
            (sub.sub_id,),
        )
        keys = [
            KeyRow(
                key_id=int(row[0]),
                server_id=int(row[1]),
                v2ray_uuid=str(row[2] or ""),
                api_url=str(row[3] or ""),
                api_key=str(row[4] or ""),
                observed_db=int(row[5] or 0),
            )
            for row in cur.fetchall()
        ]

        tasks = []
        for kr in keys:
            if kr.api_url and kr.api_key and kr.v2ray_uuid.strip():
                tasks.append(gated_fetch(kr.api_url, kr.api_key, kr.v2ray_uuid))
            else:
                tasks.append(asyncio.sleep(0, result=(None, "skipped no api")))

        results = await asyncio.gather(*tasks, return_exceptions=False)

        merged_sum = 0
        lines_detail = []
        for kr, (panel_total, err) in zip(keys, results):
            if panel_total is None:
                merged_sum += kr.observed_db
                reason = err or "?"
                lines_detail.append(
                    f"    key_id={kr.key_id} srv={kr.server_id}: stored_observed={_human_bytes(kr.observed_db)} "
                    f"(panel n/a: {reason})"
                )
            else:
                m = max(kr.observed_db, panel_total)
                merged_sum += m
                lines_detail.append(
                    f"    key_id={kr.key_id} srv={kr.server_id}: panel={_human_bytes(panel_total)} "
                    f"stored={_human_bytes(kr.observed_db)} merged={_human_bytes(m)}"
                )

        used_live = max(0, merged_sum - sub.baseline_b)
        over_at = sub.traffic_over_limit_at
        over_at_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(over_at)) if over_at else "—"
        tn = sub.tariff_name or "—"

        print(f"subscription_id={sub.sub_id} user_id={sub.user_id} tariff={tn!r}")
        print(f"  limit: {_human_bytes(sub.limit_bytes)} ({sub.limit_mb} MiB)")
        print(f"  baseline B (подписка): {_human_bytes(sub.baseline_b)}")
        print(f"  used (БД): {_human_bytes(sub.sum_usage_db)}")
        print(f"  used (live merge по ключам): {_human_bytes(used_live)}")
        print(f"  превышение (live): {_human_bytes(max(0, used_live - sub.limit_bytes))}")
        print(f"  traffic_over_limit_at: {over_at_s}")
        print("  ключи:")
        for ln in lines_detail:
            print(ln)
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

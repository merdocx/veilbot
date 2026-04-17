"""
Логика групп серверов (subscription_group_id): не более одного V2Ray-ключа на подписку
на группу; выбор сервера по максимуму свободных слотов (max_keys − текущее число ключей).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TypeVar

T = TypeVar("T")  # used by pick_best_server_by_free_slots


def user_has_active_paid_subscription(
    cursor: sqlite3.Cursor,
    user_id: int,
    now_ts: int,
    *,
    include_subscription_id: Optional[int] = None,
) -> bool:
    """
    Есть ли у пользователя активная подписка с платным тарифом (price_rub > 0).

    VIP здесь не учитывается — проверяйте is_user_vip отдельно.

    include_subscription_id: учитывать подписку с этим id даже если expires_at ещё не > now_ts
    (граничные случаи при создании подписки/ключей).
    """
    if include_subscription_id is not None:
        cursor.execute(
            """
            SELECT COUNT(*) FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.user_id = ? AND s.is_active = 1 AND COALESCE(t.price_rub, 0) > 0
              AND (s.expires_at > ? OR s.id = ?)
            """,
            (user_id, now_ts, include_subscription_id),
        )
    else:
        cursor.execute(
            """
            SELECT COUNT(*) FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.user_id = ? AND s.is_active = 1 AND s.expires_at > ?
              AND COALESCE(t.price_rub, 0) > 0
            """,
            (user_id, now_ts),
        )
    return cursor.fetchone()[0] > 0


def passes_access_level(
    access_level: str,
    *,
    is_vip: bool,
    has_active_paid_subscription: bool,
) -> bool:
    al = (access_level or "all").lower()
    if al == "all":
        return True
    if al == "vip":
        return is_vip
    if al == "paid":
        # «Платные и VIP»: платный тариф (не бесплатная подписка) или VIP
        return is_vip or has_active_paid_subscription
    return True


def free_slots_for_server(max_keys: int, used_keys: int) -> int:
    cap = max_keys if max_keys and max_keys > 0 else 10**9
    return max(0, cap - used_keys)


def build_existing_key_coverage(
    key_rows: Sequence[Tuple[int, str]],
) -> Tuple[Set[int], Set[str]]:
    """key_rows: (server_id, group_id), пустая строка group_id = без группы."""
    servers: Set[int] = set()
    groups: Set[str] = set()
    for server_id, gid in key_rows:
        g = (gid or "").strip()
        if g:
            groups.add(g)
        else:
            servers.add(int(server_id))
    return servers, groups


def pick_best_server_by_free_slots(
    candidates: Sequence[T],
    *,
    get_id: Any,
    get_max_keys: Any,
    key_counts: Mapping[int, int],
) -> Optional[T]:
    """Максимум свободных слотов, при равенстве — меньший id сервера."""
    best: Optional[T] = None
    best_rank: Optional[Tuple[int, int]] = None
    for item in candidates:
        sid = int(get_id(item))
        max_k = int(get_max_keys(item) or 0)
        used = int(key_counts.get(sid, 0))
        fs = free_slots_for_server(max_k, used)
        rank = (fs, -sid)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = item
    return best


def compute_targets_purchase_sql_rows(
    filtered_full_rows: Sequence[Tuple[Any, ...]],
    *,
    existing_key_rows: Sequence[Tuple[int, str]],
    key_counts: Mapping[int, int],
) -> List[Tuple[Any, ...]]:
    """
    filtered_full_rows: SELECT rows
    id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256,
    access_level, max_keys, subscription_group_id
    Возвращает список кортежей первых 8 полей для _create_single_v2ray_key.
    """
    targets: List[Tuple[Any, ...]] = []
    cov_s, cov_g = build_existing_key_coverage(existing_key_rows)

    by_group: Dict[str, List[Tuple[Any, ...]]] = defaultdict(list)
    for row in filtered_full_rows:
        gid = (row[10] or "").strip() if len(row) > 10 else ""
        if gid:
            by_group[gid].append(row)
        else:
            sid = int(row[0])
            if sid not in cov_s:
                targets.append(row[:8])

    for gid, rows in by_group.items():
        if gid in cov_g:
            continue
        best = pick_best_server_by_free_slots(
            rows,
            get_id=lambda r: r[0],
            get_max_keys=lambda r: r[9] if len(r) > 9 else 0,
            key_counts=key_counts,
        )
        if best is not None:
            targets.append(best[:8])

    return targets


def filter_servers_by_access_sql_rows(
    rows: Sequence[Tuple[Any, ...]],
    *,
    is_vip: bool,
    has_active_paid_subscription: bool,
) -> List[Tuple[Any, ...]]:
    out: List[Tuple[Any, ...]] = []
    for row in rows:
        al = row[8] if len(row) > 8 else "all"
        if passes_access_level(
            al,
            is_vip=is_vip,
            has_active_paid_subscription=has_active_paid_subscription,
        ):
            out.append(row)
    return out


def iter_sync_work_items(
    subscriptions: Sequence[Mapping[str, Any]],
    v2ray_servers: Sequence[Mapping[str, Any]],
    sub_coverage: Mapping[int, Tuple[Set[int], Set[str]]],
    key_counts: Mapping[int, int],
    *,
    is_user_vip: Any,
) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """
    is_user_vip: callable(int user_id) -> bool
    Возвращает пары (server_dict, subscription_dict) для создания ключа.
    """
    work: List[Tuple[Mapping[str, Any], Mapping[str, Any]]] = []

    for sub in subscriptions:
        sub_id = int(sub["id"])
        uid = int(sub["user_id"])
        is_vip = bool(is_user_vip(uid))
        has_active_paid = int(sub.get("price_rub") or 0) > 0

        cov_servers, cov_groups = sub_coverage.get(sub_id, (set(), set()))

        filtered: List[Mapping[str, Any]] = []
        for s in v2ray_servers:
            al = s.get("access_level") or "all"
            if passes_access_level(
                al,
                is_vip=is_vip,
                has_active_paid_subscription=has_active_paid,
            ):
                filtered.append(s)

        by_group: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for s in filtered:
            gid = (s.get("subscription_group_id") or "").strip()
            if gid:
                by_group[gid].append(s)
            else:
                sid = int(s["id"])
                if sid not in cov_servers:
                    work.append((s, sub))

        for gid, srvs in by_group.items():
            if gid in cov_groups:
                continue
            best = pick_best_server_by_free_slots(
                srvs,
                get_id=lambda x: x["id"],
                get_max_keys=lambda x: x.get("max_keys") or 0,
                key_counts=key_counts,
            )
            if best is not None:
                work.append((best, sub))

    return work

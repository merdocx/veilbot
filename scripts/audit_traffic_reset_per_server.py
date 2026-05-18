#!/usr/bin/env python3
"""
Readonly-friendly аудит работы POST /api/keys/{key_id}/traffic/reset на всех
активных V2Ray-серверах.

Назначение
----------
Дёргать reset на одном существующем ключе каждого сервера и проверять, что
панель действительно обнулила счётчик (GET /traffic после reset). Выводит
таблицу со статусом по каждому серверу: считается ли panel reset рабочим,
no-op, или сервер недоступен.

Сценарии:
- По умолчанию (`--dry-run`): только GET /keys/{id}/traffic, без POST. Это
  безопасно для прода: ничего не меняется, можно проверить хотя бы
  доступность панели и формат ответа.
- С `--apply`: один POST /traffic/reset на каждый сервер на выбранном ключе
  и контрольный GET. Запускать вручную, желательно на технических ключах.

Скрипт ничего не пишет в БД проекта. Все правки в `v2ray_keys` /
`subscriptions` исключены — мы только читаем список серверов и (опционально)
дёргаем панель.

Безопасность выбора ключа
-------------------------
Скрипт пытается выбрать ключ по приоритету:
1. Ключ, привязанный к подписке с `is_active = 0` (минимизирует влияние на
   живых пользователей).
2. Ключ с минимальным `traffic_usage_bytes` среди активных (если ничего
   другого нет).

Если у сервера нет ни одного ключа — он помечается как `no probe key` и
пропускается. Скрипт НИКОГДА не создаёт ключи на сервере.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.infra.sqlite_utils import open_connection
from app.settings import settings
from vpn_protocols import ProtocolFactory


logger = logging.getLogger("audit_traffic_reset")


# Минимальный объём, ниже которого «обнуление до 0» не информативно
# (мог быть ключ, по которому никто никогда не ходил).
DEFAULT_MIN_TOTAL_BEFORE = 1 * 1024 * 1024  # 1 MB

# Допустимый «остаточный» трафик после reset (учёт лагов панели/таймингов).
RESET_OK_RATIO = 0.001
RESET_OK_FLOOR_BYTES = 1 * 1024 * 1024  # 1 MB


@dataclass
class ServerRow:
    server_id: int
    name: str
    api_url: str
    api_key: str
    protocol: str


@dataclass
class ProbeKey:
    db_key_id: int
    v2ray_uuid: str
    subscription_id: Optional[int]
    is_active: Optional[int]
    traffic_usage_bytes: int


@dataclass
class ServerAuditResult:
    server: ServerRow
    probe: Optional[ProbeKey]
    reachable: bool
    total_before: Optional[int]
    reset_http_ok: Optional[bool]
    reset_payload_ok: Optional[bool]
    total_after: Optional[int]
    verdict: str
    error: Optional[str]


def _format_bytes(num: Optional[int]) -> str:
    if num is None:
        return "?"
    if num < 1024:
        return f"{num} B"
    if num < 1024 ** 2:
        return f"{num / 1024:.1f} KB"
    if num < 1024 ** 3:
        return f"{num / (1024 ** 2):.1f} MB"
    return f"{num / (1024 ** 3):.2f} GB"


def _load_servers(db_path: str, only_server_ids: Optional[list[int]] = None) -> list[ServerRow]:
    with open_connection(db_path) as conn:
        c = conn.cursor()
        sql = """
            SELECT id, name, api_url, api_key, protocol
            FROM servers
            WHERE active = 1
              AND protocol = 'v2ray'
              AND api_url IS NOT NULL AND api_url != ''
              AND api_key IS NOT NULL AND api_key != ''
            ORDER BY id
        """
        c.execute(sql)
        rows = c.fetchall()
    out: list[ServerRow] = []
    for r in rows:
        srv = ServerRow(
            server_id=int(r[0]),
            name=str(r[1] or ""),
            api_url=str(r[2] or "").strip(),
            api_key=str(r[3] or "").strip(),
            protocol=str(r[4] or "v2ray"),
        )
        if only_server_ids and srv.server_id not in only_server_ids:
            continue
        out.append(srv)
    return out


def _pick_probe_key(db_path: str, server_id: int) -> Optional[ProbeKey]:
    """
    Подобрать «безопасный» ключ для probe.
    Приоритет: неактивные подписки > наименьший usage среди активных.
    """
    with open_connection(db_path) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT k.id,
                   k.v2ray_uuid,
                   k.subscription_id,
                   COALESCE(s.is_active, 1) AS is_active,
                   COALESCE(k.traffic_usage_bytes, 0) AS usage_bytes
            FROM v2ray_keys k
            LEFT JOIN subscriptions s ON s.id = k.subscription_id
            WHERE k.server_id = ?
              AND k.v2ray_uuid IS NOT NULL
              AND TRIM(k.v2ray_uuid) != ''
            ORDER BY
                CASE WHEN COALESCE(s.is_active, 1) = 0 THEN 0 ELSE 1 END,
                COALESCE(k.traffic_usage_bytes, 0) ASC
            LIMIT 1
            """,
            (server_id,),
        )
        row = c.fetchone()
    if not row:
        return None
    return ProbeKey(
        db_key_id=int(row[0]),
        v2ray_uuid=str(row[1] or "").strip(),
        subscription_id=int(row[2]) if row[2] is not None else None,
        is_active=int(row[3]) if row[3] is not None else None,
        traffic_usage_bytes=int(row[4] or 0),
    )


async def _audit_server(
    srv: ServerRow,
    probe: Optional[ProbeKey],
    *,
    apply_reset: bool,
    min_total_before: int,
) -> ServerAuditResult:
    if probe is None:
        return ServerAuditResult(
            server=srv,
            probe=None,
            reachable=False,
            total_before=None,
            reset_http_ok=None,
            reset_payload_ok=None,
            total_after=None,
            verdict="no probe key",
            error=None,
        )

    config = {"api_url": srv.api_url, "api_key": srv.api_key}
    protocol = ProtocolFactory.create_protocol("v2ray", config)
    total_before: Optional[int] = None
    total_after: Optional[int] = None
    reset_http_ok: Optional[bool] = None
    reset_payload_ok: Optional[bool] = None
    error: Optional[str] = None
    verdict: str
    api_id_str: Optional[str] = None
    try:
        api_id, stats_before = await protocol.get_v2ray_key_traffic_resolved(probe.v2ray_uuid)
        if api_id is None or not stats_before:
            return ServerAuditResult(
                server=srv,
                probe=probe,
                reachable=False,
                total_before=None,
                reset_http_ok=None,
                reset_payload_ok=None,
                total_after=None,
                verdict="api unreachable / cannot resolve key",
                error=None,
            )
        api_id_str = str(api_id)
        if isinstance(stats_before, dict):
            tb = stats_before.get("total_bytes")
            if isinstance(tb, (int, float)) and tb >= 0:
                total_before = int(tb)

        if not apply_reset:
            verdict = "dry-run: reset not attempted"
            return ServerAuditResult(
                server=srv,
                probe=probe,
                reachable=True,
                total_before=total_before,
                reset_http_ok=None,
                reset_payload_ok=None,
                total_after=None,
                verdict=verdict,
                error=None,
            )

        try:
            reset_payload_ok = await protocol.reset_key_traffic(api_id_str)
            reset_http_ok = True
        except Exception as exc:  # noqa: BLE001
            reset_payload_ok = False
            reset_http_ok = False
            error = f"reset error: {exc}"

        try:
            stats_after = await protocol.get_key_traffic_stats(api_id_str)
            if isinstance(stats_after, dict):
                ta = stats_after.get("total_bytes")
                if isinstance(ta, (int, float)) and ta >= 0:
                    total_after = int(ta)
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"get_after error: {exc}"

        if total_before is not None and total_before < min_total_before:
            verdict = (
                f"inconclusive (total_before={_format_bytes(total_before)} < "
                f"min={_format_bytes(min_total_before)})"
            )
        elif reset_http_ok is False:
            verdict = "reset failed (HTTP/exception)"
        elif total_after is None:
            verdict = "reset called, but total_after unknown"
        else:
            tolerance = max(
                RESET_OK_FLOOR_BYTES,
                int((total_before or 0) * RESET_OK_RATIO),
            )
            if total_after <= tolerance:
                verdict = "OK: panel actually resets"
            else:
                verdict = "BROKEN: panel returns 200 but does NOT reset counter"
    finally:
        try:
            await protocol.close()
        except Exception:  # noqa: BLE001
            pass

    return ServerAuditResult(
        server=srv,
        probe=probe,
        reachable=True,
        total_before=total_before,
        reset_http_ok=reset_http_ok,
        reset_payload_ok=reset_payload_ok,
        total_after=total_after,
        verdict=verdict,
        error=error,
    )


def _print_report(results: list[ServerAuditResult]) -> None:
    header = (
        f"{'srv_id':>6}  {'name':<28}  {'reachable':<9}  "
        f"{'http_ok':<7}  {'before':>10}  {'after':>10}  verdict"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        name = (r.server.name or "")[:28]
        reachable = "yes" if r.reachable else "no"
        http_ok = "?" if r.reset_http_ok is None else ("yes" if r.reset_http_ok else "no")
        before = _format_bytes(r.total_before)
        after = _format_bytes(r.total_after)
        line = (
            f"{r.server.server_id:>6}  {name:<28}  {reachable:<9}  "
            f"{http_ok:<7}  {before:>10}  {after:>10}  {r.verdict}"
        )
        print(line)
        if r.error:
            print(f"        error: {r.error}")
        if r.probe is not None:
            uuid_short = (r.probe.v2ray_uuid[:8] + "...") if r.probe.v2ray_uuid else "?"
            sub_state = "inactive" if r.probe.is_active == 0 else "active"
            print(
                f"        probe: db_key_id={r.probe.db_key_id} uuid={uuid_short} "
                f"sub={r.probe.subscription_id} ({sub_state}) "
                f"prev_usage={_format_bytes(r.probe.traffic_usage_bytes)}"
            )


async def _run(args: argparse.Namespace) -> int:
    db_path = args.db_path or settings.DATABASE_PATH
    only_ids = None
    if args.servers:
        try:
            only_ids = [int(x) for x in args.servers.split(",") if x.strip()]
        except ValueError:
            print(f"--servers must be comma-separated ints, got: {args.servers!r}", file=sys.stderr)
            return 2

    servers = _load_servers(db_path, only_server_ids=only_ids)
    if not servers:
        print("No active V2Ray servers with API credentials found.")
        return 0

    started = time.time()
    print(
        f"Auditing {len(servers)} server(s); mode="
        f"{'APPLY (POST reset)' if args.apply else 'DRY-RUN (no POST)'}"
    )
    results: list[ServerAuditResult] = []
    for srv in servers:
        probe = _pick_probe_key(db_path, srv.server_id)
        try:
            res = await _audit_server(
                srv,
                probe,
                apply_reset=args.apply,
                min_total_before=args.min_total_before,
            )
        except Exception as exc:  # noqa: BLE001
            res = ServerAuditResult(
                server=srv,
                probe=probe,
                reachable=False,
                total_before=None,
                reset_http_ok=None,
                reset_payload_ok=None,
                total_after=None,
                verdict="exception",
                error=str(exc),
            )
        results.append(res)

    print()
    _print_report(results)

    broken = [r for r in results if r.verdict.startswith("BROKEN")]
    unreachable = [r for r in results if not r.reachable]
    print()
    print(
        f"Done in {time.time() - started:.1f}s. "
        f"servers={len(results)} broken_panels={len(broken)} unreachable={len(unreachable)}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit POST /traffic/reset behaviour per V2Ray server (readonly DB)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually call POST /traffic/reset on the picked probe key. "
        "Without this flag, the script only does GET (truly readonly).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run flag (default behaviour). Mutually exclusive with --apply.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Override DB path (defaults to settings.DATABASE_PATH).",
    )
    parser.add_argument(
        "--servers",
        type=str,
        default=None,
        help="Comma-separated server ids to audit (default: all active).",
    )
    parser.add_argument(
        "--min-total-before",
        type=int,
        default=DEFAULT_MIN_TOTAL_BEFORE,
        help=(
            "Minimal total_before in bytes to consider the test conclusive "
            f"(default: {DEFAULT_MIN_TOTAL_BEFORE})."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging for vpn_protocols.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    rc = asyncio.run(_run(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()

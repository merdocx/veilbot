"""Тесты логики групп серверов (subscription_group_id) и исключения VIP."""
from bot.services.subscription_server_groups import (
    compute_targets_purchase_sql_rows,
    iter_sync_work_items,
    subscription_group_dedup_applies,
)


def _server_row(
    server_id: int,
    *,
    group_id: str = "",
    max_keys: int = 100,
    access_level: str = "all",
) -> tuple:
    return (
        server_id,
        f"srv{server_id}",
        f"https://{server_id}.test",
        "key",
        f"{server_id}.test",
        "/v2ray",
        "v2ray",
        "sha",
        access_level,
        max_keys,
        group_id,
    )


def _server_dict(
    server_id: int,
    *,
    group_id: str = "",
    max_keys: int = 100,
    access_level: str = "all",
) -> dict:
    return {
        "id": server_id,
        "name": f"srv{server_id}",
        "access_level": access_level,
        "max_keys": max_keys,
        "subscription_group_id": group_id,
    }


class TestSubscriptionGroupDedupApplies:
    def test_vip_exempt(self):
        assert subscription_group_dedup_applies(is_vip=True) is False

    def test_non_vip_applies(self):
        assert subscription_group_dedup_applies(is_vip=False) is True


class TestComputeTargetsPurchaseSqlRows:
    def test_group_dedup_one_target_per_group(self):
        rows = [
            _server_row(1, group_id="g1"),
            _server_row(2, group_id="g1"),
            _server_row(3, group_id="g1"),
        ]
        targets = compute_targets_purchase_sql_rows(
            rows,
            existing_key_rows=[],
            key_counts={},
            apply_group_dedup=True,
        )
        assert len(targets) == 1
        assert targets[0][0] == 1

    def test_vip_three_targets_same_group(self):
        rows = [
            _server_row(1, group_id="g1"),
            _server_row(2, group_id="g1"),
            _server_row(3, group_id="g1"),
        ]
        targets = compute_targets_purchase_sql_rows(
            rows,
            existing_key_rows=[],
            key_counts={},
            apply_group_dedup=False,
        )
        assert len(targets) == 3
        assert {t[0] for t in targets} == {1, 2, 3}

    def test_vip_skips_servers_with_existing_key(self):
        rows = [
            _server_row(1, group_id="g1"),
            _server_row(2, group_id="g1"),
            _server_row(3, group_id="g1"),
        ]
        targets = compute_targets_purchase_sql_rows(
            rows,
            existing_key_rows=[(1, "g1")],
            key_counts={1: 1},
            apply_group_dedup=False,
        )
        assert len(targets) == 2
        assert {t[0] for t in targets} == {2, 3}

    def test_non_vip_group_already_covered(self):
        rows = [
            _server_row(1, group_id="g1"),
            _server_row(2, group_id="g1"),
        ]
        targets = compute_targets_purchase_sql_rows(
            rows,
            existing_key_rows=[(1, "g1")],
            key_counts={1: 1},
            apply_group_dedup=True,
        )
        assert targets == []


class TestIterSyncWorkItems:
    def _run(self, *, is_vip: bool, cov_groups: set | None = None):
        sub = {"id": 10, "user_id": 42, "price_rub": 0}
        servers = [
            _server_dict(1, group_id="g1"),
            _server_dict(2, group_id="g1"),
            _server_dict(3, group_id="g1"),
        ]
        sub_coverage = {10: (set(), cov_groups or set())}
        return iter_sync_work_items(
            [sub],
            servers,
            sub_coverage,
            {},
            is_user_vip=lambda uid: is_vip and uid == 42,
        )

    def test_non_vip_one_work_item_per_group(self):
        work = self._run(is_vip=False)
        assert len(work) == 1
        assert work[0][0]["id"] == 1

    def test_vip_work_item_per_server(self):
        work = self._run(is_vip=True)
        assert len(work) == 3
        assert {w[0]["id"] for w in work} == {1, 2, 3}

    def test_vip_respects_existing_server_coverage(self):
        work = self._run(is_vip=True, cov_groups={"g1"})
        # cov_groups ignored for VIP; only per-server coverage matters
        assert len(work) == 3

    def test_vip_skips_server_with_key(self):
        sub = {"id": 10, "user_id": 42, "price_rub": 0}
        servers = [
            _server_dict(1, group_id="g1"),
            _server_dict(2, group_id="g1"),
        ]
        sub_coverage = {10: ({1}, {"g1"})}
        work = iter_sync_work_items(
            [sub],
            servers,
            sub_coverage,
            {},
            is_user_vip=lambda uid: uid == 42,
        )
        assert len(work) == 1
        assert work[0][0]["id"] == 2

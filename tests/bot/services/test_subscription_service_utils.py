from uuid import uuid4

import pytest

from bot.services import subscription_service as service


def test_validate_subscription_token_accepts_uuid():
    token = str(uuid4())
    assert service.validate_subscription_token(token) is True


def test_validate_subscription_token_rejects_short_values():
    assert service.validate_subscription_token("short") is False
    assert service.validate_subscription_token("") is False


@pytest.mark.parametrize(
    ("usage_bytes", "limit_bytes", "expected"),
    [
        (100 * 1024 * 1024, 2 * 1024 * 1024 * 1024, "100.00 MB / 2.0 GB"),
        (2 * 1024 * 1024 * 1024, None, "2.00/unlimited GB"),
    ],
)
def test_format_traffic_label_formats_units(usage_bytes, limit_bytes, expected):
    assert service._format_traffic_label(usage_bytes, limit_bytes) == expected


def test_normalize_support_username_injects_at(monkeypatch):
    monkeypatch.setattr(service, "SUPPORT_USERNAME", "support")
    assert service._normalize_support_username() == "@support"
    monkeypatch.setattr(service, "SUPPORT_USERNAME", "   ")
    assert service._normalize_support_username() is None


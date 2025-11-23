from admin.routes.keys import (
    _build_key_view_model,
    _format_relative,
    _parse_traffic_value,
)


def test_build_key_view_model_active_with_limit():
    now_ts = 1_700_000_000
    # 1.50 GB в байтах = 1.5 * 1024^3 = 1610612736
    traffic_usage_bytes = int(1.5 * 1024 * 1024 * 1024)
    # Структура данных соответствует SQL запросу из get_key_unified_by_id (расширенный формат, row_len >= 17):
    # id, key_id, access_url, created_at, expiry_at, server, email, user_id, tariff, protocol,
    # traffic_limit_mb, api_url, api_key, traffic_usage_bytes, traffic_over_limit_at, traffic_over_limit_notified, subscription_id
    row = [
        42,  # 0: id
        'uuid-123',  # 1: key_id
        'access-url',  # 2: access_url
        now_ts - 3_600,  # 3: created_at (one hour ago)
        now_ts + 3_600,  # 4: expiry_at (expires in one hour)
        'Test server',  # 5: server name
        'user@example.com',  # 6: email
        12345,  # 7: user_id (расширенный формат)
        'Premium',  # 8: tariff
        'outline',  # 9: protocol
        1024,  # 10: traffic_limit_mb (1 GB)
        '',  # 11: api_url
        '',  # 12: api_key
        traffic_usage_bytes,  # 13: traffic_usage_bytes в байтах
        None,  # 14: traffic_over_limit_at
        0,  # 15: traffic_over_limit_notified
        None,  # 16: subscription_id
    ]

    view = _build_key_view_model(row, now_ts)

    assert view['id'] == '42_outline'  # ID теперь включает протокол
    assert view['numeric_id'] == 42  # Числовой ID доступен отдельно
    assert view['status'] == 'active'
    assert view['status_label'] == 'Активен'
    assert view['status_icon'] == 'check_circle'
    assert view['traffic']['display'] == '1.50 GB'
    assert view['traffic']['limit_display'] == '1.00 GB'
    assert view['traffic']['limit_mb'] == 1024
    assert view['traffic']['usage_percent'] == 1.0  # clamped to 100%
    assert view['expiry_remaining'].startswith('Через')


def test_parse_traffic_value_handles_error_and_na():
    assert _parse_traffic_value('Error')['state'] == 'error'
    assert _parse_traffic_value('N/A')['state'] == 'na'


def test_format_relative_labels_future_and_past():
    assert 'через' in _format_relative(3600)
    assert 'назад' in _format_relative(-120)


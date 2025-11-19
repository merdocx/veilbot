from admin.routes.keys import (
    _build_key_view_model,
    _format_relative,
    _parse_traffic_value,
)


def test_build_key_view_model_active_with_limit():
    now_ts = 1_700_000_000
    row = [
        42,  # id
        'uuid-123',
        'access-url',
        now_ts - 3_600,  # created one hour ago
        now_ts + 3_600,  # expires in one hour
        'Test server',
        'user@example.com',
        'Premium',
        'outline',
        '1024',  # traffic limit MB (1 GB)
        '',
        '',
        '1.50 GB',
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


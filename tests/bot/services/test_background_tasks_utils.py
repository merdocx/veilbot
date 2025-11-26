from bot.services import background_tasks as tasks


def test_format_bytes_short_handles_zero():
    assert tasks._format_bytes_short(0) == "0 Б"
    assert tasks._format_bytes_short(None) == "0 Б"


def test_format_bytes_short_formats_megabytes():
    # 3.5 MB
    bytes_value = 3.5 * 1024 * 1024
    assert tasks._format_bytes_short(bytes_value) == "3.50 МБ"


def test_format_bytes_short_formats_gigabytes():
    bytes_value = 5 * 1024 * 1024 * 1024
    assert tasks._format_bytes_short(bytes_value) == "5.00 ГБ"


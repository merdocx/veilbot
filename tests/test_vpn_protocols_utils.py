from vpn_protocols import (
    normalize_vless_host,
    remove_fragment_from_vless,
    add_server_name_to_vless,
)


def test_normalize_vless_host_replaces_hostname_with_domain():
    original = "vless://uuid@example.com:443?encryption=none#Node"
    updated = normalize_vless_host(original, "new-host.com", "https://api.old")
    assert "new-host.com" in updated
    assert updated.startswith("vless://")


def test_normalize_vless_host_uses_api_hostname_when_domain_missing():
    original = "vless://uuid@example.com:443?encryption=none"
    updated = normalize_vless_host(original, None, "https://api.server.dev")
    assert "api.server.dev" in updated


def test_remove_fragment_from_vless_strips_tail():
    config = "vless://uuid@example.com:443#MyNode"
    assert remove_fragment_from_vless(config) == "vless://uuid@example.com:443"


def test_add_server_name_to_vless_updates_fragment():
    config = "vless://uuid@example.com:443#Old"
    updated = add_server_name_to_vless(config, "New Server")
    assert updated.endswith("#New%20Server")


def test_add_server_name_to_vless_no_server_name_returns_original():
    config = "vless://uuid@example.com:443"
    assert add_server_name_to_vless(config, None) == config


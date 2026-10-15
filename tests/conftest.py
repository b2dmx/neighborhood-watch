"""Shared fixtures. The Home Assistant tests run only where Home Assistant is installed."""
from __future__ import annotations

import importlib.util

import pytest

HAS_HA = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

if HAS_HA:
    import sys

    if sys.platform == "win32":
        # Local runs only (CI is Linux): asyncio on Windows needs a socket pair for its
        # own loop, which the harness's socket guard would block.
        import pytest_socket

        pytest_socket.disable_socket = lambda *a, **k: None
        pytest_socket.socket_allow_hosts = lambda *a, **k: None

    pytest_plugins = ["pytest_homeassistant_custom_component"]

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        yield


def pytest_ignore_collect(collection_path, config):
    if not HAS_HA and collection_path.name.startswith("test_ha_"):
        return True
    return None

# ruff: noqa: E402

"""Regression tests for SteamGridDB cover downloads.

A blocked or broken CDN once answered with an error page that got
persisted verbatim as a cover image, the same poison class that wedged
the portal session through installer icons.
"""

import base64
from types import SimpleNamespace

import pytest

from bottles.backend.managers import steamgriddb as steamgriddb_module
from bottles.backend.managers.steamgriddb import SteamGridDBManager
from bottles.backend.models.config import BottleConfig
from bottles.backend.utils.manager import ManagerUtils

PNG_DATA = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zwe0AAAAASUVORK5CYII="
)


def _response(status=200, payload=None, content=b""):
    res = SimpleNamespace(status_code=status, content=content)

    if payload is not None:
        res.json = lambda: payload
    else:

        def _broken_json():
            raise ValueError("not JSON")

        res.json = _broken_json
    return res


@pytest.fixture
def bottle_env(monkeypatch, tmp_path):
    grids = tmp_path / "grids"
    monkeypatch.setattr(ManagerUtils, "get_bottle_path", lambda _config: str(tmp_path))
    return BottleConfig(Name="Test", Path="Test"), grids


def test_search_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(
        steamgriddb_module.requests,
        "get",
        lambda *_a, **_k: _response(status=403),
    )
    assert SteamGridDBManager.get_game_grid("Control") is None


def test_search_invalid_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        steamgriddb_module.requests,
        "get",
        lambda *_a, **_k: _response(status=200),
    )
    assert SteamGridDBManager.get_game_grid("Control") is None


def test_grid_rejects_error_page(monkeypatch, bottle_env):
    config, grids = bottle_env
    monkeypatch.setattr(
        steamgriddb_module.requests,
        "get",
        lambda *_a, **_k: _response(content=b"<html>error code: 1010</html>"),
    )

    result = SteamGridDBManager._SteamGridDBManager__save_grid(
        "https://example.com/cover.png", config
    )

    assert result is None
    assert not any(grids.glob("*")) if grids.exists() else True


def test_grid_persists_real_image(monkeypatch, bottle_env):
    config, grids = bottle_env
    monkeypatch.setattr(
        steamgriddb_module.requests,
        "get",
        lambda *_a, **_k: _response(content=PNG_DATA),
    )

    result = SteamGridDBManager._SteamGridDBManager__save_grid(
        "https://example.com/cover.png", config
    )

    assert result is not None
    assert result.startswith("grid:")
    files = list(grids.glob("*"))
    assert len(files) == 1
    assert files[0].suffix == ".png"
    assert files[0].read_bytes() == PNG_DATA
    assert not list(grids.glob("*.part"))

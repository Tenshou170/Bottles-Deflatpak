# ruff: noqa: E402

"""Regression tests for the version plumbing in bottles.frontend.params.

The About dialog version pill must always reflect the version declared in
meson.build, never a hardcoded number.
"""

import importlib
import os
import re
from pathlib import Path

import pytest

from bottles.frontend import params

_source_root = Path(__file__).resolve().parents[3]
_meson_build = _source_root / "meson.build"


@pytest.fixture
def reload_params():
    """Reload the params module so tests can exercise the template guards."""
    importlib.reload(params)
    yield params
    importlib.reload(params)


def test_app_version_matches_meson_build(reload_params):
    content = _meson_build.read_text(encoding="utf-8")
    expected = params._parse_project_version(content)
    assert expected != "unknown"
    assert reload_params.APP_VERSION == expected


def test_major_minor_derived_from_app_version(reload_params):
    version = reload_params.APP_VERSION
    major, _, minor = version.partition(".")

    assert reload_params.APP_MAJOR_VERSION == major
    assert reload_params.APP_MINOR_VERSION == minor


def test_parse_project_version_skips_meson_version():
    content = "project('bottles', version: '67.4', meson_version: '>= 1.5.0')\n"
    assert params._parse_project_version(content) == "67.4"


def test_parse_project_version_double_quoted():
    content = "project('bottles', version: \"12.3\")\n"
    assert params._parse_project_version(content) == "12.3"


def test_parse_project_version_missing():
    assert params._parse_project_version("project('bottles')\n") == "unknown"


def test_fallback_parses_meson_build_from_source_tree(
    reload_params, tmp_path, monkeypatch
):
    """A source tree without a configured build still gets the meson version."""
    assert reload_params.APP_VERSION == params._parse_project_version(
        _meson_build.read_text(encoding="utf-8")
    )

    fake_repo = tmp_path / "repo"
    (fake_repo / "bottles" / "frontend").mkdir(parents=True)
    (fake_repo / "meson.build").write_text(
        "project('other', version: '12.3')\n", encoding="utf-8"
    )
    template = _source_root / "bottles/frontend/params.py"
    (fake_repo / "bottles" / "frontend" / "params.py").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8"
    )

    spec = importlib.util.spec_from_file_location(
        "params_unconfigured", fake_repo / "bottles" / "frontend" / "params.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.chdir(fake_repo)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    assert module.APP_VERSION == "12.3"
    assert module.APP_MAJOR_VERSION == "12"
    assert module.APP_MINOR_VERSION == "3"


def test_unresolvable_version_falls_back_to_unknown():
    """Without any meson.build the module degrades gracefully."""
    template = _source_root / "bottles/frontend/params.py"
    spec = importlib.util.spec_from_file_location("params_orphan", template)
    module = importlib.util.module_from_spec(spec)
    patcher = pytest.MonkeyPatch()
    try:
        # Deny every meson.build probe (both module-relative and cwd) so the
        # template cannot resolve a version from the source tree.
        real_isfile = os.path.isfile

        def fake_isfile(p):
            return False if "meson.build" in os.fspath(p) else real_isfile(p)

        patcher.setattr(os.path, "isfile", fake_isfile)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    finally:
        patcher.undo()

    assert module.APP_VERSION == "unknown"
    assert module.APP_MAJOR_VERSION == "unknown"
    assert module.APP_MINOR_VERSION == "0"


def test_no_hardcoded_version_in_template():
    """The template must not carry a hardcoded version fallback."""
    template = (_source_root / "bottles/frontend/params.py").read_text(encoding="utf-8")
    assert not re.search(r'"60\.1"', template)
    assert not re.search(r'"60"', template)


def test_about_dialog_uses_full_app_version():
    """__show_about_dialog must not truncate the version to major.minor."""
    main_py = (_source_root / "bottles/frontend/main.py").read_text(encoding="utf-8")
    assert "set_version(APP_VERSION)" in main_py
    assert 'set_version(f"{APP_MAJOR_VERSION}' not in main_py


def test_main_no_longer_imports_major_minor():
    """The truncated-version imports should be gone from main.py."""
    main_py = (_source_root / "bottles/frontend/main.py").read_text(encoding="utf-8")
    assert "APP_MAJOR_VERSION" not in main_py
    assert "APP_MINOR_VERSION" not in main_py

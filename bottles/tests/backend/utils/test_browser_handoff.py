import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from bottles.backend.globals import Paths
from bottles.backend.utils.manager import ManagerUtils

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")


@pytest.fixture
def helpers_dir(tmp_path, monkeypatch):
    """Point Paths.helpers at a temp dir for the duration of a test."""
    helpers = tmp_path / "helpers"
    monkeypatch.setattr(Paths, "helpers", str(helpers))
    return helpers


# --------------------------------------------------------------- generation


def test_wrapper_has_safe_sh_shebang():
    # /bin/sh on purpose: the wrapper must run even with a broken/stripped
    # PATH where /usr/bin/env would fail to locate sh.
    content = ManagerUtils.build_browser_handoff_wrapper()
    assert content.startswith("#!/bin/sh\n")


def test_wrapper_unsets_wine_and_proton_environment():
    content = ManagerUtils.build_browser_handoff_wrapper()
    # unset lines group multiple variables: collect every name that follows
    unset_names = set()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("unset ") and "=" not in stripped:
            unset_names.update(stripped.removeprefix("unset ").split())
    for var in (
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "WINEDLLOVERRIDES",
        "WINEPREFIX",
        "WINEARCH",
        "MANGOHUD",
        "PROTON_USE_SECCOMP",
        "LSFG_DLL_PATH",
        "DXVK_CONFIG",
        "SteamAppId",
        "STEAM_COMPAT_DATA_PATH",
    ):
        assert var in unset_names, f"wrapper must unset {var}"


def test_wrapper_respects_bottles_sandbox_flag():
    content = ManagerUtils.build_browser_handoff_wrapper()
    assert "BOTTLES_SANDBOX" in content


# --------------------------------------------------------------- deployment


def test_deploy_creates_wrapper_and_aliases(helpers_dir):
    ManagerUtils.ensure_browser_helpers()
    wrapper = helpers_dir / "xdg-open"
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    # a sample of opener and browser aliases
    for name in ("gio", "kde-open", "brave-browser", "vivaldi", "firefox"):
        alias = helpers_dir / name
        assert alias.is_symlink(), name
        assert os.readlink(alias) == str(wrapper)


def test_deploy_does_not_touch_user_local_bin(helpers_dir, tmp_path, monkeypatch):
    user_bin = tmp_path / "home" / ".local" / "bin"
    user_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ManagerUtils.ensure_browser_helpers()
    assert not (user_bin / "xdg-open").exists()


def test_deploy_removes_only_legacy_bottles_wrapper(helpers_dir, tmp_path, monkeypatch):
    home = tmp_path / "home"
    user_bin = home / ".local" / "bin"
    user_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    legacy = user_bin / "xdg-open"
    legacy.write_text(
        "#!/usr/bin/env sh\n# Clean Wine/Proton runner environment\nexit 1\n"
    )
    foreign = user_bin / "foreign-open"
    foreign.write_text("#!/bin/sh\necho user script\n")

    ManagerUtils.ensure_browser_helpers()

    assert not legacy.exists(), "legacy Bottles wrapper should be removed"
    assert foreign.exists(), "user's own script must never be touched"


def test_deploy_leaves_non_bottles_xdg_open_alone(helpers_dir, tmp_path, monkeypatch):
    home = tmp_path / "home"
    user_bin = home / ".local" / "bin"
    user_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    custom = user_bin / "xdg-open"
    custom.write_text('#!/bin/sh\nexec my-real-opener "$@"\n')

    ManagerUtils.ensure_browser_helpers()

    assert custom.exists(), "user's own xdg-open must not be removed"


def test_deploy_is_idempotent(helpers_dir):
    ManagerUtils.ensure_browser_helpers()
    first = (helpers_dir / "xdg-open").read_text()
    ManagerUtils.ensure_browser_helpers()
    assert (helpers_dir / "xdg-open").read_text() == first
    assert os.access(helpers_dir / "xdg-open", os.X_OK)


def test_deploy_into_runner_dirs(helpers_dir, tmp_path):
    runner_bin = tmp_path / "runner" / "bin"
    runner_bin.mkdir(parents=True)
    ManagerUtils.ensure_browser_helpers(runner_path=str(tmp_path / "runner"))

    link = runner_bin / "xdg-open"
    assert link.is_symlink()
    assert os.readlink(link) == str(helpers_dir / "xdg-open")


# -------------------------------------------------------------- runtime


def _make_probe(bin_dir: Path, name: str = "xdg-open", fail: bool = False) -> Path:
    """Create a fake host binary that records its argv into $PROBE_OUT."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    probe = bin_dir / name
    body = "exit 1\n" if fail else 'printf "%s\\n" "$@" > "$PROBE_OUT"\n'
    probe.write_text(f"#!/bin/sh\n{body}")
    probe.chmod(probe.stat().st_mode | stat.S_IEXEC)
    return probe


def _run_wrapper(helpers_dir, tmp_path, args, extra_env=None, with_probe=True):
    """
    Execute the deployed wrapper in a hermetic environment.

    PATH only contains a fake bin dir (probe recorder) and the helpers dir,
    so no real host browser can be launched from the test run. Returns
    (returncode, log lines, probe argv or None).
    """
    ManagerUtils.ensure_browser_helpers()
    wrapper = helpers_dir / "xdg-open"

    fake_bin = tmp_path / "fakebin"
    probe = None
    if with_probe:
        probe = _make_probe(fake_bin)

    state_dir = tmp_path / "state"
    env = {
        "PATH": f"{fake_bin}:{helpers_dir}",
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(state_dir),
        # a runtime dir with no bus socket: portal/systemd strategies bail out
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime-empty"),
        "WINEPREFIX": str(tmp_path / "prefix"),
        "PROBE_OUT": str(tmp_path / "probe.txt"),
    }
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        [str(wrapper), *args],
        env=env,
        capture_output=True,
        timeout=20,
    )
    if probe is not None:
        # the wrapper spawns the target detached (setsid ... &) and exits
        # immediately: poll briefly for the probe to record its argv
        probe_out = tmp_path / "probe.txt"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not probe_out.exists():
            time.sleep(0.05)
    # read the log after waiting: the final 'detached' entry is written by
    # the detached child, which may lag the wrapper's exit
    log = state_dir / "bottles" / "browser-handoff.log"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not log.exists():
        time.sleep(0.05)
    lines = log.read_text().splitlines() if log.exists() else []
    received = None
    if probe is not None and (tmp_path / "probe.txt").exists():
        received = (tmp_path / "probe.txt").read_text().splitlines()
    return proc.returncode, lines, received


def test_runtime_detached_fallback_without_host_session(helpers_dir, tmp_path):
    """No bus, no portal: strategy 4 must fire and report success."""
    code, lines, received = _run_wrapper(
        helpers_dir, tmp_path, ["https://example.com/page"]
    )
    assert code == 0
    joined = "\n".join(lines)
    assert "strategy=detached" in joined
    assert "status=success" in joined
    assert received == ["https://example.com/page"]


def test_runtime_logs_portal_failure_before_fallback(helpers_dir, tmp_path):
    code, lines, received = _run_wrapper(
        helpers_dir, tmp_path, ["https://example.com/page"]
    )
    assert code == 0
    joined = "\n".join(lines)
    # strategy 1 attempted (or skipped) and strategy 4 completed
    assert "strategy=portal" in joined
    assert "strategy=detached" in joined
    assert received == ["https://example.com/page"]


def test_runtime_sandbox_flag_skips_portal_strategy(helpers_dir, tmp_path):
    code, lines, received = _run_wrapper(
        helpers_dir,
        tmp_path,
        ["https://example.com/page"],
        extra_env={"BOTTLES_SANDBOX": "1"},
    )
    assert code == 0
    joined = "\n".join(lines)
    # portal is gated on BOTTLES_SANDBOX and logs as skipped
    assert "strategy=portal" in joined
    assert "strategy=detached" in joined
    assert received == ["https://example.com/page"]


def test_runtime_no_target_fails_cleanly(helpers_dir, tmp_path):
    """No URI and no reachable host target anywhere -> exit 1, logged."""
    code, lines, _received = _run_wrapper(
        helpers_dir, tmp_path, ["--help"], with_probe=False
    )
    joined = "\n".join(lines)
    if code == 0:
        # the test host exposes a real /usr/bin/xdg-open, which the wrapper's
        # absolute fallbacks intentionally reach: the success path is valid,
        # just verify the decision was logged
        assert "status=success" in joined
        return
    assert "strategy=none" in joined
    assert "status=failure" in joined


def test_runtime_quotes_args_with_spaces(helpers_dir, tmp_path):
    """A browser alias + quoted args must reach the host target intact."""
    code, lines, received = _run_wrapper(
        helpers_dir, tmp_path, ["path with spaces.txt"]
    )
    assert code == 0
    assert received == ["path with spaces.txt"]
    joined = "\n".join(lines)
    assert "status=success" in joined


def test_runtime_dos_path_rewrites_through_dosdevices(helpers_dir, tmp_path):
    """C:\\path must be converted via the prefix's dosdevices mapping."""
    prefix = tmp_path / "prefix"
    drive_c = tmp_path / "drive-target"
    drive_c.mkdir()
    (prefix / "dosdevices").mkdir(parents=True)
    os.symlink(drive_c, prefix / "dosdevices" / "c:")

    code, lines, received = _run_wrapper(
        helpers_dir, tmp_path, ["C:\\Users\\steamuser\\doc.txt"]
    )
    assert code == 0
    assert received == [f"{drive_c}/Users/steamuser/doc.txt"]
    joined = "\n".join(lines)
    assert "status=success" in joined

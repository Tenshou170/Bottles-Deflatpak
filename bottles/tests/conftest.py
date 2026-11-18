import atexit
import os
import shutil
import sys
import tempfile


def _setup_test_env() -> None:
    this_dir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(this_dir, os.pardir, os.pardir))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Some upstream tests read TMPDIR directly; not every session exports it.
    # Point it at a user-owned directory: upstream's bridge-readiness code
    # fchmod()s the socket's parent directory, which fails with EPERM on a
    # root-owned shared /tmp when tests run as a regular user (upstream CI
    # runs as root, so their suite never sees this).
    tmpdir = tempfile.mkdtemp(prefix="bottles-tests-")
    os.environ["TMPDIR"] = tmpdir
    atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)

    build_data = os.path.join(repo_root, "build", "data")
    data_dir = os.path.join(repo_root, "data")
    if os.path.isfile(os.path.join(build_data, "gschemas.compiled")):
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", build_data)
    elif os.path.isfile(os.path.join(data_dir, "gschemas.compiled")):
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", data_dir)
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")

    # Upstream's frontend tests register the compiled UI resource, defaulting
    # to the Flatpak prefix (/app). This fork runs from a local build or a
    # system install, so resolve the resource here instead of asking every
    # test module to guess the layout.
    for candidate in (
        os.path.join(
            repo_root, "build", "bottles", "frontend", "ui", "bottles.gresource"
        ),
        os.path.join(repo_root, "build", "bottles.gresource"),
        "/usr/share/bottles/bottles.gresource",
    ):
        if os.path.isfile(candidate):
            os.environ.setdefault("BOTTLES_TEST_RESOURCE", candidate)
            break


_setup_test_env()

import os
import sys
import tempfile


def _setup_test_env() -> None:
    this_dir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(this_dir, os.pardir, os.pardir))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Some upstream tests read TMPDIR directly; not every session exports it.
    os.environ.setdefault("TMPDIR", tempfile.gettempdir())

    build_data = os.path.join(repo_root, "build", "data")
    data_dir = os.path.join(repo_root, "data")
    if os.path.isfile(os.path.join(build_data, "gschemas.compiled")):
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", build_data)
    elif os.path.isfile(os.path.join(data_dir, "gschemas.compiled")):
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", data_dir)
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")


_setup_test_env()

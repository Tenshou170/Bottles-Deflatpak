# Application details
APP_NAME = "@APP_NAME@"
if APP_NAME == "@APP_NAME@":
    APP_NAME = "Bottles"

APP_NAME_LOWER = APP_NAME.lower()

BASE_ID = "@BASE_ID@"
if BASE_ID == "@BASE_ID@":
    BASE_ID = "com.usebottles.bottles"

APP_ID = "@APP_ID@"
if APP_ID == "@APP_ID@":
    APP_ID = BASE_ID


def _parse_project_version(content: str) -> str:
    """Extract the project() version from a meson.build's content."""
    import re

    # The negative lookbehind avoids matching e.g. "meson_version:"; the
    # first occurrence in the file always belongs to project(), which
    # meson requires to be the first statement.
    match = re.search(r"(?<![\w_])version:\s*(['\"])([^'\"]+)\1", content)
    return match.group(2) if match else "unknown"


def _project_version() -> str:
    """Read the version from meson.build when running from a source tree."""
    from os import path

    for candidate in (
        path.join(path.dirname(__file__), "..", "..", "meson.build"),
        "meson.build",
    ):
        meson_build = path.normpath(path.abspath(candidate))
        if not path.isfile(meson_build):
            continue
        try:
            with open(meson_build, encoding="utf-8") as f:
                return _parse_project_version(f.read())
        except OSError:
            continue
    return "unknown"


# Guards use startswith("@") rather than comparing against the literal
# placeholder: Meson replaces every occurrence of @VAR@, including the
# one inside the guard itself, so a literal comparison can never be true
# in a configured build.
APP_VERSION = "@APP_VERSION@"
if APP_VERSION.startswith("@"):
    APP_VERSION = _project_version()

APP_MAJOR_VERSION = "@APP_MAJOR_VERSION@"
if APP_MAJOR_VERSION.startswith("@"):
    APP_MAJOR_VERSION = APP_VERSION.split(".")[0]

APP_MINOR_VERSION = "@APP_MINOR_VERSION@"
if APP_MINOR_VERSION.startswith("@"):
    _minor_parts = APP_VERSION.split(".")
    APP_MINOR_VERSION = _minor_parts[1] if len(_minor_parts) > 1 else "0"

APP_ICON = APP_ID
PROFILE = "@PROFILE@"
if PROFILE == "@PROFILE@":
    PROFILE = "default"

# Internal settings not user editable
ANIM_DURATION = 120

# General purpose definitions
EXECUTABLE_EXTS = (".exe", ".msi", ".bat", ".lnk")

# URLs
DOC_URL = "https://docs.usebottles.com"

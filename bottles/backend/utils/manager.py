# manager.py
#
# Copyright 2025 mirkobrombin <brombin94@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from gettext import gettext as _
from glob import glob
from typing import Optional

import icoextract  # type: ignore [import-untyped]

from bottles.backend.params import APP_ID
from bottles.backend.globals import Paths
from bottles.backend.logger import Logger
from bottles.backend.models.config import BottleConfig
from bottles.backend.models.result import Result
from bottles.backend.state import SignalManager, Signals
from bottles.backend.utils.generic import get_mime
from bottles.backend.utils.imagemagick import ImageMagickUtils

from gi.repository import GLib, Gio


logging = Logger()

try:
    from gi.repository import Xdp

    portal = Xdp.Portal()
    portal_available = True
except (ImportError, ValueError):
    Xdp = None
    portal = None
    portal_available = False
    logging.warning(
        "libportal (gi.repository.Xdp) not available — "
        "desktop entry creation will fall back to direct file writing."
    )


class ManagerUtils:
    """
    This class contains methods (tools, utilities) that are not
    directly related to the Manager.
    """

    external_runner_paths: dict[str, str] = {}

    @staticmethod
    def open_filemanager(
        config: Optional[BottleConfig] = None,
        path_type: str = "bottle",
        component: str = "",
        custom_path: str = "",
    ):
        logging.info("Opening the file manager in the path …")
        path = ""

        if path_type == "bottle" and config is None:
            raise NotImplementedError("bottle type need a valid Config")

        if path_type == "bottle":
            bottle_path = ManagerUtils.get_bottle_path(config)
            if config.Environment == "Steam":
                bottle_path = config.Path
            path = f"{bottle_path}/drive_c"
        elif component != "":
            if path_type in ["runner", "runner:proton"]:
                path = ManagerUtils.get_runner_path(component)
            elif path_type == "d7vk":
                path = ManagerUtils.get_d7vk_path(component)
            elif path_type == "dxvk":
                path = ManagerUtils.get_dxvk_path(component)
            elif path_type == "vkd3d":
                path = ManagerUtils.get_vkd3d_path(component)
            elif path_type == "nvapi":
                path = ManagerUtils.get_nvapi_path(component)
            elif path_type == "latencyflex":
                path = ManagerUtils.get_latencyflex_path(component)
            elif path_type == "runtime":
                path = Paths.runtimes
            elif path_type == "winebridge":
                path = Paths.winebridge

        if path_type == "custom" and custom_path != "":
            path = custom_path

        uri = Gio.File.new_for_path(path).get_uri()
        SignalManager.send(Signals.GShowUri, Result(data=uri))

    @staticmethod
    def get_bottle_path(config: BottleConfig) -> str:
        if config.Environment == "Steam":
            return os.path.join(Paths.steam, config.CompatData)

        if config.Custom_Path:
            return config.Path

        return os.path.join(Paths.bottles, config.Path)

    @staticmethod
    def resolve_portal_path(path: str) -> str:
        """
        Resolve a document portal path (/run/user/<uid>/doc/<id>/...) to its real
        host path through the Documents portal when the host path is accessible
        inside the sandbox. Returns the original path unchanged on any failure.
        """
        if not path or "/run/user/" not in path or "/doc/" not in path:
            return path

        resolved = ManagerUtils.get_portal_host_path(path)
        return resolved if resolved and os.path.exists(resolved) else path

    @staticmethod
    def is_portal_document_path(path: str) -> bool:
        if not path:
            return False

        portal_root = f"/run/user/{os.getuid()}/doc"
        normalized = os.path.normpath(path)
        if normalized != path:
            return False

        try:
            if os.path.commonpath((portal_root, normalized)) != portal_root:
                return False
        except ValueError:
            return False

        relative_path = os.path.relpath(normalized, portal_root)
        if len(relative_path.split(os.sep)) < 2:
            return False

        return (
            os.path.isfile(normalized)
            and not os.path.islink(normalized)
            and os.path.realpath(normalized) == normalized
        )

    @staticmethod
    def get_portal_host_path(path: str) -> Optional[str]:
        """Return the host path represented by a document portal path."""
        if not path or "/run/user/" not in path or "/doc/" not in path:
            return path

        def _to_str(value) -> str:
            if isinstance(value, bytes):
                raw = value
            elif isinstance(value, (list, tuple)):
                raw = bytes(value)
            else:
                return str(value)
            return raw.rstrip(b"\x00").decode("utf-8", "replace")

        try:
            documents = "org.freedesktop.portal.Documents"
            proxy = Gio.DBusProxy.new_sync(
                Gio.bus_get_sync(Gio.BusType.SESSION, None),
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
                None,
                documents,
                "/org/freedesktop/portal/documents",
                documents,
                None,
            )

            mount = _to_str(
                proxy.call_sync(
                    "GetMountPoint", None, Gio.DBusCallFlags.NONE, -1, None
                ).unpack()[0]
            )
            if not mount or not path.startswith(mount + "/"):
                return None

            doc_id, _, remainder = path[len(mount) + 1 :].partition("/")

            hosts = proxy.call_sync(
                "GetHostPaths",
                GLib.Variant("(as)", ([doc_id],)),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            ).unpack()[0]
            if doc_id not in hosts:
                return None

            host = _to_str(hosts[doc_id])
            exported_name, _, nested_path = remainder.partition("/")
            if exported_name and os.path.basename(host) != exported_name:
                return None
            if nested_path:
                resolved = os.path.normpath(os.path.join(host, nested_path))
                if os.path.commonpath((host, resolved)) != os.path.normpath(host):
                    return None
                return resolved
            return host
        except Exception as e:
            logging.warning(f"Could not resolve document portal path: {e}")
            return None

    @staticmethod
    def get_runner_path(runner: str) -> str:
        if runner.startswith("sys-") or runner.startswith("/"):
            return runner
        if runner in ManagerUtils.external_runner_paths:
            return ManagerUtils.external_runner_paths[runner]
        return f"{Paths.runners}/{runner}"

    @staticmethod
    def set_external_runner_paths(runners: dict[str, str]) -> None:
        ManagerUtils.external_runner_paths = runners.copy()

    @staticmethod
    def get_dxvk_path(dxvk: str) -> str:
        return f"{Paths.dxvk}/{dxvk}"

    @staticmethod
    def get_d7vk_path(d7vk: str) -> str:
        return f"{Paths.d7vk}/{d7vk}"

    @staticmethod
    def get_vkd3d_path(vkd3d: str) -> str:
        return f"{Paths.vkd3d}/{vkd3d}"

    @staticmethod
    def get_nvapi_path(nvapi: str) -> str:
        return f"{Paths.nvapi}/{nvapi}"

    @staticmethod
    def get_latencyflex_path(latencyflex: str) -> str:
        return f"{Paths.latencyflex}/{latencyflex}"

    @staticmethod
    def get_temp_path(dest: str) -> str:
        return f"{Paths.temp}/{dest}"

    @staticmethod
    def get_template_path(template: str) -> str:
        return f"{Paths.templates}/{template}"

    @staticmethod
    def move_file_to_bottle(
        file_path: str, config: BottleConfig, fn_update: callable = None
    ) -> str | bool:
        logging.info(f"Adding file {file_path} to the bottle …")
        bottle_path = ManagerUtils.get_bottle_path(config)

        if not os.path.exists(f"{bottle_path}/storage"):
            """
            If the storage folder does not exist for the bottle,
            create it before moving the file.
            """
            os.makedirs(f"{bottle_path}/storage")

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        file_new_path = f"{bottle_path}/storage/{file_name}"

        logging.info(f"Copying file {file_path} to the bottle …")
        try:
            if file_size == 0:
                with open(file_new_path, "wb"):
                    pass
                if fn_update:
                    fn_update(1)
                return file_new_path

            chunk_size = 64 * 1024
            bytes_copied = 0
            with open(file_path, "rb") as f_in:
                with open(file_new_path, "wb") as f_out:
                    while True:
                        chunk = f_in.read(chunk_size)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        bytes_copied += len(chunk)

                        if fn_update:
                            fn_update(bytes_copied / file_size)

                    if fn_update:
                        fn_update(1)
            return file_new_path
        except (OSError, IOError):
            logging.error(f"Could not copy file {file_path} to the bottle.")
            return False

    @staticmethod
    def get_exe_parent_dir(config, executable_path):
        """Get parent directory of the executable."""
        if "\\" in executable_path:
            p = "\\".join(executable_path.split("\\")[:-1])
            p = p.replace("C:\\", "\\drive_c\\").replace("\\", "/")
            return ManagerUtils.get_bottle_path(config) + p
        return os.path.dirname(executable_path)

    @staticmethod
    def extract_icon(config: BottleConfig, program_name: str, program_path: str) -> str:
        from bottles.backend.wine.winepath import WinePath

        winepath = WinePath(config)
        icon = "com.usebottles.bottles-program"
        bottle_icons_path = os.path.join(ManagerUtils.get_bottle_path(config), "icons")

        try:
            if winepath.is_windows(program_path):
                program_path = winepath.to_unix(program_path)

            ico_dest_temp = os.path.join(bottle_icons_path, f"_{program_name}.png")
            ico_dest = os.path.join(bottle_icons_path, f"{program_name}.png")
            ico = icoextract.IconExtractor(program_path)
            os.makedirs(bottle_icons_path, exist_ok=True)

            if os.path.exists(ico_dest_temp):
                os.remove(ico_dest_temp)

            if os.path.exists(ico_dest):
                os.remove(ico_dest)

            ico.export_icon(ico_dest_temp)
            if get_mime(ico_dest_temp) == "image/vnd.microsoft.icon":
                if not ico_dest_temp.endswith(".ico"):
                    shutil.move(ico_dest_temp, f"{ico_dest_temp}.ico")
                    ico_dest_temp = f"{ico_dest_temp}.ico"
                im = ImageMagickUtils(ico_dest_temp)
                im.convert(ico_dest)
                os.remove(ico_dest_temp)
                if os.path.isfile(ico_dest):
                    icon = ico_dest
            else:
                shutil.move(ico_dest_temp, ico_dest)
                icon = ico_dest
        except Exception as e:
            logging.error(f"Could not extract icon for {program_name}: {e}")

        return icon

    @staticmethod
    def create_desktop_entry(
        config,
        program: dict,
        skip_icon: bool = False,
        custom_icon: str = "",
        callback: Callable[[Result], None] | None = None,
        on_created=None,
        on_failed=None,
        on_cancelled=None,
    ):
        """
        Create a desktop entry for the program.
        """
        icon = "com.usebottles.bottles-program"

        if not skip_icon and not custom_icon:
            icon = ManagerUtils.extract_icon(
                config, program.get("name"), program.get("path")
            )
        elif custom_icon:
            icon = custom_icon

        def notify(result: Result) -> None:
            if callback:
                callback(result)
                return
            SignalManager.send(Signals.DesktopEntryCreated, result)

        try:
            portal_exec_cmd = ManagerUtils.get_desktop_entry_exec(
                config, program, for_host=True
            )
            portal_content = ManagerUtils.build_desktop_entry(
                config, program, portal_exec_cmd
            )
        except ValueError as error:
            logging.error(f"Failed to create desktop entry: {error}")
            notify(Result(False, message=str(error)))
            if on_failed:
                on_failed()
            return

        def notify_created(method: str, paths=None, message: str = ""):
            data = {"method": method}
            if paths is not None:
                data["paths"] = paths
            notify(Result(True, data=data, message=message))
            if on_created:
                on_created()

        def notify_failed(message: str = "", method=None, paths=None):
            data = {}
            if method is not None:
                data["method"] = method
            if paths is not None:
                data["paths"] = paths
            notify(Result(False, data=data, message=message))
            if on_failed:
                on_failed()

        def create_manual_fallback(icon_path):
            """Create desktop entry manually when portal is unavailable."""
            filename = ManagerUtils.get_desktop_entry_filename(config, program)
            _, mime_types, _ = ManagerUtils.resolve_file_associations(
                program.get("file_extensions", [])
            )
            default_apps = {
                mime_type: Gio.AppInfo.get_default_for_type(mime_type, False)
                for mime_type in mime_types
            }
            host_exec_cmd = ManagerUtils.get_desktop_entry_exec(
                config, program, for_host=True
            )
            if icon_path == "com.usebottles.bottles-program":
                icon_path = APP_ID
            content = ManagerUtils.build_desktop_entry(
                config,
                program,
                host_exec_cmd,
                icon_path,
            )
            paths = []
            errors = []

            # Write to application menu (~/.local/share/applications)
            apps_dir = os.path.expanduser("~/.local/share/applications")
            apps_path = os.path.join(apps_dir, filename)
            try:
                os.makedirs(apps_dir, exist_ok=True)
                with open(apps_path, "w") as f:
                    f.write(content)
                paths.append(apps_path)
                logging.info(f"Desktop entry created at {apps_path}")
                ManagerUtils.update_desktop_database(apps_dir, default_apps)
            except OSError as e:
                errors.append(str(e))
                logging.error(f"Failed to write desktop entry to applications: {e}")
                notify_failed("\n".join(errors), "manual", paths)
                return

            # Also write a shortcut to the desktop surface
            desktop_dir = GLib.get_user_special_dir(
                GLib.UserDirectory.DIRECTORY_DESKTOP
            )
            if desktop_dir:
                desktop_path = os.path.join(desktop_dir, filename)
                try:
                    with open(desktop_path, "w") as f:
                        f.write(content)
                    # Mark executable so KDE/GNOME desktop shells will run it
                    os.chmod(desktop_path, 0o755)
                    paths.append(desktop_path)
                    logging.info(f"Desktop shortcut created at {desktop_path}")
                except OSError as e:
                    errors.append(str(e))
                    logging.error(f"Failed to write desktop shortcut: {e}")

            notify_created("manual", paths, "\n".join(errors))

        portal_entry_state = ManagerUtils.get_portal_desktop_entry_state(
            config, program
        )
        if portal_entry_state is None:
            logging.warning("Could not determine the Dynamic Launcher state.")
            notify_failed()
            return

        def prepare_install_cb(self, result):
            # Handle portal preparation failure (e.g., KDE's broken implementation)
            try:
                ret = portal.dynamic_launcher_prepare_install_finish(result)
                if ret is None:
                    logging.info("Dynamic Launcher portal request cancelled.")
                    if on_cancelled:
                        on_cancelled()
                    return
            except GLib.Error as e:
                if ManagerUtils.is_cancelled_portal_error(e):
                    logging.info("Dynamic Launcher portal request cancelled.")
                    if on_cancelled:
                        on_cancelled()
                    return
                logging.warning(
                    f"Dynamic Launcher portal preparation failed: {e}. "
                    "Using the manual launcher when safe."
                )
                if portal_entry_state is False:
                    create_manual_fallback(icon)
                else:
                    notify_failed()
                return

            try:
                portal.dynamic_launcher_install(
                    ret["token"],
                    ManagerUtils.get_desktop_entry_id(config, program),
                    portal_content,
                )
                if not ManagerUtils.remove_manual_desktop_entry(config, program):
                    notify_failed()
                    return
                notify_created("portal")
            except GLib.Error as e:
                logging.warning(
                    f"Dynamic Launcher portal install failed: {e}. "
                    "Using the manual launcher when safe."
                )
                if portal_entry_state is False:
                    create_manual_fallback(icon)
                else:
                    notify_failed()

        if icon != "com.usebottles.bottles-program" and not os.path.exists(icon):
            logging.warning(f"Icon file not found: {icon}. Falling back to default.")
            icon = "com.usebottles.bottles-program"

        try:
            ManagerUtils.validate_desktop_entry_value(icon)
        except ValueError as error:
            logging.error(f"Failed to create desktop entry: {error}")
            notify_failed()
            return

        if icon == "com.usebottles.bottles-program":
            _icon = Gio.File.new_for_uri(
                f"resource:/com/usebottles/bottles/icons/scalable/apps/{icon}.svg"
            )
        else:
            _icon = Gio.File.new_for_path(icon)
        icon_v = Gio.BytesIcon.new(_icon.load_bytes()[0]).serialize()
        try:
            portal.dynamic_launcher_prepare_install(
                None,
                program.get("name"),
                icon_v,
                Xdp.LauncherType.APPLICATION,
                None,
                True,
                False,
                None,
                prepare_install_cb,
            )
        except GLib.Error as error:
            logging.warning(f"Dynamic Launcher portal request failed: {error}")
            if portal_entry_state is False:
                create_manual_fallback(icon)
            else:
                notify_failed()

    @staticmethod
    def resolve_file_associations(value) -> tuple[list[str], list[str], list[str]]:
        if isinstance(value, str):
            candidates = [part for part in re.split(r"[\s,;]+", value) if part]
        elif isinstance(value, list):
            candidates = value
        else:
            candidates = []

        extensions = []
        mime_types = []
        invalid = []
        extension_pattern = re.compile(r"\.[a-z0-9][a-z0-9._+-]{0,31}")

        for candidate in candidates:
            if not isinstance(candidate, str):
                invalid.append(str(candidate))
                continue

            extension = candidate.strip().lower()
            if not extension.startswith("."):
                extension = f".{extension}"

            if (
                not extension_pattern.fullmatch(extension)
                or ".." in extension
                or extension.endswith(".")
            ):
                invalid.append(candidate)
                continue

            content_type, _uncertain = Gio.content_type_guess(f"file{extension}", None)
            mime_type = Gio.content_type_get_mime_type(content_type)
            if not mime_type or mime_type == "application/octet-stream":
                invalid.append(candidate)
                continue

            if extension not in extensions:
                extensions.append(extension)
            if mime_type not in mime_types:
                mime_types.append(mime_type)

        return extensions, mime_types, invalid

    @staticmethod
    def get_desktop_entry_exec(config, program: dict, for_host: bool = False) -> str:
        umu_game = program.get("umu_game")
        if umu_game:
            return "{} umu run --game {}".format(
                "bottles-cli",
                ManagerUtils.quote_desktop_entry_exec_arg(umu_game),
            )

        _, mime_types, _ = ManagerUtils.resolve_file_associations(
            program.get("file_extensions", [])
        )
        field_code = "%f" if mime_types else "%u"

        return "{} run -p {} -b {} -- {}".format(
            "bottles-cli",
            ManagerUtils.quote_desktop_entry_exec_arg(program.get("name", "")),
            ManagerUtils.quote_desktop_entry_exec_arg(config.get("Name", "")),
            field_code,
        )

    @staticmethod
    def quote_desktop_entry_exec_arg(value: str) -> str:
        value = ManagerUtils.validate_desktop_entry_value(value)
        value = value.replace("\\", "\\\\")
        for character in ('"', "`", "$"):
            value = value.replace(character, f"\\{character}")
        value = value.replace("%", "%%")
        return f'"{value}"'

    @staticmethod
    def validate_desktop_entry_value(value) -> str:
        value = str(value or "")
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("desktop entry values cannot contain control characters")
        return value

    @staticmethod
    def update_desktop_database(
        applications_dir: str, default_apps: Optional[dict] = None
    ) -> None:
        updater = shutil.which("update-desktop-database")
        if not updater:
            return

        try:
            result = subprocess.run(
                [updater, applications_dir],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode != 0:
                logging.warning(
                    f"Failed to update the desktop database: {result.stderr.strip()}"
                )
                return

            for mime_type, app_info in (default_apps or {}).items():
                if app_info is None:
                    continue
                if not app_info.set_as_default_for_type(mime_type):
                    logging.warning(
                        f"Failed to preserve the default application for {mime_type}."
                    )
        except OSError as error:
            logging.warning(f"Failed to update the desktop database: {error}")
        except GLib.Error as error:
            logging.warning(f"Failed to preserve a default application: {error}")

    @staticmethod
    def build_desktop_entry(
        config,
        program: dict,
        exec_cmd: str,
        icon_path: Optional[str] = None,
    ) -> str:
        _, mime_types, _ = ManagerUtils.resolve_file_associations(
            program.get("file_extensions", [])
        )
        name = ManagerUtils.validate_desktop_entry_value(program.get("name", ""))
        executable = ManagerUtils.validate_desktop_entry_value(
            program.get("executable", "")
        )
        exec_cmd = ManagerUtils.validate_desktop_entry_value(exec_cmd)

        desktop_entry = GLib.KeyFile()
        group = "Desktop Entry"
        desktop_entry.set_string(group, "Exec", exec_cmd)
        desktop_entry.set_string(group, "Type", "Application")
        desktop_entry.set_boolean(group, "Terminal", False)
        desktop_entry.set_string(group, "Categories", "Game;")
        desktop_entry.set_string(group, "Comment", f"Launch {name} using Bottles.")
        wm_class = executable.lower()
        app_id = ManagerUtils.get_program_steam_app_id(config, program)
        if app_id:
            wm_class = f"steam_app_{app_id}"
        desktop_entry.set_string(group, "StartupWMClass", wm_class)
        if mime_types:
            desktop_entry.set_string(group, "MimeType", f"{';'.join(mime_types)};")
        if icon_path is not None:
            desktop_entry.set_string(group, "Name", name)
            desktop_entry.set_string(
                group,
                "Icon",
                ManagerUtils.validate_desktop_entry_value(icon_path),
            )
        return desktop_entry.to_data()[0]

    @staticmethod
    def get_program_steam_app_id(config, program: dict) -> str:
        environments = (
            program.get("environment"),
            config.get("Environment_Variables"),
        )
        for environment in environments:
            if not isinstance(environment, dict):
                continue
            app_id = environment.get("SteamAppId")
            if isinstance(app_id, str) and re.fullmatch(
                r"[A-Za-z0-9_.-]{1,117}", app_id
            ):
                return app_id

        return ""

    @staticmethod
    def get_desktop_entry_id(config, program: dict):
        launcher_id = f"{config.get('Name')}.{program.get('name')}"
        return "{}.App_{}.desktop".format(
            APP_ID,
            GLib.compute_checksum_for_string(
                GLib.ChecksumType.SHA1,
                launcher_id,
                -1,
            ),
        )

    @staticmethod
    def get_desktop_entry_filename(config, program: dict):
        safe_name = "".join(
            [c for c in program.get("name") if c.isalnum() or c in ("-", "_")]
        )
        safe_bottle = "".join(
            [c for c in config.get("Name") if c.isalnum() or c in ("-", "_")]
        )
        return f"bottles-{safe_bottle}-{safe_name}.desktop"

    @staticmethod
    def is_missing_portal_entry_error(error: GLib.Error) -> bool:
        return error.matches(
            GLib.file_error_quark(), GLib.FileError.NOENT
        ) or error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND)

    @staticmethod
    def is_cancelled_portal_error(error: GLib.Error) -> bool:
        return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)

    @staticmethod
    def get_portal_desktop_entry_state(config, program: dict) -> Optional[bool]:
        desktop_entry_id = ManagerUtils.get_desktop_entry_id(config, program)
        try:
            portal.dynamic_launcher_get_desktop_entry(desktop_entry_id)
            return True
        except GLib.Error as error:
            if ManagerUtils.is_missing_portal_entry_error(error):
                return False
            logging.warning(
                f"Failed to query Dynamic Launcher entry {desktop_entry_id}: {error}"
            )
            return None

    @staticmethod
    def get_manual_desktop_entry_paths(config, program: dict) -> tuple[str, list[str]]:
        desktop_entry_filename = ManagerUtils.get_desktop_entry_filename(
            config, program
        )
        applications_dir = os.path.expanduser("~/.local/share/applications")
        entry_paths = [os.path.join(applications_dir, desktop_entry_filename)]
        desktop_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
        if desktop_dir:
            entry_paths.append(os.path.join(desktop_dir, desktop_entry_filename))
        return applications_dir, entry_paths

    @staticmethod
    def remove_manual_desktop_entry(config, program: dict) -> bool:
        applications_dir, entry_paths = ManagerUtils.get_manual_desktop_entry_paths(
            config, program
        )
        application_removed = False
        success = True
        for entry_path in entry_paths:
            if not os.path.exists(entry_path):
                continue

            try:
                os.remove(entry_path)
                logging.info(f"Desktop entry removed: {entry_path}")
                if entry_path.startswith(applications_dir + os.sep):
                    application_removed = True
            except OSError as error:
                success = False
                logging.warning(f"Failed to remove desktop entry {entry_path}: {error}")

        if application_removed:
            ManagerUtils.update_desktop_database(applications_dir)
        return success

    @staticmethod
    def has_desktop_entry(config, program: dict) -> Optional[bool]:
        portal_state = ManagerUtils.get_portal_desktop_entry_state(config, program)
        if portal_state is True:
            return True

        _, entry_paths = ManagerUtils.get_manual_desktop_entry_paths(config, program)
        if any(os.path.exists(entry_path) for entry_path in entry_paths):
            return True
        return portal_state

    @staticmethod
    def remove_desktop_entry(config, program: dict) -> bool:
        desktop_entry_id = ManagerUtils.get_desktop_entry_id(config, program)
        portal_state = ManagerUtils.get_portal_desktop_entry_state(config, program)
        if portal_state is None:
            _, entry_paths = ManagerUtils.get_manual_desktop_entry_paths(
                config, program
            )
            if not any(os.path.exists(entry_path) for entry_path in entry_paths):
                return False
            return ManagerUtils.remove_manual_desktop_entry(config, program)
        if portal_state:
            try:
                portal.dynamic_launcher_uninstall(desktop_entry_id)
                logging.info(f"Desktop entry removed: {desktop_entry_id}")
            except GLib.Error as error:
                if not ManagerUtils.is_missing_portal_entry_error(error):
                    logging.warning(
                        f"Failed to remove desktop entry {desktop_entry_id}: {error}"
                    )
                    return False

        return ManagerUtils.remove_manual_desktop_entry(config, program)

    @staticmethod
    def get_autostart_programs(configs):
        programs = []
        for config in configs:
            for program in getattr(config, "External_Programs", {}).values():
                if (
                    program.get("autostart")
                    and program.get("id")
                    and not program.get("removed")
                ):
                    programs.append((config, program))
        return programs

    @staticmethod
    def set_autostart_entry(enabled: bool) -> bool:
        autostart_dir = os.path.join(GLib.get_user_config_dir(), "autostart")
        entry_path = os.path.join(autostart_dir, f"{APP_ID}.autostart.desktop")

        try:
            if not enabled:
                if os.path.exists(entry_path):
                    os.remove(entry_path)
                return True

            os.makedirs(autostart_dir, exist_ok=True)
            with open(entry_path, "w", encoding="utf-8") as entry:
                entry.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Bottles\n"
                    "Comment=Launch selected Bottles programs\n"
                    "Exec=bottles-cli autostart\n"
                    "Terminal=false\n"
                    "NoDisplay=true\n"
                )
            return True
        except OSError as error:
            logging.error(f"Failed to update the autostart entry: {error}")
            return False

    @staticmethod
    def browse_wineprefix(wineprefix: dict):
        """Presents a dialog to browse the wineprefix."""
        ManagerUtils.open_filemanager(
            path_type="custom", custom_path=wineprefix.get("Path")
        )

    @staticmethod
    def get_languages(
        from_name=None,
        from_locale=None,
        from_index=None,
        get_index=False,
        get_locales=False,
    ):
        locales = [
            "sys",
            "bg_BG",
            "cs_CZ",
            "da_DK",
            "de_DE",
            "el_GR",
            "en_US",
            "es_ES",
            "et_EE",
            "fi_FI",
            "fr_FR",
            "hr_HR",
            "hu_HU",
            "it_IT",
            "lt_LT",
            "lv_LV",
            "nl_NL",
            "no_NO",
            "pl_PL",
            "pt_PT",
            "ro_RO",
            "ru_RU",
            "sk_SK",
            "sl_SI",
            "sv_SE",
            "tr_TR",
            "zh_CN",
            "ja_JP",
            "zh_TW",
            "ko_KR",
        ]
        names = [
            _("System"),
            _("Bulgarian"),
            _("Czech"),
            _("Danish"),
            _("German"),
            _("Greek"),
            _("English"),
            _("Spanish"),
            _("Estonian"),
            _("Finnish"),
            _("French"),
            _("Croatian"),
            _("Hungarian"),
            _("Italian"),
            _("Lithuanian"),
            _("Latvian"),
            _("Dutch"),
            _("Norwegian"),
            _("Polish"),
            _("Portuguese"),
            _("Romanian"),
            _("Russian"),
            _("Slovak"),
            _("Slovenian"),
            _("Swedish"),
            _("Turkish"),
            _("Chinese (Simplified)"),
            _("Japanese"),
            _("Chinese (Traditional)"),
            _("Korean"),
        ]

        if from_name and from_locale:
            raise ValueError("Cannot pass both from_name, from_locale and from_index.")

        if from_name:
            if from_name not in names:
                raise ValueError("Given name not in list.")
            i = names.index(from_name)
            if get_index:
                return i
            return from_name, locales[i]

        if from_locale:
            if from_locale not in locales:
                raise ValueError("Given locale not in list.")
            i = locales.index(from_locale)
            if get_index:
                return i
            return from_locale, names[i]

        if isinstance(from_index, int):
            if from_index not in range(0, len(locales)):
                raise ValueError("Given index not in range.")
            return locales[from_index], names[from_index]

        if get_locales:
            return locales

        return names

    @staticmethod
    def build_browser_handoff_wrapper() -> str:
        """
        Return the POSIX sh script deployed as the browser handoff wrapper.

        Wine prefixes resolve URL handlers through winebrowser, which shells
        out to xdg-open (or a browser binary by name). Inside Bottles those
        names resolve to this wrapper, which must hand the request over to
        the host desktop session without leaking the Wine/Proton environment:
        injected DLL overrides, Steam Runtime LD_LIBRARY_PATH and Vulkan
        layer variables abort sandboxed Chromium browsers (Brave, Vivaldi,
        Chromium, Edge, ...) on startup, and Wine's spoofed USER breaks
        D-Bus activation.

        Strategies, in order:
          1. FreeDesktop OpenURI portal over the session bus
          2. transient systemd --user unit over the session bus
          3. desktop session launchers (kstart, gio open)
          4. sanitized detached execution as a last resort

        Strategies 1-2 are the important ones: they talk to the host session
        bus, so the browser is launched by the host session manager — outside
        the Wine process tree AND outside Bottles' bwrap namespace when the
        sandbox binds the bus socket (the browser then never runs inside the
        namespace, which is what aborts sandboxed Chromium browsers). When no
        reachable bus socket exists they are skipped and strategy 4 runs the
        browser in-place with a sanitized environment. The BOTTLES_SANDBOX
        marker is recorded in handoff logs for diagnostics only.
        Failures are appended to $XDG_STATE_HOME/bottles/browser-handoff.log
        for diagnostics.
        """
        return r"""#!/bin/sh
# Bottles browser handoff wrapper.
# Invoked as xdg-open/winebrowser-adjacent names from inside a Wine prefix
# (optionally inside Bottles' bwrap sandbox). Hands the request over to the
# host desktop session with a clean environment.
# NOTE: /bin/sh shebang on purpose — this script must be executable even
# when PATH does not contain a usable directory.

# POSIX parameter expansion instead of basename/dirname: this script must
# work even when PATH is broken or stripped (a not-uncommon Wine state).
CALLER="${0##*/}"
INSIDE_SANDBOX="${BOTTLES_SANDBOX:-0}"  # recorded for diagnostics only
case "$0" in
    */*) SELF_ARG_DIR="${0%/*}" ;;
    *) SELF_ARG_DIR="." ;;
esac
# Captured before the cleanup below: the DOS-path rewrite needs the prefix
# to resolve dosdevices mappings, but WINEPREFIX itself must not leak into
# the launched browser.
ORIG_WINEPREFIX="$WINEPREFIX"

# ---------------------------------------------------------------- logging
HANDOFF_LOG="${XDG_STATE_HOME:-$HOME/.local/state}/bottles/browser-handoff.log"
log_handoff() {
    # log_handoff <strategy> <status> <detail>
    # Create the directory first: the >> redirection below is evaluated
    # before the block would run, so it cannot create it itself.
    mkdir -p "$(dirname -- "$HANDOFF_LOG")" 2>/dev/null || return
    printf '%s caller=%s sandbox=%s strategy=%s status=%s uri=%s detail=%s\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CALLER" "$INSIDE_SANDBOX" \
            "$1" "$2" "${TARGET_URI:-none}" "$3" >> "$HANDOFF_LOG" 2>/dev/null
}

# ------------------------------------------------- clean host environment
# Wine/Proton/Steam-runtime injection variables abort sandboxed browsers
# and break D-Bus activation; drop them before any handover. The wrapper
# itself re-exports the session pieces it needs (display, bus, runtime dir).
unset LD_LIBRARY_PATH LD_PRELOAD WINEDLLOVERRIDES
unset WINEPREFIX WINEARCH WINEDEBUG WINELOADER WINESERVER WINEDLLPATH
unset WINEESYNC WINEFSYNC WINENTSYNC WINE_USE_EGL WINE_MOVE_HACK
unset WINE_DISABLE_FULLSCREEN_HACK WINE_LARGE_ADDRESS_AWARE
unset STAGING_SHARED_MEMORY PROTON_USE_SECCOMP PROTON_NO_STEAMINPUT
unset PROTON_USE_XALIA PROTON_LOG PROTON_LOG_DIR
unset PROTON_EAC_RUNTIME PROTON_BATTLEYE_RUNTIME
unset STEAM_COMPAT_DATA_PATH STEAM_COMPAT_CLIENT_INSTALL_PATH
unset STEAM_COMPAT_INSTALL_PATH SteamVirtualGamepadInfo
unset SteamAppId SteamGameId UMU_ID UMU_USE_STEAM
unset VK_ICD_FILENAMES VK_LAYER_PATH VK_ADD_LAYER_PATH
unset DXVK_CONFIG DXVK_CONFIG_FILE DXVK_HDR
unset VKD3D_FRAME_RATE VKD3D_SHADER_CACHE_PATH DXVK_SHADER_CACHE_PATH
unset DISABLE_LSFG DISABLE_LSFGVK LSFGVK_ENV LSFGVK_DLL_PATH
unset LSFGVK_MULTIPLIER LSFGVK_FLOW_SCALE LSFGVK_PERFORMANCE_MODE
unset LSFG_LEGACY LSFG_DLL_PATH LSFG_MULTIPLIER LSFG_FLOW_SCALE
unset LSFG_PERFORMANCE_MODE SODA_OPENXR_RUNTIME
unset FEX_APP_CONFIG FEX_APP_CONFIG_LOCATION
unset GST_PLUGIN_PATH GST_PLUGIN_SYSTEM_PATH
unset GAMEMODERUN GAMEMODEAUTO MANGOHUD MANGOHUD_CONFIG
unset ENABLE_VKBASALT OBS_VKCAPTURE

# Restore host identity: Wine spoofs USER/USERNAME (steamuser for Proton),
# which breaks D-Bus services that key off the user.
HOST_UID="$(id -u 2>/dev/null)"
HOST_USER="$(id -un 2>/dev/null)"
[ -n "$HOST_USER" ] && USER="$HOST_USER" && export USER
[ -n "$HOST_USER" ] && LOGNAME="$HOST_USER" && export LOGNAME

# Resolve the session bus socket, honouring an existing XDG_RUNTIME_DIR so
# callers can point us at a controlled location (used by the test suite).
if [ -n "$XDG_RUNTIME_DIR" ]; then
    BUS_SOCKET="$XDG_RUNTIME_DIR/bus"
else
    BUS_SOCKET="/run/user/$HOST_UID/bus"
    if [ -d "/run/user/$HOST_UID" ]; then
        XDG_RUNTIME_DIR="/run/user/$HOST_UID"
        export XDG_RUNTIME_DIR
    fi
fi
if [ ! -S "$BUS_SOCKET" ] && [ -S "${DBUS_SESSION_BUS_ADDRESS#unix:path=}" ]; then
    BUS_SOCKET="${DBUS_SESSION_BUS_ADDRESS#unix:path=}"
fi
if [ -S "$BUS_SOCKET" ] && [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    DBUS_SESSION_BUS_ADDRESS="unix:path=$BUS_SOCKET"
    export DBUS_SESSION_BUS_ADDRESS
fi

# -------------------------------------------------------------- arguments
# Rewrite DOS-style paths (C:\..., Z:\...) to Unix paths through the
# prefix's dosdevices mappings, falling back to winepath, then shell-quote
# everything for the strategies below.
rewrite_dos_path() {
    case "$1" in
        [A-Za-z]:[/\\]*) ;;
        *)
            printf '%s' "$1"
            return
            ;;
    esac
    if [ -n "$ORIG_WINEPREFIX" ]; then
        # POSIX-only path surgery: no cut/tr/sed — this runs before the
        # clean PATH is built and stripped sandboxes may not have coreutils.
        ch="${1%"${1#?}"}"
        case "$ch" in
            A) d=a ;; B) d=b ;; C) d=c ;; D) d=d ;; E) d=e ;; F) d=f ;;
            G) d=g ;; H) d=h ;; I) d=i ;; J) d=j ;; K) d=k ;; L) d=l ;;
            M) d=m ;; N) d=n ;; O) d=o ;; P) d=p ;; Q) d=q ;; R) d=r ;;
            S) d=s ;; T) d=t ;; U) d=u ;; V) d=v ;; W) d=w ;; X) d=x ;;
            Y) d=y ;; Z) d=z ;; *) d="$ch" ;;
        esac
        rest="${1#??}"
        while :; do
            case "$rest" in
                *\\*) rest="${rest%%\\*}/${rest#*\\}" ;;
                *) break ;;
            esac
        done
        if [ -d "$ORIG_WINEPREFIX/dosdevices/$d:" ]; then
            # resolve the dosdevices symlink without relying on readlink -f
            # (not present everywhere; coreutils may not be reachable yet)
            target="$(cd -P "$ORIG_WINEPREFIX/dosdevices/$d:" 2>/dev/null && pwd -P)"
            if [ -n "$target" ]; then
                printf '%s%s' "$target" "$rest"
                return
            fi
        fi
    fi
    if command -v winepath >/dev/null 2>&1; then
        u_arg="$(winepath -u "$1" 2>/dev/null)"
        if [ -n "$u_arg" ]; then
            printf '%s' "$u_arg"
            return
        fi
    fi
    printf '%s' "$1"
}

# Single-quote an argument for later eval-based strategies, escaping any
# embedded quote as '\'' — pure POSIX, no external tools.
quote_arg() {
    rest="$1"
    q=""
    while :; do
        case "$rest" in
            *\'*)
                q="$q${rest%%\'*}'\\''"
                rest="${rest#*\'}"
                ;;
            *)
                q="$q$rest"
                break
                ;;
        esac
    done
    printf "'%s'" "$q"
}

QUOTED_ARGS=""
for arg in "$@"; do
    arg="$(rewrite_dos_path "$arg")"
    if [ -z "$QUOTED_ARGS" ]; then
        QUOTED_ARGS="$(quote_arg "$arg")"
    else
        QUOTED_ARGS="$QUOTED_ARGS $(quote_arg "$arg")"
    fi
done

TARGET_URI=""
for arg in "$@"; do
    case "$arg" in
        [A-Za-z][A-Za-z0-9+.-]*://*|mailto:*|tel:*|magnet:*)
            TARGET_URI="$arg"
            break
            ;;
    esac
done

# ------------------------------------------------------------ host lookup
SELF_DIR="$(CDPATH= cd -- "$SELF_ARG_DIR" && pwd)"
find_host_xdg_open() {
    # PATH first, so user/system overrides win; skip every entry that would
    # resolve back to this very wrapper (helpers dir, runner bin aliases).
    IFS=':'
    for p in $PATH; do
        [ -n "$p" ] || continue
        [ -x "$p/xdg-open" ] || continue
        [ "$p/xdg-open" -ef "$0" ] && continue
        printf '%s' "$p/xdg-open"
        return
    done
    unset IFS
    # Absolute fallbacks for a wiped or hostile PATH.
    for p in /usr/bin/xdg-open /usr/local/bin/xdg-open /bin/xdg-open; do
        if [ -x "$p" ] && ! [ "$p" -ef "$0" ]; then
            printf '%s' "$p"
            return
        fi
    done
}

CLEAN_PATH=""
IFS=':'
for base in $PATH /usr/local/bin /usr/bin /bin /usr/local/sbin /usr/sbin /sbin; do
    [ "$base" = "$SELF_DIR" ] && continue
    [ -d "$base" ] || continue
    case ":$CLEAN_PATH:" in
        *":$base:"*) continue ;;
    esac
    if [ -z "$CLEAN_PATH" ]; then
        CLEAN_PATH="$base"
    else
        CLEAN_PATH="$CLEAN_PATH:$base"
    fi
done
unset IFS
export PATH="$CLEAN_PATH"

HOST_XDG_OPEN="$(find_host_xdg_open)"

# Openers are routed to xdg-open; anything else (brave, firefox, ...) is a
# browser invoked by name: prefer the real host binary, and fall back to
# xdg-open (MIME default) when that browser is not installed on the host.
TARGET_CMD=""
case "$CALLER" in
    xdg-open|gio|gnome-open|kde-open|kde-open5|kfmclient|winebrowser)
        TARGET_CMD="$HOST_XDG_OPEN"
        ;;
    *)
        IFS=':'
        for p in $CLEAN_PATH; do
            # -ef: skip aliases that point back at this wrapper (the helpers
            # dir is on PATH and symlinks every browser name to it)
            if [ -x "$p/$CALLER" ] && ! [ "$p/$CALLER" -ef "$0" ]; then
                TARGET_CMD="$p/$CALLER"
                break
            fi
        done
        unset IFS
        [ -z "$TARGET_CMD" ] && TARGET_CMD="$HOST_XDG_OPEN"
        ;;
esac

# --------------------------------------------------------------- strategy 1
# FreeDesktop OpenURI portal: fully decouples the browser from this process
# tree and environment. Requires a reachable session bus and gdbus/busctl.
try_portal() {
    [ -n "$TARGET_URI" ] || return 1
    if [ ! -S "$BUS_SOCKET" ] && [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
        return 1
    fi
    if command -v gdbus >/dev/null 2>&1; then
        gdbus call --session \
            --dest org.freedesktop.portal.Desktop \
            --object-path /org/freedesktop/portal/desktop \
            --method org.freedesktop.portal.OpenURI.OpenURI \
            --timeout 5 \
            "" "$TARGET_URI" "{}" >/dev/null 2>&1 || return 1
    elif command -v busctl >/dev/null 2>&1; then
        busctl --user call \
            org.freedesktop.portal.Desktop \
            /org/freedesktop/portal/desktop \
            org.freedesktop.portal.OpenURI \
            OpenURI ssa\\{sv\\} "" "$TARGET_URI" 0 >/dev/null 2>&1 || return 1
    else
        return 1
    fi
    log_handoff portal success ""
    exit 0
}
try_portal || log_handoff portal skipped-or-failed "no-uri/bus/tool or denied"

# --------------------------------------------------------------- strategy 2
# Transient systemd user unit: fresh environment from the user manager and
# outside the Wine process group, so browser sandboxing (setuid sandbox,
# Crashpad) starts cleanly.
try_systemd_run() {
    [ -n "$TARGET_CMD" ] || return 1
    command -v systemd-run >/dev/null 2>&1 || return 1
    if [ ! -S "$BUS_SOCKET" ] && [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
        return 1
    fi
    SYSTEMD_ENV_ARGS="--setenv=PATH=$CLEAN_PATH"
    [ -n "$DISPLAY" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=DISPLAY=$DISPLAY"
    [ -n "$WAYLAND_DISPLAY" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
    [ -n "$XDG_CURRENT_DESKTOP" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP"
    [ -n "$DESKTOP_SESSION" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=DESKTOP_SESSION=$DESKTOP_SESSION"
    [ -n "$XAUTHORITY" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XAUTHORITY=$XAUTHORITY"
    [ -n "$XDG_SESSION_TYPE" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
    [ -n "$XDG_RUNTIME_DIR" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
    [ -n "$DBUS_SESSION_BUS_ADDRESS" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"
    [ -n "$XDG_DATA_DIRS" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XDG_DATA_DIRS=$XDG_DATA_DIRS"
    [ -n "$XDG_CONFIG_DIRS" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=XDG_CONFIG_DIRS=$XDG_CONFIG_DIRS"
    [ -n "$USER" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=USER=$USER"
    [ -n "$HOME" ] && SYSTEMD_ENV_ARGS="$SYSTEMD_ENV_ARGS --setenv=HOME=$HOME"
    eval "systemd-run --user --collect --slice=app.slice -q $SYSTEMD_ENV_ARGS \
        \"\$TARGET_CMD\" $QUOTED_ARGS" >/dev/null 2>&1 || return 1
    log_handoff systemd-run success ""
    exit 0
}
try_systemd_run || log_handoff systemd-run skipped-or-failed "no bus/tool or spawn failed"

# --------------------------------------------------------------- strategy 3
# Desktop session launchers (KDE kstart, gio open).
try_kstart() {
    [ -n "$TARGET_CMD" ] || return 1
    [ -S "$BUS_SOCKET" ] || return 1
    case "$XDG_CURRENT_DESKTOP" in
        [Kk][Dd][Ee]*) ;;
        *) return 1 ;;
    esac
    if command -v kstart6 >/dev/null 2>&1; then
        eval "kstart6 \"\$TARGET_CMD\" $QUOTED_ARGS" >/dev/null 2>&1 || return 1
    elif command -v kstart5 >/dev/null 2>&1; then
        eval "kstart5 \"\$TARGET_CMD\" $QUOTED_ARGS" >/dev/null 2>&1 || return 1
    else
        return 1
    fi
    log_handoff kstart success ""
    exit 0
}
try_kstart || true

# gio's handler resolution needs the session bus (gvfs/dconf); without it
# the call either fails or behaves unpredictably, so require the socket.
if [ -n "$TARGET_URI" ] && [ -S "$BUS_SOCKET" ] && command -v gio >/dev/null 2>&1; then
    if gio open "$TARGET_URI" >/dev/null 2>&1; then
        log_handoff gio success ""
        exit 0
    fi
fi

# --------------------------------------------------------------- strategy 4
# Sanitized detached execution. Works everywhere, including inside the
# bwrap sandbox; the browser talks to whatever sockets are reachable there.
if [ -n "$TARGET_CMD" ]; then
    if command -v setsid >/dev/null 2>&1; then
        eval "setsid \"\$TARGET_CMD\" $QUOTED_ARGS" </dev/null >/dev/null 2>&1 &
    else
        eval "\"\$TARGET_CMD\" $QUOTED_ARGS" </dev/null >/dev/null 2>&1 &
    fi
    log_handoff detached success ""
    exit 0
fi

log_handoff none failure "no host target available"
exit 1
"""

    @staticmethod
    def ensure_browser_helpers(runner_path: Optional[str] = None):
        """
        Deploy decoupled browser handoff and URL opener wrappers across helper
        directories and runner bin paths to ensure compatibility for Chrome,
        Brave, Chromium forks, and Firefox.

        The wrapper is written once into Paths.helpers and symlinked under the
        opener/browser names into every runner's bin directory, so Wine's
        winebrowser resolves it through PATH inside the prefix. ~/.local/bin
        is deliberately NOT touched: an earlier fork revision installed a copy
        there, which shadowed the user's real xdg-open system-wide; legacy
        copies left by that revision are removed on migration.
        """
        if not os.path.isdir(Paths.helpers):
            os.makedirs(Paths.helpers, exist_ok=True)

        xdg_open_wrapper = os.path.join(Paths.helpers, "xdg-open")
        wrapper_content = ManagerUtils.build_browser_handoff_wrapper()
        helper_binaries = [
            "xdg-open",
            "gio",
            "gnome-open",
            "kde-open",
            "kde-open5",
            "kfmclient",
            "google-chrome",
            "google-chrome-stable",
            "google-chrome-beta",
            "google-chrome-unstable",
            "chromium",
            "chromium-browser",
            "brave",
            "brave-browser",
            "brave-bin",
            "firefox",
            "firefox-developer-edition",
            "firefox-nightly",
            "firefox-esr",
            "firefox-bin",
            "vivaldi",
            "vivaldi-stable",
            "helium",
            "helium-browser",
            "zen-browser",
            "zen",
            "microsoft-edge",
            "microsoft-edge-stable",
            "microsoft-edge-beta",
            "microsoft-edge-dev",
            "opera",
            "opera-beta",
            "opera-developer",
            "epiphany",
            "midori",
            "floorp",
            "thorium-browser",
            "thorium",
            "librewolf",
            "waterfox",
        ]

        try:
            with open(xdg_open_wrapper, "w", encoding="utf-8") as f:
                f.write(wrapper_content)
            os.chmod(xdg_open_wrapper, 0o755)

            # Link all binary aliases in helpers directory
            for bin_name in helper_binaries:
                if bin_name != "xdg-open":
                    h_path = os.path.join(Paths.helpers, bin_name)
                    try:
                        if os.path.islink(h_path) or os.path.isfile(h_path):
                            os.remove(h_path)
                        os.symlink(xdg_open_wrapper, h_path)
                    except OSError:
                        pass

            # Remove the legacy ~/.local/bin/xdg-open copy installed by older
            # revisions, but only when it is our generated wrapper: never touch
            # anything the user (or their distro) put there themselves.
            legacy_user_wrapper = os.path.expanduser("~/.local/bin/xdg-open")
            if os.path.isfile(legacy_user_wrapper) or os.path.islink(
                legacy_user_wrapper
            ):
                try:
                    with open(legacy_user_wrapper, encoding="utf-8") as f:
                        head = f.read(64)
                    if head.startswith("#!/usr/bin/env sh") and (
                        "Bottles" in head
                        or "Wine" in head
                        or "winebrowser" in head
                        or "Clean Wine" in head
                        or "handoff" in head
                    ):
                        os.remove(legacy_user_wrapper)
                        logging.info(
                            "Removed legacy Bottles browser handoff wrapper from "
                            "~/.local/bin/xdg-open."
                        )
                except OSError:
                    pass

            target_dirs = []
            if runner_path and os.path.isdir(runner_path):
                for sub in ["bin", "files/bin"]:
                    d = os.path.join(runner_path, sub)
                    if os.path.isdir(d):
                        target_dirs.append(d)

            if os.path.isdir(Paths.runners):
                for runner_dir in glob(f"{Paths.runners}/*/"):
                    for sub in ["bin", "files/bin"]:
                        d = os.path.join(runner_dir, sub)
                        if os.path.isdir(d) and d not in target_dirs:
                            target_dirs.append(d)

            for target_dir in target_dirs:
                for bin_name in helper_binaries:
                    r_bin = os.path.join(target_dir, bin_name)
                    try:
                        if os.path.islink(r_bin) or os.path.isfile(r_bin):
                            os.remove(r_bin)
                        os.symlink(xdg_open_wrapper, r_bin)
                    except OSError:
                        pass
        except OSError:
            pass

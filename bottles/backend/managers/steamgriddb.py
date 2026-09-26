# steamgriddb.py
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
import uuid
from pathlib import Path
from typing import Optional

import requests

from bottles.backend.globals import Paths
from bottles.backend.logger import Logger
from bottles.backend.models.config import BottleConfig
from bottles.backend.utils.generic import get_mime
from bottles.backend.utils.manager import ManagerUtils

logging = Logger()


class SteamGridDBManager:
    @staticmethod
    def get_game_grid(name: str, config: Optional[BottleConfig] = None):
        try:
            res = requests.get(
                f"https://steamgrid.usebottles.com/api/search/{name}", timeout=10
            )
        except Exception:
            return

        if res.status_code != 200:
            logging.warning(
                f"SteamGridDB search for '{name}' failed with HTTP {res.status_code}."
            )
            return

        try:
            url = res.json()
        except ValueError:
            logging.warning(f"SteamGridDB search for '{name}' returned invalid JSON.")
            return

        return SteamGridDBManager.__save_grid(url, config)

    @staticmethod
    def __save_grid(url: str, config: Optional[BottleConfig] = None):
        if config is None:
            grids_path = os.fspath(Path(Paths.base) / "umu" / "covers")
            uri_prefix = "umu-grid:"
        else:
            grids_path = os.path.join(ManagerUtils.get_bottle_path(config), "grids")
            uri_prefix = "grid:"
        if not os.path.exists(grids_path):
            os.makedirs(grids_path)

        ext = url.split(".")[-1]
        filename = str(uuid.uuid4()) + "." + ext
        path = os.path.join(grids_path, filename)

        # Persist only after validating the response: a blocked or broken
        # CDN can answer with an HTML error page, and a garbage grid file
        # would be cached and rendered by the library forever.
        tmp_path = f"{path}.part"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                logging.warning(f"Grid download failed with HTTP {r.status_code}.")
                return
            with open(tmp_path, "wb") as f:
                f.write(r.content)
            mime = get_mime(tmp_path)
            if not mime or not mime.startswith("image/"):
                logging.warning(f"Grid download is not an image ({mime}).")
                os.remove(tmp_path)
                return
            os.replace(tmp_path, path)
        except Exception as e:
            logging.error(f"Failed to save grid from {url}: {e}")
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
            return

        return f"{uri_prefix}{filename}"

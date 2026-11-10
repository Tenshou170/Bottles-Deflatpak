import os
import subprocess
from functools import lru_cache


class DisplayUtils:
    @staticmethod
    @lru_cache
    def get_x_display():
        """Get the X display port."""
        if os.environ.get("DISPLAY"):
            return os.environ.get("DISPLAY")

        for i in range(3):
            _port = f":{i}"
            try:
                result = subprocess.run(
                    ["xdpyinfo", "-display", _port],
                    capture_output=True,
                    timeout=1,
                    check=False,
                )
            except FileNotFoundError:
                return False
            except (OSError, subprocess.TimeoutExpired):
                continue
            if b"x.org" in result.stdout.lower():
                return _port

        return False

    @staticmethod
    def check_nvidia_device():
        """Check if there is an nvidia device connected"""
        _query = "NVIDIA Corporation".lower()
        try:
            # Check lspci output for VGA controllers with NVIDIA vendor
            output = subprocess.check_output(["lspci"], text=True).lower()
            for line in output.splitlines():
                if "vga" in line and _query in line:
                    return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        return False

    @staticmethod
    def display_server_type():
        """Return the display server type"""
        session_type = os.environ.get("XDG_SESSION_TYPE")
        if session_type:
            return session_type.lower()
        return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"

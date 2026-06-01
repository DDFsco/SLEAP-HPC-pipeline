"""OpenSSH SSH_ASKPASS helper for the SLEAP Pipeline GUI on Windows."""
from __future__ import annotations

import json
import os
import sys
import time
import tempfile
from pathlib import Path

AUTH_CACHE_TTL_SECONDS = 8 * 60 * 60


def _load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        age = time.time() - path.stat().st_mtime
        if age > AUTH_CACHE_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, cache: dict[str, str]) -> None:
    path.write_text(json.dumps(cache), encoding="utf-8")


def _prompt_kind(step: int, password_retry: bool = False) -> tuple[str, bool, str]:
    prompt_env = os.environ.get("SSH_ASKPASS_PROMPT", "").strip()
    if prompt_env:
        lower = prompt_env.lower()
        if "yes/no" in lower or "continue connecting" in lower:
            return ("SSH host key confirmation. Type yes to trust this host.", False, "hostkey")
        if "passcode" in lower or "verification code" in lower or "duo" in lower:
            return (
                "Great Lakes Duo verification\n\n"
                f"{prompt_env}\n\n"
                "Enter the requested Duo code or option number. If Great Lakes offers a push option, "
                "enter that option number and approve the notification.",
                False,
                "duo",
            )
        if "password" in lower:
            if password_retry:
                return (
                    "Great Lakes rejected the cached password.\n\n"
                    "Enter your current Great Lakes password to try again.",
                    True,
                    "password",
                )
            return (
                "Great Lakes password requested\n\n"
                "Enter your Great Lakes password. The SLEAP pipeline caches it during this GUI session "
                "so repeated SSH/SFTP commands do not ask again.",
                True,
                "password",
            )
        return (
            "Great Lakes authentication\n\n"
            f"{prompt_env}\n\n"
            "Enter the response requested by Great Lakes.",
            True,
            "password",
        )

    if step == 1:
        if password_retry:
            return (
                "Great Lakes rejected the cached password.\n\n"
                "Enter your current Great Lakes password to try again.",
                True,
                "password",
            )
        return (
            "Enter your Great Lakes password.\n\n"
            "The SLEAP pipeline caches it during this GUI session so Windows does not ask "
            "again for each SSH/SFTP command.",
            True,
            "password",
        )
    return (
        "Great Lakes Duo verification\n\n"
        "Enter your Duo passcode or the option number requested by Great Lakes. "
        "Duo prompts cannot be reused like your password.",
        False,
        "duo",
    )


def main() -> int:
    connection = os.environ.get("SLEAP_ASKPASS_CONNECTION", "")
    count_file = Path(tempfile.gettempdir()) / f"sleap_askpass_{connection}.count" if connection else None
    used_password_file = Path(tempfile.gettempdir()) / f"sleap_askpass_{connection}.password_used" if connection else None
    step = 1
    if count_file and count_file.exists():
        step = int(count_file.read_text(encoding="utf-8").strip() or "0") + 1
        count_file.write_text(str(step), encoding="utf-8")

    cache_path = Path(os.environ.get("SLEAP_AUTH_CACHE", ""))
    cache = _load_cache(cache_path) if cache_path else {}
    password_retry = bool(used_password_file and used_password_file.exists())
    message, secret, kind = _prompt_kind(step, password_retry=password_retry)

    if kind == "hostkey":
        value = cache.get("hostkey") or "yes"
        if cache_path:
            cache["hostkey"] = value
            _save_cache(cache_path, cache)
        sys.stdout.write(value)
        return 0

    if kind == "password" and cache.get("password") and not password_retry:
        if used_password_file:
            used_password_file.write_text("1", encoding="utf-8")
        sys.stdout.write(cache["password"])
        return 0

    if kind == "password" and password_retry and cache_path:
        cache.pop("password", None)
        _save_cache(cache_path, cache)

    try:
        import tkinter as tk
        from tkinter import simpledialog
    except ImportError:
        return 1

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    initial = "yes" if kind == "hostkey" else ""
    value = simpledialog.askstring(
        "Great Lakes Authentication",
        message,
        initialvalue=initial or None,
        show="*" if secret else None,
        parent=root,
    )
    root.destroy()
    if value is None:
        return 1

    if cache_path and kind == "password":
        cache["password"] = value
        _save_cache(cache_path, cache)

    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

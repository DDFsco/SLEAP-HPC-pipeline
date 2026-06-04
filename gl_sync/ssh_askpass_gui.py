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


def _terminal_prompt() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return os.environ.get("SSH_ASKPASS_PROMPT", "").strip()


def _with_terminal_request(heading: str, prompt: str, instructions: str) -> str:
    parts = [heading]
    if prompt:
        parts.extend(["Terminal request:", prompt])
    if instructions:
        parts.append(instructions)
    return "\n\n".join(parts)


def _prompt_kind(step: int, password_retry: bool = False) -> tuple[str, bool, str]:
    prompt = _terminal_prompt()
    if prompt:
        lower = prompt.lower()
        if "yes/no" in lower or "continue connecting" in lower:
            return (
                _with_terminal_request(
                    "SSH host key confirmation",
                    prompt,
                    "Type yes to trust this Great Lakes host.",
                ),
                False,
                "hostkey",
            )
        if "passcode" in lower or "verification code" in lower or "duo" in lower:
            return (
                _with_terminal_request(
                    "Great Lakes Duo verification",
                    prompt,
                    "Enter the requested Duo code or option number. If Great Lakes offers a push option, "
                    "enter that option number and approve the notification.",
                ),
                False,
                "duo",
            )
        if "password" in lower:
            if password_retry:
                return (
                    _with_terminal_request(
                        "Great Lakes rejected the cached password",
                        prompt,
                        "Enter your current Great Lakes password to try again.",
                    ),
                    True,
                    "password",
                )
            return (
                _with_terminal_request(
                    "Great Lakes password requested",
                    prompt,
                    "Enter your Great Lakes password. The SLEAP pipeline caches it during this GUI session "
                    "so repeated SSH/SFTP commands do not ask again.",
                ),
                True,
                "password",
            )
        return (
            _with_terminal_request(
                "Great Lakes authentication",
                prompt,
                "Enter the response requested by Great Lakes.",
            ),
            False,
            "response",
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
            _with_terminal_request(
                "Great Lakes password requested",
                "",
                "Enter your Great Lakes password. The SLEAP pipeline caches it during this GUI session "
                "so Windows does not ask again for each SSH/SFTP command.",
            ),
            True,
            "password",
        )
    return (
        _with_terminal_request(
            "Great Lakes Duo verification",
            "",
            "Enter your Duo passcode or the option number requested by Great Lakes. "
            "Duo prompts cannot be reused like your password.",
        ),
        False,
        "duo",
    )


def _ask_response(root, title: str, message: str, secret: bool, initial: str = "") -> str | None:
    import tkinter as tk
    from tkinter import ttk

    result: dict[str, str | None] = {"value": None}
    window = tk.Toplevel(root)
    window.title(title)
    window.attributes("-topmost", True)
    window.resizable(True, True)
    window.minsize(560, 360)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    ttk.Label(
        window,
        text="Great Lakes is requesting authentication input.",
        font=("Arial", 11, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))

    feedback_frame = ttk.Frame(window)
    feedback_frame.grid(row=1, column=0, sticky="nsew", padx=16)
    feedback_frame.columnconfigure(0, weight=1)
    feedback_frame.rowconfigure(0, weight=1)
    feedback = tk.Text(feedback_frame, wrap="word", height=12, padx=8, pady=8)
    feedback.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(feedback_frame, orient="vertical", command=feedback.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    feedback.configure(yscrollcommand=scrollbar.set)
    feedback.insert("1.0", message)
    feedback.configure(state="disabled")

    ttk.Label(window, text="Response").grid(row=2, column=0, sticky="w", padx=16, pady=(12, 4))
    response_var = tk.StringVar(value=initial)
    entry = ttk.Entry(window, textvariable=response_var, show="*" if secret else "")
    entry.grid(row=3, column=0, sticky="ew", padx=16)

    buttons = ttk.Frame(window)
    buttons.grid(row=4, column=0, sticky="e", padx=16, pady=16)

    def submit() -> None:
        result["value"] = response_var.get()
        window.destroy()

    def cancel() -> None:
        window.destroy()

    ttk.Button(buttons, text="Cancel", command=cancel).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Submit", command=submit).grid(row=0, column=1)
    window.protocol("WM_DELETE_WINDOW", cancel)
    window.bind("<Return>", lambda _event: submit())
    window.bind("<Escape>", lambda _event: cancel())
    entry.focus_set()
    window.grab_set()
    root.wait_window(window)
    return result["value"]


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
    except ImportError:
        return 1

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    initial = "yes" if kind == "hostkey" else ""
    title = {
        "password": "Great Lakes Password",
        "duo": "Great Lakes Duo Verification",
        "hostkey": "SSH Host Key Confirmation",
    }.get(kind, "Great Lakes Authentication")
    value = _ask_response(root, title, message, secret, initial)
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

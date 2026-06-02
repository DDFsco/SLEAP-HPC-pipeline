from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from io import BytesIO
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


GL_HOST_DEFAULT = "greatlakes.arc-ts.umich.edu"
CONFIG_PATH = Path.home() / ".sleap_pipeline.json"
LOG_NAME = "pipeline.log.json"
GL_SYNC_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "tasks"}
GL_SYNC_SKIP_SUFFIXES = {".pyc", ".pyo"}
INFERENCE_CONFIG_SUFFIXES = {".conf", ".env", ".sh"}
InputCallback = Callable[[str, bool, str | None], str | None]
_windows_auth_config: ContextVar["PipelineConfig | None"] = ContextVar("windows_auth_config", default=None)
AUTH_CACHE_TTL_SECONDS = 8 * 60 * 60


def local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


@dataclass
class PipelineConfig:
    gl_user: str = ""
    slurm_account: str = ""
    gl_host: str = GL_HOST_DEFAULT
    gl_scratch_dir: str = ""
    local_project: str = str(Path.home() / "sleap_project")
    sleap_label_cmd: str = ""
    default_preset: str = "default"
    gl_sync_remote: str = "~/gl_sync"

    @property
    def ssh_target(self) -> str:
        if not self.gl_user:
            raise ValueError("GL user is not configured.")
        return f"{self.gl_user}@{self.gl_host}"

    @property
    def local_project_path(self) -> Path:
        return Path(self.local_project).expanduser()


def load_config(path: Path = CONFIG_PATH) -> PipelineConfig:
    if not path.exists():
        return PipelineConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return PipelineConfig(**{**asdict(PipelineConfig()), **data})


def save_config(config: PipelineConfig, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")


def default_gl_scratch_dir(gl_user: str) -> str:
    user = safe_task_name(gl_user)
    return f"/scratch/gid_root/gid0/{user}/sleap_rat"


def ensure_config_defaults(config: PipelineConfig) -> list[str]:
    updates: list[str] = []
    if not config.gl_scratch_dir and config.gl_user:
        config.gl_scratch_dir = default_gl_scratch_dir(config.gl_user)
        updates.append(f"GL scratch dir defaulted to {config.gl_scratch_dir}")
    return updates


def log_path(config: PipelineConfig) -> Path:
    return config.local_project_path / LOG_NAME


def load_pipeline_log(config: PipelineConfig) -> dict:
    path = log_path(config)
    if not path.exists():
        return {"jobs": [], "downloaded_models": [], "downloaded_predictions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_pipeline_log(config: PipelineConfig, data: dict) -> None:
    path = log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def append_job(config: PipelineConfig, job: dict) -> None:
    data = load_pipeline_log(config)
    data.setdefault("jobs", []).append({"submitted_at": local_now(), "status": "submitted", **job})
    save_pipeline_log(config, data)


def mark_download(config: PipelineConfig, kind: str, record: dict) -> None:
    key = {"model": "downloaded_models", "prediction": "downloaded_predictions"}[kind]
    data = load_pipeline_log(config)
    data.setdefault(key, []).append({"downloaded_at": local_now(), **record})
    save_pipeline_log(config, data)


def task_root(config: PipelineConfig, task: str) -> Path:
    return config.local_project_path / "tasks" / safe_task_name(task)


def ensure_task(config: PipelineConfig, task: str) -> Path:
    root = task_root(config, task)
    for name in ["labels", "training_package", "models", "videos", "exports"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def safe_task_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.strip())
    if not cleaned:
        raise ValueError("Task name is empty.")
    return cleaned


def list_tasks(config: PipelineConfig) -> list[str]:
    tasks_dir = config.local_project_path / "tasks"
    if not tasks_dir.exists():
        return []
    return sorted(path.name for path in tasks_dir.iterdir() if path.is_dir())


def list_training_zips(config: PipelineConfig, task: str | None = None) -> list[Path]:
    roots = [task_root(config, task)] if task else [task_root(config, t) for t in list_tasks(config)]
    zips: list[Path] = []
    for root in roots:
        if root.exists():
            zips.extend(sorted(root.rglob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True))
    return zips


def list_model_refs(config: PipelineConfig, task: str | None = None) -> list[dict]:
    refs: dict[tuple[str, str], dict] = {}
    try:
        log = load_pipeline_log(config)
    except Exception:
        log = {"jobs": [], "downloaded_models": []}

    for job in log.get("jobs", []):
        if job.get("type") != "train" or not job.get("task") or not job.get("run_name"):
            continue
        if task and safe_task_name(job["task"]) != safe_task_name(task):
            continue
        key = (safe_task_name(job["task"]), safe_task_name(job["run_name"]))
        refs[key] = {
            "task": key[0],
            "model": key[1],
            "source": "training history",
            "time": job.get("submitted_at", ""),
            "job_id": job.get("job_id", ""),
        }

    for record in log.get("downloaded_models", []):
        if not record.get("task") or not record.get("run_name"):
            continue
        if task and safe_task_name(record["task"]) != safe_task_name(task):
            continue
        key = (safe_task_name(record["task"]), safe_task_name(record["run_name"]))
        refs[key] = {
            **refs.get(key, {}),
            "task": key[0],
            "model": key[1],
            "source": "downloaded model",
            "time": record.get("downloaded_at", refs.get(key, {}).get("time", "")),
            "path": record.get("path", ""),
            "job_id": refs.get(key, {}).get("job_id", ""),
        }

    for task_name in list_tasks(config):
        if task and safe_task_name(task_name) != safe_task_name(task):
            continue
        models_dir = task_root(config, task_name) / "models"
        if not models_dir.exists():
            continue
        for model_dir in sorted((path for path in models_dir.iterdir() if path.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
            key = (safe_task_name(task_name), safe_task_name(model_dir.name))
            refs[key] = {
                **refs.get(key, {}),
                "task": key[0],
                "model": key[1],
                "source": "local model folder",
                "time": refs.get(key, {}).get("time", local_from_timestamp(model_dir.stat().st_mtime)),
                "path": str(model_dir),
                "job_id": refs.get(key, {}).get("job_id", ""),
            }

    return sorted(refs.values(), key=lambda ref: ref.get("time", ""), reverse=True)


def list_prediction_refs(config: PipelineConfig, task: str | None = None) -> list[dict]:
    refs: dict[tuple[str, str], dict] = {}
    try:
        log = load_pipeline_log(config)
    except Exception:
        log = {"jobs": [], "downloaded_predictions": []}

    for job in log.get("jobs", []):
        if job.get("type") != "predict" or not job.get("task") or not job.get("expected_output"):
            continue
        if task and safe_task_name(job["task"]) != safe_task_name(task):
            continue
        task_name = safe_task_name(job["task"])
        file_name = Path(job["expected_output"]).name
        key = (task_name, file_name)
        refs[key] = {
            "task": task_name,
            "file": file_name,
            "remote_rel": job.get("expected_output", f"exports/{file_name}"),
            "source": "prediction history",
            "time": job.get("submitted_at", ""),
            "job_id": job.get("job_id", ""),
            "model": job.get("model", ""),
            "video": job.get("video", ""),
        }

    for record in log.get("downloaded_predictions", []):
        if not record.get("task") or not record.get("file"):
            continue
        if task and safe_task_name(record["task"]) != safe_task_name(task):
            continue
        task_name = safe_task_name(record["task"])
        file_name = Path(record["file"]).name
        key = (task_name, file_name)
        refs[key] = {
            **refs.get(key, {}),
            "task": task_name,
            "file": file_name,
            "remote_rel": refs.get(key, {}).get("remote_rel", f"exports/{file_name}"),
            "source": "downloaded prediction",
            "time": record.get("downloaded_at", refs.get(key, {}).get("time", "")),
            "path": record.get("path", ""),
            "job_id": refs.get(key, {}).get("job_id", ""),
            "model": refs.get(key, {}).get("model", ""),
            "video": refs.get(key, {}).get("video", ""),
        }

    for task_name in list_tasks(config):
        if task and safe_task_name(task_name) != safe_task_name(task):
            continue
        task_dir = task_root(config, task_name)
        if not task_dir.exists():
            continue
        for export_file in sorted(task_dir.rglob("*.slp"), key=lambda p: p.stat().st_mtime, reverse=True):
            clean_task = safe_task_name(task_name)
            try:
                remote_rel = export_file.relative_to(task_dir).as_posix()
            except ValueError:
                remote_rel = f"exports/{export_file.name}"
            key = (clean_task, remote_rel)
            refs[key] = {
                **refs.get(key, {}),
                "task": clean_task,
                "file": export_file.name,
                "remote_rel": refs.get(key, {}).get("remote_rel", remote_rel),
                "source": "local export file",
                "time": refs.get(key, {}).get("time", local_from_timestamp(export_file.stat().st_mtime)),
                "path": str(export_file),
                "job_id": refs.get(key, {}).get("job_id", ""),
                "model": refs.get(key, {}).get("model", ""),
                "video": refs.get(key, {}).get("video", ""),
            }

    return sorted(refs.values(), key=lambda ref: ref.get("time", ""), reverse=True)


def list_inference_configs(base_dir: Path | None = None) -> list[str]:
    inference_dir = (base_dir or Path(__file__).resolve().parent) / "inference"
    if not inference_dir.exists():
        return ["default"]
    names: list[str] = []
    for path in sorted(inference_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix not in INFERENCE_CONFIG_SUFFIXES:
            continue
        names.append(safe_task_name(path.stem))
    unique_names = sorted(set(names))
    if "default" in unique_names:
        unique_names.remove("default")
        unique_names.insert(0, "default")
    return unique_names or ["default"]


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def safe_control_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def ssh_control_path(config: PipelineConfig) -> str:
    user = safe_control_name(config.gl_user or "user")
    host = safe_control_name(config.gl_host or "host")
    if sys.platform == "win32":
        return str(Path(tempfile.gettempdir()) / f"sleap-gl-{user}-{host}-%p")
    return f"/tmp/sleap-gl-{user}-{host}-%p"


def ssh_multiplex_options(config: PipelineConfig) -> list[str]:
    # Windows OpenSSH accepts these options in `ssh -G`, but the actual
    # connection fails with "getsockname failed: Not a socket".
    if sys.platform == "win32":
        return []
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=15m",
        "-o",
        f"ControlPath={ssh_control_path(config)}",
    ]


def ssh_master_is_running(config: PipelineConfig) -> bool:
    if sys.platform == "win32":
        return False
    args = [
        "ssh",
        *ssh_multiplex_options(config),
        "-o",
        "BatchMode=yes",
        "-O",
        "check",
        config.ssh_target,
    ]
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def run_streaming(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    close_stdin: bool = False,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")
    if input_text is not None:
        stdin = subprocess.PIPE
    elif close_stdin:
        stdin = subprocess.DEVNULL
    else:
        stdin = None
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if input_text is not None and proc.stdin is not None:
        proc.stdin.write(input_text)
        proc.stdin.close()
    output: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output.append(line)
        emit(line.rstrip())
    code = proc.wait()
    completed = subprocess.CompletedProcess(args, code, "".join(output), "")
    if check and code:
        raise subprocess.CalledProcessError(code, args, completed.stdout, completed.stderr)
    return completed


def run_streaming_binary(
    args: list[str],
    input_bytes: bytes,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")
    proc = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output_bytes, _ = proc.communicate(input_bytes)
    output = output_bytes.decode(errors="replace")
    for line in output.splitlines():
        emit(line.rstrip())
    completed = subprocess.CompletedProcess(args, proc.returncode, output, "")
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, args, output, "")
    return completed


def run_streaming_file(
    args: list[str],
    input_path: Path,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")
    proc = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output_chunks: list[bytes] = []

    def read_output() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            output_chunks.append(chunk)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    assert proc.stdin is not None
    with input_path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            proc.stdin.write(chunk)
    proc.stdin.close()
    code = proc.wait()
    reader.join(timeout=5)
    output = b"".join(output_chunks).decode(errors="replace")
    for line in output.splitlines():
        emit(line.rstrip())
    completed = subprocess.CompletedProcess(args, code, output, "")
    if check and code:
        raise subprocess.CalledProcessError(code, args, output, "")
    return completed


def run_capture_to_file(
    args: list[str],
    output_path: Path,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")
    proc = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    with output_path.open("wb") as output_file:
        shutil.copyfileobj(proc.stdout, output_file)
    code = proc.wait()
    completed = subprocess.CompletedProcess(args, code, "", "")
    if check and code:
        preview = output_path.read_bytes()[:4000].decode(errors="replace")
        raise subprocess.CalledProcessError(code, args, preview, "")
    return completed


def _ensure_windows_askpass_wrapper() -> str:
    script_dir = Path(__file__).resolve().parent
    askpass_py = script_dir / "ssh_askpass_gui.py"
    wrapper = Path(tempfile.gettempdir()) / "sleap_ssh_askpass.cmd"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw) if pythonw.is_file() else sys.executable
    content = f'@echo off\r\n"{launcher}" "{askpass_py}"\r\n'
    if not wrapper.exists() or wrapper.read_text(encoding="utf-8") != content:
        wrapper.write_text(content, encoding="utf-8")
    return str(wrapper)


def _auth_cache_path(config: PipelineConfig) -> Path:
    user = safe_control_name(config.gl_user or "user")
    host = safe_control_name(config.gl_host or "host")
    return Path(tempfile.gettempdir()) / f"sleap_auth_cache_{user}_{host}.json"


def seed_windows_auth_cache(config: PipelineConfig, password: str) -> None:
    """Preload the Windows SSH_ASKPASS cache so password is requested once."""
    cache_path = _auth_cache_path(config)
    cache = {}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = loaded
        except (OSError, json.JSONDecodeError):
            cache = {}
    cache["password"] = password
    cache_path.write_text(json.dumps(cache), encoding="utf-8")


def windows_auth_cache_has_password(config: PipelineConfig) -> bool:
    cache_path = _auth_cache_path(config)
    if not cache_path.exists():
        return False
    try:
        if time.time() - cache_path.stat().st_mtime > AUTH_CACHE_TTL_SECONDS:
            return False
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(isinstance(data, dict) and data.get("password"))


@contextmanager
def windows_auth_session(config: PipelineConfig):
    """Reuse cached Great Lakes password across SSH/SFTP calls on Windows."""
    token = _windows_auth_config.set(config)
    try:
        yield
    finally:
        _windows_auth_config.reset(token)


def _windows_askpass_env() -> dict[str, str]:
    connection_id = uuid.uuid4().hex
    count_file = Path(tempfile.gettempdir()) / f"sleap_askpass_{connection_id}.count"
    count_file.write_text("0", encoding="utf-8")
    env = {
        "SSH_ASKPASS": _ensure_windows_askpass_wrapper(),
        "SSH_ASKPASS_REQUIRE": "force",
        "DISPLAY": "dummy",
        "SLEAP_ASKPASS_CONNECTION": connection_id,
    }
    config = _windows_auth_config.get()
    if config is not None:
        env["SLEAP_AUTH_CACHE"] = str(_auth_cache_path(config))
    return env


def _run_with_windows_askpass(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    check: bool = True,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(_windows_askpass_env())
    return run_streaming(
        args,
        emit=emit,
        cwd=cwd,
        check=check,
        env=env,
        input_text=input_text,
        close_stdin=input_text is None,
    )


def _run_binary_with_windows_askpass(
    args: list[str],
    input_bytes: bytes,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(_windows_askpass_env())
    return run_streaming_binary(args, input_bytes, emit=emit, check=check, env=env)


def _run_file_with_windows_askpass(
    args: list[str],
    input_path: Path,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(_windows_askpass_env())
    return run_streaming_file(args, input_path, emit=emit, check=check, env=env)


def _capture_to_file_with_windows_askpass(
    args: list[str],
    output_path: Path,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(_windows_askpass_env())
    return run_capture_to_file(args, output_path, emit=emit, check=check, env=env)


def _copy_after_marker(source_path: Path, dest_path: Path, marker: bytes) -> bool:
    keep = b""
    found = False
    with source_path.open("rb") as source, dest_path.open("wb") as dest:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            data = keep + chunk
            if found:
                dest.write(data)
                keep = b""
                continue
            index = data.find(marker)
            if index >= 0:
                dest.write(data[index + len(marker):])
                keep = b""
                found = True
            else:
                keep = data[-len(marker):]
    return found


def prompt_kind(text: str) -> tuple[str, bool, str | None] | None:
    tail = text.lower()[-1200:]
    if "are you sure you want to continue connecting" in tail or "(yes/no" in tail:
        return ("SSH host key confirmation. Type yes to trust this host.", False, "yes")
    if "password:" in tail:
        return ("Great Lakes password", True, None)
    if "passcode" in tail:
        return ("Duo passcode", False, None)
    if "verification code" in tail:
        return ("Verification code", False, None)
    if "keyboard-interactive" in tail and tail.rstrip().endswith(":"):
        return ("Great Lakes authentication response", True, None)
    return None


def run_interactive(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
    check: bool = True,
    stdin_text: str | None = None,
    wait_for_prompt: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        if input_callback or stdin_text is not None:
            return _run_with_windows_askpass(
                args,
                emit=emit,
                check=check,
                input_text=stdin_text,
            )
        return run_streaming(args, emit=emit, check=check)
    return _run_interactive_unix(
        args,
        emit=emit,
        input_callback=input_callback,
        check=check,
        stdin_text=stdin_text,
        wait_for_prompt=wait_for_prompt,
    )


def _run_interactive_unix(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
    check: bool = True,
    stdin_text: str | None = None,
    wait_for_prompt: str | None = None,
) -> subprocess.CompletedProcess[str]:
    import pty
    import select
    import signal

    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")

    pid, master_fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(args[0], args)
        except Exception as exc:
            os.write(2, f"exec failed: {exc}\n".encode())
            os._exit(127)

    output: list[str] = []
    pending = ""
    sent_stdin = False
    exit_status: int | None = None

    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                text = data.decode(errors="replace")
                output.append(text)
                pending = (pending + text)[-2000:]
                for line in text.replace("\r", "").splitlines():
                    if line.strip():
                        emit(line.rstrip())

                if stdin_text is not None and not sent_stdin and wait_for_prompt and wait_for_prompt in pending:
                    os.write(master_fd, stdin_text.encode())
                    sent_stdin = True

                if input_callback:
                    prompt = prompt_kind(pending)
                    if prompt:
                        label, secret, default = prompt
                        response = input_callback(label + "\n\n" + pending.strip()[-500:], secret, default)
                        if response is None:
                            try:
                                os.kill(pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            raise RuntimeError("Authentication input cancelled.")
                        os.write(master_fd, (response + "\n").encode())
                        pending = ""

            child_pid, status = os.waitpid(pid, os.WNOHANG)
            if child_pid:
                exit_status = status
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0)
                    if not ready:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    text = data.decode(errors="replace")
                    output.append(text)
                    for line in text.replace("\r", "").splitlines():
                        if line.strip():
                            emit(line.rstrip())
                break

            if stdin_text is not None and not sent_stdin and not wait_for_prompt:
                # Used only for commands whose stdin is known to be safe immediately.
                os.write(master_fd, stdin_text.encode())
                sent_stdin = True
    finally:
        os.close(master_fd)

    if exit_status is None:
        _, exit_status = os.waitpid(pid, 0)
    if os.WIFEXITED(exit_status):
        code = os.WEXITSTATUS(exit_status)
    elif os.WIFSIGNALED(exit_status):
        code = 128 + os.WTERMSIG(exit_status)
    else:
        code = 1
    completed = subprocess.CompletedProcess(args, code, "".join(output), "")
    if check and code:
        raise subprocess.CalledProcessError(code, args, completed.stdout, completed.stderr)
    return completed


def ssh(
    config: PipelineConfig,
    remote_command: str,
    emit: Callable[[str], None] | None = None,
    check: bool = True,
    input_callback: InputCallback | None = None,
):
    args = ["ssh", *ssh_multiplex_options(config), config.ssh_target, remote_command]
    if input_callback:
        return run_interactive(args, emit=emit, check=check, input_callback=input_callback)
    return run_streaming(args, emit=emit, check=check)


def ssh_check(
    config: PipelineConfig,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> bool:
    try:
        ssh(config, "echo ok", emit=emit, input_callback=input_callback)
        return True
    except Exception as exc:
        if emit:
            emit(f"SSH check failed: {exc}")
        return False


def sftp_batch(
    config: PipelineConfig,
    commands: list[str],
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
):
    emit = emit or (lambda line: None)
    if input_callback and sys.platform == "win32":
        emit("$ sftp " + config.ssh_target)
        return _run_with_windows_askpass(
            ["sftp", *ssh_multiplex_options(config), config.ssh_target],
            emit=emit,
            input_text="\n".join(commands + ["bye"]) + "\n",
        )
    if input_callback:
        emit("$ sftp " + config.ssh_target)
        batch = "\n".join(commands + ["bye"]) + "\n"
        return run_interactive(
            ["sftp", *ssh_multiplex_options(config), config.ssh_target],
            emit=emit,
            input_callback=input_callback,
            stdin_text=batch,
            wait_for_prompt="sftp>",
        )
    emit("$ sftp -b - " + config.ssh_target)
    proc = subprocess.run(
        ["sftp", *ssh_multiplex_options(config), "-b", "-", config.ssh_target],
        input="\n".join(commands) + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        emit(line)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, "")
    return proc


def remote_home(
    config: PipelineConfig,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> str:
    start = "__SLEAP_HOME_START__"
    end = "__SLEAP_HOME_END__"
    result = ssh(config, f'printf "{start}%s{end}" "$HOME"', emit=emit, input_callback=input_callback)
    output = result.stdout
    if start in output and end in output:
        home = output.split(start, 1)[1].split(end, 1)[0].strip()
    else:
        home = ""
    if not home:
        raise RuntimeError("Could not resolve remote $HOME.")
    return home


def expand_remote_path(
    config: PipelineConfig,
    remote_path: str,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> str:
    if remote_path == "~":
        return remote_home(config, emit=emit, input_callback=input_callback)
    if remote_path.startswith("~/"):
        return remote_home(config, emit=emit, input_callback=input_callback).rstrip("/") + "/" + remote_path[2:]
    return remote_path


def sftp_quote(value: str | os.PathLike[str]) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def should_upload(path: Path) -> bool:
    if any(part in GL_SYNC_SKIP_DIRS for part in path.parts):
        return False
    if path.suffix in GL_SYNC_SKIP_SUFFIXES:
        return False
    if path.name == ".DS_Store":
        return False
    return True


def _gl_sync_tar_bytes(local_gl_sync: Path) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(local_gl_sync.rglob("*")):
            rel = path.relative_to(local_gl_sync)
            if not path.is_file() or not should_upload(rel):
                continue
            data = path.read_bytes()
            if path.suffix in INFERENCE_CONFIG_SUFFIXES:
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            info = tarfile.TarInfo(rel.as_posix())
            info.size = len(data)
            info.mtime = path.stat().st_mtime
            info.mode = 0o755 if path.suffix == ".sh" else 0o644
            archive.addfile(info, BytesIO(data))
    return buffer.getvalue()


def _tar_file_bytes(paths: list[Path], arcname_for: Callable[[Path], str], *, gzip: bool = False) -> bytes:
    buffer = BytesIO()
    mode = "w:gz" if gzip else "w"
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for path in paths:
            data = path.read_bytes()
            info = tarfile.TarInfo(arcname_for(path))
            info.size = len(data)
            info.mtime = path.stat().st_mtime
            info.mode = 0o644
            archive.addfile(info, BytesIO(data))
    return buffer.getvalue()


def _tar_file_to_temp(paths: list[Path], arcname_for: Callable[[Path], str]) -> Path:
    temp = tempfile.NamedTemporaryFile(prefix="sleap-upload-", suffix=".tar", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    try:
        with tarfile.open(temp_path, mode="w") as archive:
            for path in paths:
                archive.add(path, arcname=arcname_for(path), recursive=False)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _remote_root_shell(remote_spec: str) -> str:
    remote_spec = remote_spec.rstrip("/")
    if remote_spec == "~":
        return "$HOME"
    if remote_spec.startswith("~/"):
        return "$HOME/" + remote_spec[2:]
    return remote_spec


def _remote_assignment(name: str, value: str) -> str:
    if value == "$HOME" or value.startswith("$HOME/"):
        return f'{name}="{value}"'
    return f"{name}={shlex.quote(value)}"


def bootstrap_gl_sync_single_ssh(
    config: PipelineConfig,
    local_gl_sync: Path,
    tasks_root: str,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> str:
    local_gl_sync = local_gl_sync.resolve()
    if not local_gl_sync.is_dir():
        raise FileNotFoundError(f"Local gl_sync directory not found: {local_gl_sync}")

    required = ["install.sh", "train.sh", "predict.sh", "sleap_common.sh"]
    missing = [name for name in required if not (local_gl_sync / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing required GL scripts in local gl_sync: " + ", ".join(missing))

    archive = _gl_sync_tar_bytes(local_gl_sync)
    remote_root = _remote_root_shell(config.gl_sync_remote)
    remote_command = (
        "set -e; "
        f"{_remote_assignment('remote_root', remote_root)}; "
        f"tasks_root={shlex.quote(tasks_root)}; "
        'mkdir -p "$remote_root" "$tasks_root"; '
        'tar -xzf - -C "$remote_root"; '
        'chmod +x "$remote_root"/*.sh; '
        '"$remote_root/install.sh" --check || "$remote_root/install.sh"'
    )
    args = ["ssh", *ssh_multiplex_options(config), config.ssh_target, remote_command]
    if input_callback and sys.platform == "win32":
        _run_binary_with_windows_askpass(args, archive, emit=emit)
    else:
        run_streaming_binary(args, archive, emit=emit)

    if config.gl_sync_remote == "~" or config.gl_sync_remote.startswith("~/"):
        return "/home/" + safe_control_name(config.gl_user) + ("" if config.gl_sync_remote == "~" else "/" + config.gl_sync_remote[2:].rstrip("/"))
    return config.gl_sync_remote.rstrip("/")


def submit_train_single_ssh(
    config: PipelineConfig,
    task_remote_root: str,
    zip_file: Path,
    run_name: str,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    zip_file = zip_file.resolve()
    archive = _tar_file_bytes([zip_file], lambda path: path.name)
    script_root = _remote_root_shell(config.gl_sync_remote)
    remote_command = (
        "set -e; "
        f"work={shlex.quote(task_remote_root)}; "
        f"{_remote_assignment('script_root', script_root)}; "
        'mkdir -p "$work/training_package"; '
        'tar -xf - -C "$work/training_package"; '
        f"SLEAP_SCRATCH_DIR={shlex.quote(task_remote_root)} "
        '"$script_root/train.sh" '
        f"{shlex.quote(zip_file.name)} {shlex.quote(run_name)}"
    )
    args = ["ssh", *ssh_multiplex_options(config), config.ssh_target, remote_command]
    if input_callback and sys.platform == "win32":
        return _run_binary_with_windows_askpass(args, archive, emit=emit)
    return run_streaming_binary(args, archive, emit=emit)


def submit_predict_single_ssh(
    config: PipelineConfig,
    task_remote_root: str,
    videos: list[Path],
    model: str,
    preset: str,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    video_paths = [path.resolve() for path in videos]
    archive_path = _tar_file_to_temp(video_paths, lambda path: path.name)
    script_root = _remote_root_shell(config.gl_sync_remote)
    video_names = " ".join(shlex.quote(path.name) for path in video_paths)
    remote_command = (
        "set -e; "
        f"work={shlex.quote(task_remote_root)}; "
        f"{_remote_assignment('script_root', script_root)}; "
        'mkdir -p "$work/videos" "$work/exports"; '
        'tar -xf - -C "$work/videos"; '
        f"for video_name in {video_names}; do "
        f"SLEAP_SCRATCH_DIR={shlex.quote(task_remote_root)} "
        '"$script_root/predict.sh" '
        f"--preset {shlex.quote(preset)} "
        '"videos/${video_name}" '
        f"models/{shlex.quote(model)}; "
        "done"
    )
    args = ["ssh", *ssh_multiplex_options(config), config.ssh_target, remote_command]
    try:
        if input_callback and sys.platform == "win32":
            return _run_file_with_windows_askpass(args, archive_path, emit=emit)
        return run_streaming_file(args, archive_path, emit=emit)
    finally:
        archive_path.unlink(missing_ok=True)


def download_remote_path_tar(
    config: PipelineConfig,
    remote_path: str,
    local_parent: Path,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> Path:
    local_parent.mkdir(parents=True, exist_ok=True)
    marker = f"__SLEAP_TAR_START_{uuid.uuid4().hex}__"
    output = Path(tempfile.NamedTemporaryFile(prefix="sleap-download-", suffix=".tar.gz", delete=False).name)
    captured = Path(tempfile.NamedTemporaryFile(prefix="sleap-ssh-output-", suffix=".bin", delete=False).name)
    remote_command = (
        "set -e; "
        f"target={shlex.quote(remote_path)}; "
        f"printf '\\n{marker}\\n'; "
        'parent="$(dirname "$target")"; '
        'name="$(basename "$target")"; '
        'tar -C "$parent" -czf - "$name"'
    )
    args = ["ssh", *ssh_multiplex_options(config), config.ssh_target, remote_command]
    try:
        if input_callback and sys.platform == "win32":
            _capture_to_file_with_windows_askpass(args, captured, emit=emit)
        else:
            run_capture_to_file(args, captured, emit=emit)

        marker_bytes = ("\n" + marker + "\n").encode()
        if not _copy_after_marker(captured, output, marker_bytes):
            preview = captured.read_bytes()[:1200].decode(errors="replace")
            raise RuntimeError("Could not find download marker in SSH output. Remote shell may be printing startup text:\n" + preview)
        names: list[str] = []
        with tarfile.open(output, mode="r:gz") as archive:
            names = [
                member.name.split("/", 1)[0]
                for member in archive.getmembers()
                if member.name and not member.name.startswith("/")
            ]
            archive.extractall(local_parent)
        if names:
            return local_parent / names[0]
        return local_parent
    finally:
        captured.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def upload_gl_sync(
    config: PipelineConfig,
    local_gl_sync: Path,
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
) -> str:
    local_gl_sync = local_gl_sync.resolve()
    if not local_gl_sync.is_dir():
        raise FileNotFoundError(f"Local gl_sync directory not found: {local_gl_sync}")

    required = ["install.sh", "train.sh", "predict.sh", "sleap_common.sh"]
    missing = [name for name in required if not (local_gl_sync / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing required GL scripts in local gl_sync: " + ", ".join(missing))

    files = [
        path
        for path in sorted(local_gl_sync.rglob("*"))
        if path.is_file() and should_upload(path.relative_to(local_gl_sync))
    ]
    remote_spec = config.gl_sync_remote.rstrip("/")
    start = "__SLEAP_HOME_START__"
    end = "__SLEAP_HOME_END__"

    if remote_spec == "~" or remote_spec.startswith("~/"):
        rel_root = "" if remote_spec == "~" else remote_spec[2:]
        rel_dirs = sorted(
            {
                str(path.parent.relative_to(local_gl_sync)).replace("\\", "/")
                for path in files
                if path.parent != local_gl_sync
            }
        )
        mkdir_parts = [f'"$HOME/{rel_root}"' if rel_root else '"$HOME"']
        for rel_dir in rel_dirs:
            if rel_root:
                mkdir_parts.append(f'"$HOME/{rel_root}/{rel_dir}"')
            else:
                mkdir_parts.append(f'"$HOME/{rel_dir}"')
        mkdir_cmd = " ".join(mkdir_parts)
        result = ssh(
            config,
            f'printf "{start}%s{end}" "$HOME" && mkdir -p {mkdir_cmd}',
            emit=emit,
            input_callback=input_callback,
        )
        output = result.stdout
        if start not in output or end not in output:
            raise RuntimeError("Could not resolve remote $HOME.")
        home = output.split(start, 1)[1].split(end, 1)[0].strip()
        if not home:
            raise RuntimeError("Could not resolve remote $HOME.")
        remote_root = f"{home}/{rel_root}".rstrip("/") if rel_root else home
    else:
        remote_root = remote_spec
        rel_dirs = sorted(
            {
                str(path.parent.relative_to(local_gl_sync)).replace("\\", "/")
                for path in files
                if path.parent != local_gl_sync
            }
        )
        mkdir_parts = [shlex.quote(remote_root), *[shlex.quote(f"{remote_root}/{rel_dir}") for rel_dir in rel_dirs]]
        ssh(config, f"mkdir -p {' '.join(mkdir_parts)}", emit=emit, input_callback=input_callback)

    if emit:
        emit(f"Uploading gl_sync to GL: {local_gl_sync} -> {remote_root}")

    commands: list[str] = []
    for path in files:
        rel = path.relative_to(local_gl_sync)
        remote_file = remote_root + "/" + rel.as_posix()
        commands.append(f"put {sftp_quote(path)} {sftp_quote(remote_file)}")
    sftp_batch(config, commands, emit=emit, input_callback=input_callback)
    ssh(
        config,
        (
            f"find {shlex.quote(remote_root)} -type f "
            "\\( -name '*.sh' -o -name '*.conf' -o -name '*.env' \\) "
            "-exec sed -i 's/\\r$//' {} +"
        ),
        emit=emit,
        input_callback=input_callback,
    )
    if emit:
        emit(f"Uploaded {len(files)} gl_sync file(s).")
    return remote_root


def remote_task_dir(config: PipelineConfig, task: str) -> str:
    if not config.gl_scratch_dir:
        raise ValueError("GL scratch dir is not configured.")
    return f"{config.gl_scratch_dir.rstrip('/')}/tasks/{safe_task_name(task)}"


def bootstrap_local_dirs(config: PipelineConfig) -> None:
    config.local_project_path.mkdir(parents=True, exist_ok=True)
    (config.local_project_path / "tasks").mkdir(parents=True, exist_ok=True)
    save_pipeline_log(config, load_pipeline_log(config))


def default_sleap_command() -> str:
    candidates = [
        Path.home() / "sleap_gui_env" / "bin" / "sleap",
        Path.home() / "sleap_gui_env" / "bin" / "sleap-label",
        "sleap",
        "sleap-label",
    ]
    for candidate in candidates:
        try:
            subprocess.run([str(candidate), "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return str(candidate)
        except FileNotFoundError:
            continue
    return sys.executable


def local_video_record(path: Path) -> dict:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}

from __future__ import annotations

import json
import os
import pty
import select
import signal
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


GL_HOST_DEFAULT = "greatlakes.arc-ts.umich.edu"
CONFIG_PATH = Path.home() / ".sleap_pipeline.json"
LOG_NAME = "pipeline.log.json"
GL_SYNC_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "tasks"}
GL_SYNC_SKIP_SUFFIXES = {".pyc", ".pyo"}
InputCallback = Callable[[str, bool, str | None], str | None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PipelineConfig:
    gl_user: str = ""
    slurm_account: str = ""
    gl_host: str = GL_HOST_DEFAULT
    gl_scratch_dir: str = ""
    local_project: str = str(Path.home() / "sleap_project")
    sleap_label_cmd: str = ""
    default_preset: str = "single_rat_sensitive"
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
    data.setdefault("jobs", []).append({"submitted_at": utc_now(), "status": "submitted", **job})
    save_pipeline_log(config, data)


def mark_download(config: PipelineConfig, kind: str, record: dict) -> None:
    key = {"model": "downloaded_models", "prediction": "downloaded_predictions"}[kind]
    data = load_pipeline_log(config)
    data.setdefault(key, []).append({"downloaded_at": utc_now(), **record})
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
        package_dir = root / "training_package"
        if package_dir.exists():
            zips.extend(sorted(package_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True))
    return zips


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def run_streaming(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    emit(f"$ {shell_join(args)}")
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
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
    if input_callback:
        return run_interactive(["ssh", config.ssh_target, remote_command], emit=emit, check=check, input_callback=input_callback)
    return run_streaming(["ssh", config.ssh_target, remote_command], emit=emit, check=check)


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
    if input_callback:
        emit("$ sftp " + config.ssh_target)
        batch = "\n".join(commands + ["bye"]) + "\n"
        return run_interactive(
            ["sftp", config.ssh_target],
            emit=emit,
            input_callback=input_callback,
            stdin_text=batch,
            wait_for_prompt="sftp>",
        )
    emit("$ sftp -b - " + config.ssh_target)
    proc = subprocess.run(
        ["sftp", "-b", "-", config.ssh_target],
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
    result = ssh(config, 'printf "%s" "$HOME"', emit=emit, input_callback=input_callback)
    home = result.stdout.strip()
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

    remote_root = expand_remote_path(config, config.gl_sync_remote, emit=emit, input_callback=input_callback).rstrip("/")
    files = [
        path
        for path in sorted(local_gl_sync.rglob("*"))
        if path.is_file() and should_upload(path.relative_to(local_gl_sync))
    ]
    dirs = sorted({remote_root + "/" + str(path.parent.relative_to(local_gl_sync)) for path in files if path.parent != local_gl_sync})

    if emit:
        emit(f"Uploading gl_sync to GL: {local_gl_sync} -> {remote_root}")
    lib_dirs = " ".join(shlex.quote(path) for path in [remote_root, *dirs])
    ssh(config, f"mkdir -p {lib_dirs}", emit=emit, input_callback=input_callback)

    commands: list[str] = []
    for path in files:
        rel = path.relative_to(local_gl_sync)
        remote_file = remote_root + "/" + rel.as_posix()
        commands.append(f"put {sftp_quote(path)} {sftp_quote(remote_file)}")
    sftp_batch(config, commands, emit=emit, input_callback=input_callback)
    ssh(config, f"chmod +x {shlex.quote(remote_root)}/*.sh", emit=emit, check=False, input_callback=input_callback)
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

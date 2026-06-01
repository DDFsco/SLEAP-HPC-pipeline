from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


GL_HOST_DEFAULT = "greatlakes.arc-ts.umich.edu"
CONFIG_PATH = Path.home() / ".sleap_pipeline.json"
LOG_NAME = "pipeline.log.json"
GL_SYNC_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "tasks"}
GL_SYNC_SKIP_SUFFIXES = {".pyc", ".pyo"}
INFERENCE_CONFIG_SUFFIXES = {".conf", ".env", ".sh"}
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
                "time": refs.get(key, {}).get("time", datetime.fromtimestamp(model_dir.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")),
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
        exports_dir = task_root(config, task_name) / "exports"
        if not exports_dir.exists():
            continue
        for export_file in sorted(exports_dir.glob("*.slp"), key=lambda p: p.stat().st_mtime, reverse=True):
            clean_task = safe_task_name(task_name)
            key = (clean_task, export_file.name)
            refs[key] = {
                **refs.get(key, {}),
                "task": clean_task,
                "file": export_file.name,
                "remote_rel": refs.get(key, {}).get("remote_rel", f"exports/{export_file.name}"),
                "source": "local export file",
                "time": refs.get(key, {}).get("time", datetime.fromtimestamp(export_file.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")),
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
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=15m",
        "-o",
        f"ControlPath={ssh_control_path(config)}",
    ]


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
    if sys.platform == "win32":
        return _run_interactive_windows(
            args,
            emit=emit,
            input_callback=input_callback,
            check=check,
            stdin_text=stdin_text,
            wait_for_prompt=wait_for_prompt,
        )
    return _run_interactive_unix(
        args,
        emit=emit,
        input_callback=input_callback,
        check=check,
        stdin_text=stdin_text,
        wait_for_prompt=wait_for_prompt,
    )


def _run_interactive_windows(
    args: list[str],
    emit: Callable[[str], None] | None = None,
    input_callback: InputCallback | None = None,
    check: bool = True,
    stdin_text: str | None = None,
    wait_for_prompt: str | None = None,
) -> subprocess.CompletedProcess[str]:
    emit = emit or (lambda line: None)
    cmd = list(args)
    if cmd and cmd[0] == "ssh" and "-tt" not in cmd and "-t" not in cmd:
        cmd = [cmd[0], "-tt", *cmd[1:]]
    emit(f"$ {shell_join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    output: list[str] = []
    pending = ""
    sent_stdin = False
    lock = threading.Lock()
    stop = threading.Event()

    def reader() -> None:
        nonlocal pending
        assert proc.stdout is not None
        while not stop.is_set():
            try:
                data = proc.stdout.read(4096)
            except OSError:
                break
            if not data:
                break
            text = data.decode(errors="replace")
            with lock:
                output.append(text)
                pending = (pending + text)[-2000:]
            for line in text.replace("\r", "").splitlines():
                if line.strip():
                    emit(line.rstrip())

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    try:
        while proc.poll() is None:
            with lock:
                current_pending = pending

            if stdin_text is not None and not sent_stdin and wait_for_prompt and wait_for_prompt in current_pending:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text.encode())
                proc.stdin.flush()
                sent_stdin = True

            if input_callback:
                prompt = prompt_kind(current_pending)
                if prompt:
                    label, secret, default = prompt
                    response = input_callback(label + "\n\n" + current_pending.strip()[-500:], secret, default)
                    if response is None:
                        proc.terminate()
                        raise RuntimeError("Authentication input cancelled.")
                    assert proc.stdin is not None
                    proc.stdin.write((response + "\n").encode())
                    proc.stdin.flush()
                    with lock:
                        pending = ""

            if stdin_text is not None and not sent_stdin and not wait_for_prompt:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text.encode())
                proc.stdin.flush()
                sent_stdin = True

            time.sleep(0.1)
    finally:
        stop.set()
        if proc.stdin is not None:
            proc.stdin.close()
        reader_thread.join(timeout=5)

    code = proc.returncode if proc.returncode is not None else 1
    completed = subprocess.CompletedProcess(args, code, "".join(output), "")
    if check and code:
        raise subprocess.CalledProcessError(code, args, completed.stdout, completed.stderr)
    return completed


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

from __future__ import annotations

import json
import os
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


def ssh(config: PipelineConfig, remote_command: str, emit: Callable[[str], None] | None = None, check: bool = True):
    return run_streaming(["ssh", config.ssh_target, remote_command], emit=emit, check=check)


def ssh_check(config: PipelineConfig, emit: Callable[[str], None] | None = None) -> bool:
    try:
        ssh(config, "echo ok", emit=emit)
        return True
    except Exception as exc:
        if emit:
            emit(f"SSH check failed: {exc}")
        return False


def sftp_batch(config: PipelineConfig, commands: list[str], emit: Callable[[str], None] | None = None):
    emit = emit or (lambda line: None)
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


def remote_task_dir(config: PipelineConfig, task: str) -> str:
    if not config.gl_scratch_dir:
        raise ValueError("GL scratch dir is not configured.")
    return f"{config.gl_scratch_dir.rstrip('/')}/tasks/{safe_task_name(task)}"


def bootstrap_local_dirs(config: PipelineConfig) -> None:
    config.local_project_path.mkdir(parents=True, exist_ok=True)
    (config.local_project_path / "tasks").mkdir(parents=True, exist_ok=True)
    save_pipeline_log(config, load_pipeline_log(config))


def default_sleap_command() -> str:
    candidates = ["sleap", "sleap-label"]
    for name in candidates:
        try:
            subprocess.run([name, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return name
        except FileNotFoundError:
            continue
    return sys.executable


def local_video_record(path: Path) -> dict:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}

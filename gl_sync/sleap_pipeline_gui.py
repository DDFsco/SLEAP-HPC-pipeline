from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pipeline_lib as lib


class PipelineApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SLEAP Great Lakes Pipeline")
        self.geometry("980x680")
        self.config_data = lib.load_config()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build()
        self._load_config_to_ui()
        self.refresh_history()
        self.after(100, self._drain_log_queue)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")

        self.pipeline_tab = ttk.Frame(notebook, padding=12)
        self.history_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        self.log_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.pipeline_tab, text="Pipeline")
        notebook.add(self.history_tab, text="History")
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.log_tab, text="Logs")

        self._build_pipeline_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self._build_log_tab()

        self.status = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status, anchor="w").grid(row=1, column=0, sticky="ew", padx=12, pady=6)

    def _build_pipeline_tab(self) -> None:
        self.pipeline_tab.columnconfigure(0, weight=1)
        buttons = [
            ("Login GL / Bootstrap", self.login_gl),
            ("Open SLEAP", self.open_sleap),
            ("Train", self.train),
            ("Download Model", self.download_model),
            ("Predict", self.predict),
            ("Download Predictions", self.download_predictions),
        ]
        for row, (label, command) in enumerate(buttons):
            ttk.Button(self.pipeline_tab, text=label, command=command).grid(row=row, column=0, sticky="ew", pady=6)

    def _build_history_tab(self) -> None:
        self.history_tab.rowconfigure(0, weight=1)
        self.history_tab.columnconfigure(0, weight=1)
        columns = ("time", "type", "task", "run", "package", "job_id", "status")
        self.history_tree = ttk.Treeview(self.history_tab, columns=columns, show="headings", height=18)
        headings = {
            "time": "Submitted / Downloaded",
            "type": "Type",
            "task": "Task",
            "run": "Run / Model",
            "package": "Package / File",
            "job_id": "Job ID",
            "status": "Status",
        }
        widths = {
            "time": 160,
            "type": 90,
            "task": 140,
            "run": 180,
            "package": 260,
            "job_id": 90,
            "status": 90,
        }
        for column in columns:
            self.history_tree.heading(column, text=headings[column])
            self.history_tree.column(column, width=widths[column], anchor="w")
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.history_tab, command=self.history_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.history_tree.configure(yscrollcommand=scrollbar.set)
        ttk.Button(self.history_tab, text="Refresh", command=self.refresh_history).grid(row=1, column=0, sticky="e", pady=(10, 0))

    def _build_settings_tab(self) -> None:
        self.settings_tab.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.StringVar] = {}
        fields = [
            ("gl_user", "GL uniqname"),
            ("slurm_account", "SLURM account"),
            ("gl_host", "GL host"),
            ("gl_scratch_dir", "GL scratch dir"),
            ("local_project", "Local project"),
            ("sleap_label_cmd", "SLEAP command"),
            ("default_preset", "Default preset"),
            ("gl_sync_remote", "Remote gl_sync"),
        ]
        for row, (key, label) in enumerate(fields):
            ttk.Label(self.settings_tab, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            self.vars[key] = var
            ttk.Entry(self.settings_tab, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)
            if key == "local_project":
                ttk.Button(self.settings_tab, text="Browse", command=self._browse_local_project).grid(row=row, column=2, padx=6)
            elif key == "sleap_label_cmd":
                ttk.Button(self.settings_tab, text="Browse", command=self._browse_sleap_cmd).grid(row=row, column=2, padx=6)
        ttk.Button(self.settings_tab, text="Save Settings", command=self.save_settings).grid(row=len(fields), column=1, sticky="e", pady=12)

    def _build_log_tab(self) -> None:
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.log_text = tk.Text(self.log_tab, wrap="word", height=28)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.log_tab, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _load_config_to_ui(self) -> None:
        for key, var in self.vars.items():
            var.set(str(getattr(self.config_data, key)))

    def save_settings(self) -> None:
        for key, var in self.vars.items():
            setattr(self.config_data, key, var.get().strip())
        for message in lib.ensure_config_defaults(self.config_data):
            self.emit(message)
        self._load_config_to_ui()
        lib.save_config(self.config_data)
        lib.bootstrap_local_dirs(self.config_data)
        self.refresh_history()
        self.emit("Settings saved.")
        self.status.set("Settings saved")

    def _browse_local_project(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.vars["local_project"].set(path)

    def _browse_sleap_cmd(self) -> None:
        path = filedialog.askopenfilename()
        if path:
            self.vars["sleap_label_cmd"].set(path)

    def emit(self, line: str) -> None:
        self.log_queue.put(line)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.after(100, self._drain_log_queue)

    def run_threaded(self, label: str, func) -> None:
        def worker() -> None:
            self.status.set(f"{label} running")
            try:
                func()
                self.status.set(f"{label} finished")
            except Exception as exc:
                self.emit(f"ERROR: {exc}")
                self.status.set(f"{label} failed")
                self.after(0, lambda: messagebox.showerror(label, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def auth_input(self, prompt: str, secret: bool, default: str | None = None) -> str | None:
        done = threading.Event()
        result: dict[str, str | None] = {"value": None}

        def ask() -> None:
            result["value"] = simpledialog.askstring(
                "Great Lakes Authentication",
                prompt,
                initialvalue=default or "",
                show="*" if secret else None,
                parent=self,
            )
            done.set()

        self.after(0, ask)
        done.wait()
        return result["value"]

    def login_gl(self) -> None:
        self.save_settings()

        def work() -> None:
            lib.bootstrap_local_dirs(self.config_data)
            if not lib.ssh_check(self.config_data, emit=self.emit, input_callback=self.auth_input):
                raise RuntimeError("SSH check failed. Configure SSH keys for Great Lakes first.")
            local_gl_sync = Path(__file__).resolve().parent
            remote_gl_sync = lib.upload_gl_sync(self.config_data, local_gl_sync, emit=self.emit, input_callback=self.auth_input)
            remote_tasks = f"mkdir -p {lib.remote_task_dir(self.config_data, '_bootstrap_check').rsplit('/', 1)[0]}"
            lib.ssh(self.config_data, remote_tasks, emit=self.emit, input_callback=self.auth_input)
            check = lib.ssh(
                self.config_data,
                f"bash {sh_quote(remote_gl_sync + '/install.sh')} --check",
                emit=self.emit,
                check=False,
                input_callback=self.auth_input,
            )
            if check.returncode:
                self.emit("GL install check failed; running remote install.sh.")
                lib.ssh(self.config_data, f"bash {sh_quote(remote_gl_sync + '/install.sh')}", emit=self.emit, input_callback=self.auth_input)
            else:
                self.emit("GL install check passed.")
            self.emit("GL SSH, gl_sync upload, environment check, and task root are ready.")

        self.run_threaded("Login GL", work)

    def _ask_task(self, create: bool = True) -> str | None:
        tasks = lib.list_tasks(self.config_data)
        prompt = "Task name"
        initial = tasks[-1] if tasks else ""
        task = simpledialog.askstring("Task", prompt, initialvalue=initial, parent=self)
        if not task:
            return None
        root = lib.ensure_task(self.config_data, task) if create else lib.task_root(self.config_data, task)
        self.emit(f"Task: {root}")
        return lib.safe_task_name(task)

    def refresh_history(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        try:
            data = lib.load_pipeline_log(self.config_data)
        except Exception:
            return
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        for job in data.get("jobs", []):
            rows.append(
                (
                    job.get("submitted_at", ""),
                    job.get("type", ""),
                    job.get("task", ""),
                    job.get("run_name") or job.get("model", ""),
                    job.get("training_package") or job.get("video", ""),
                    job.get("job_id", ""),
                    job.get("status", ""),
                )
            )
        for record in data.get("downloaded_models", []):
            rows.append(
                (
                    record.get("downloaded_at", ""),
                    "model download",
                    record.get("task", ""),
                    record.get("run_name", ""),
                    Path(record.get("path", "")).name,
                    "",
                    "downloaded",
                )
            )
        for record in data.get("downloaded_predictions", []):
            rows.append(
                (
                    record.get("downloaded_at", ""),
                    "prediction download",
                    record.get("task", ""),
                    "",
                    record.get("file", ""),
                    "",
                    "downloaded",
                )
            )
        for row in sorted(rows, key=lambda item: item[0], reverse=True):
            self.history_tree.insert("", "end", values=row)

    def open_sleap(self) -> None:
        self.save_settings()
        task = self._ask_task(create=True)
        if not task:
            return
        labels_dir = lib.task_root(self.config_data, task) / "labels"
        cmd = self.config_data.sleap_label_cmd or lib.default_sleap_command()
        try:
            subprocess.Popen([cmd, str(labels_dir)])
        except FileNotFoundError as exc:
            messagebox.showerror("Open SLEAP", f"Could not launch SLEAP command: {cmd}\n{exc}")
            return
        self.emit(f"Opened SLEAP for {labels_dir}")
        messagebox.showinfo("Export Training Package", "After labeling, export the training package zip into this task's training_package folder.")

    def train(self) -> None:
        self.save_settings()
        selection = TrainingPackageDialog(self, self.config_data).show()
        if selection is None:
            return
        task, zip_file, run_name = selection

        def work() -> None:
            remote_root = lib.remote_task_dir(self.config_data, task)
            lib.ssh(self.config_data, f"mkdir -p {remote_root}/training_package", emit=self.emit, input_callback=self.auth_input)
            lib.sftp_batch(
                self.config_data,
                [f"put {sftp_quote(zip_file)} {sftp_quote(remote_root + '/training_package/' + zip_file.name)}"],
                emit=self.emit,
                input_callback=self.auth_input,
            )
            remote_cmd = (
                f"SLEAP_SCRATCH_DIR={sh_quote(remote_root)} "
                f"bash {self.config_data.gl_sync_remote}/train.sh {sh_quote(zip_file.name)} {sh_quote(run_name)}"
            )
            result = lib.ssh(self.config_data, remote_cmd, emit=self.emit, input_callback=self.auth_input)
            job_id = parse_job_id(result.stdout)
            lib.append_job(
                self.config_data,
                {
                    "type": "train",
                    "task": task,
                    "run_name": run_name,
                    "training_package": zip_file.name,
                    "training_package_path": str(zip_file),
                    "job_id": job_id,
                },
            )
            self.after(0, self.refresh_history)

        self.run_threaded("Train", work)

    def download_model(self) -> None:
        self.save_settings()
        task = self._ask_task(create=False)
        if not task:
            return
        run_name = simpledialog.askstring("Model", "Model/run name", parent=self)
        if not run_name:
            return

        def work() -> None:
            local_dir = lib.ensure_task(self.config_data, task) / "models" / run_name
            local_dir.mkdir(parents=True, exist_ok=True)
            remote_dir = f"{lib.remote_task_dir(self.config_data, task)}/models/{run_name}"
            lib.sftp_batch(
                self.config_data,
                [f"get -r {sftp_quote(remote_dir)} {sftp_quote(local_dir.parent)}"],
                emit=self.emit,
                input_callback=self.auth_input,
            )
            lib.mark_download(self.config_data, "model", {"task": task, "run_name": run_name, "path": str(local_dir)})
            self.after(0, self.refresh_history)

        self.run_threaded("Download Model", work)

    def predict(self) -> None:
        self.save_settings()
        task = self._ask_task(create=True)
        if not task:
            return
        model = simpledialog.askstring("Model", "Model directory name", parent=self)
        if not model:
            return
        videos = filedialog.askopenfilenames(title="Select videos")
        if not videos:
            return
        preset = simpledialog.askstring("Preset", "Preset", initialvalue=self.config_data.default_preset, parent=self)
        if not preset:
            return

        def work() -> None:
            remote_root = lib.remote_task_dir(self.config_data, task)
            lib.ssh(self.config_data, f"mkdir -p {remote_root}/videos {remote_root}/exports", emit=self.emit, input_callback=self.auth_input)
            for video_name in videos:
                video = Path(video_name)
                remote_video = f"{remote_root}/videos/{video.name}"
                check = lib.ssh(
                    self.config_data,
                    f"test -f {sh_quote(remote_video)} && stat -c %s {sh_quote(remote_video)} || echo missing",
                    emit=self.emit,
                    check=False,
                    input_callback=self.auth_input,
                )
                if str(video.stat().st_size) in check.stdout.split():
                    self.emit(f"skip upload: {video.name} (same size on GL)")
                else:
                    lib.sftp_batch(
                        self.config_data,
                        [f"put {sftp_quote(video)} {sftp_quote(remote_video)}"],
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                remote_cmd = (
                    f"SLEAP_SCRATCH_DIR={sh_quote(remote_root)} "
                    f"bash {self.config_data.gl_sync_remote}/predict.sh --preset {sh_quote(preset)} "
                    f"videos/{sh_quote(video.name)} models/{sh_quote(model)}"
                )
                result = lib.ssh(self.config_data, remote_cmd, emit=self.emit, input_callback=self.auth_input)
                lib.append_job(
                    self.config_data,
                    {
                        "type": "predict",
                        "task": task,
                        "model": model,
                        "video": video.name,
                        "preset": preset,
                        "job_id": parse_job_id(result.stdout),
                        "expected_output": f"exports/{video.stem}.predicted.slp",
                    },
                )
            self.after(0, self.refresh_history)

        self.run_threaded("Predict", work)

    def download_predictions(self) -> None:
        self.save_settings()
        task = self._ask_task(create=False)
        if not task:
            return

        def work() -> None:
            root = lib.ensure_task(self.config_data, task)
            local_exports = root / "exports"
            remote_exports = f"{lib.remote_task_dir(self.config_data, task)}/exports"
            listing = lib.ssh(
                self.config_data,
                f"ls {remote_exports}/*.predicted.slp 2>/dev/null || true",
                emit=self.emit,
                input_callback=self.auth_input,
            )
            for remote_file in [line.strip() for line in listing.stdout.splitlines() if line.strip().endswith(".slp")]:
                local_file = local_exports / Path(remote_file).name
                if local_file.exists():
                    continue
                lib.sftp_batch(
                    self.config_data,
                    [f"get {sftp_quote(remote_file)} {sftp_quote(local_file)}"],
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
                lib.mark_download(self.config_data, "prediction", {"task": task, "file": local_file.name, "path": str(local_file)})
            self.after(0, self.refresh_history)

        self.run_threaded("Download Predictions", work)


class TrainingPackageDialog:
    def __init__(self, parent: PipelineApp, config: lib.PipelineConfig) -> None:
        self.parent = parent
        self.config = config
        self.result: tuple[str, Path, str] | None = None
        self.window: tk.Toplevel | None = None
        self.task_var = tk.StringVar()
        self.package_var = tk.StringVar()
        self.run_var = tk.StringVar()
        self.package_combo: ttk.Combobox | None = None
        self.packages_by_task = self._load_packages()

    def _load_packages(self) -> dict[str, list[Path]]:
        packages: dict[str, list[Path]] = {}
        for task in lib.list_tasks(self.config):
            zips = lib.list_training_zips(self.config, task)
            if zips:
                packages[task] = zips
        return packages

    def show(self) -> tuple[str, Path, str] | None:
        if not self.packages_by_task:
            messagebox.showwarning("Train", "No training zip found under tasks/*/training_package/.", parent=self.parent)
            return None

        self.window = tk.Toplevel(self.parent)
        self.window.title("Select Training Package")
        self.window.transient(self.parent)
        self.window.grab_set()
        self.window.columnconfigure(1, weight=1)

        ttk.Label(self.window, text="Task").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        task_combo = ttk.Combobox(self.window, textvariable=self.task_var, values=list(self.packages_by_task), state="readonly")
        task_combo.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 6))

        ttk.Label(self.window, text="Training package").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        self.package_combo = ttk.Combobox(self.window, textvariable=self.package_var, state="readonly")
        self.package_combo.grid(row=1, column=1, sticky="ew", padx=12, pady=6)

        ttk.Label(self.window, text="Run name").grid(row=2, column=0, sticky="w", padx=12, pady=6)
        ttk.Entry(self.window, textvariable=self.run_var).grid(row=2, column=1, sticky="ew", padx=12, pady=6)

        button_row = ttk.Frame(self.window)
        button_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(button_row, text="Cancel", command=self.window.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(button_row, text="Train", command=self._confirm).grid(row=0, column=1)

        task_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_packages())
        self.package_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_run_name())
        first_task = next(iter(self.packages_by_task))
        self.task_var.set(first_task)
        self._sync_packages()

        self.window.wait_window()
        return self.result

    def _sync_packages(self) -> None:
        if self.window is None:
            return
        task = self.task_var.get()
        package_names = [path.name for path in self.packages_by_task.get(task, [])]
        if self.package_combo is None:
            return
        self.package_combo.configure(values=package_names)
        if package_names:
            self.package_var.set(package_names[0])
        else:
            self.package_var.set("")
        self._sync_run_name()

    def _sync_run_name(self) -> None:
        task = lib.safe_task_name(self.task_var.get())
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
        self.run_var.set(f"{task}_{timestamp}")

    def _confirm(self) -> None:
        if self.window is None:
            return
        task = lib.safe_task_name(self.task_var.get())
        package_name = self.package_var.get()
        run_name = lib.safe_task_name(self.run_var.get())
        zip_file = next((path for path in self.packages_by_task.get(task, []) if path.name == package_name), None)
        if zip_file is None:
            messagebox.showerror("Train", "Select a training package.", parent=self.window)
            return
        self.result = (task, zip_file, run_name)
        self.window.destroy()


def parse_job_id(output: str) -> str:
    for token in output.replace(".", " ").split():
        if token.isdigit() and len(token) >= 5:
            return token
    return ""


def sh_quote(value: str | os.PathLike[str]) -> str:
    import shlex

    return shlex.quote(str(value))


def sftp_quote(value: str | os.PathLike[str]) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


if __name__ == "__main__":
    PipelineApp().mainloop()

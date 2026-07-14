from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pipeline_lib as lib


BG = "#f5f6fa"
WHITE = "#ffffff"
DARK = "#2c3e50"
MUTED = "#6b7280"
BUTTON_BG = "#34495e"
LOG_MAX_LINES = 1000

STEP_DEFS = [
    ("1", "Label Videos", "Open SLEAP locally, label frames, then export a Training Job Package zip into the task folder.", "Open SLEAP"),
    ("2", "Train Model", "Select a task training package, upload it to Great Lakes, and submit a GPU Slurm training job.", "Train"),
    ("3", "Run Inference", "Select a trained model and video files, upload videos, and submit prediction jobs on Great Lakes.", "Predict"),
    ("4", "Download Results", "Download trained models or prediction files from Great Lakes back into the local task folders.", "Download"),
    ("5", "Review / Correct", "Open downloaded predictions in SLEAP, correct labels, export a new package, and repeat training if needed.", "Review"),
]


class PipelineApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SLEAP Pipeline Manager - UM Great Lakes")
        self.geometry("1040x760")
        self.configure(bg=BG)
        self.config_data = lib.load_config()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._configure_styles()
        self._build()
        self._load_config_to_ui()
        self.refresh_history()
        self.after(100, self._drain_log_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Pipeline.TButton",
            background=BUTTON_BG,
            foreground=WHITE,
            borderwidth=0,
            focusthickness=0,
            padding=(14, 7),
        )
        style.map(
            "Pipeline.TButton",
            background=[("active", BUTTON_BG), ("pressed", BUTTON_BG), ("disabled", "#95a5a6")],
            foreground=[("active", WHITE), ("pressed", WHITE), ("disabled", "#ecf0f1")],
        )

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(8, 6))

        self.pipeline_tab = tk.Frame(notebook, bg=BG)
        self.history_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        self.log_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.pipeline_tab, text="  Pipeline  ")
        notebook.add(self.history_tab, text="  History  ")
        notebook.add(self.settings_tab, text="  Settings  ")
        notebook.add(self.log_tab, text="  Logs  ")

        self._build_pipeline_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self._build_log_tab()

        self.status = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status, anchor="w").grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=DARK)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="SLEAP Pipeline Manager",
            font=("Arial", 17, "bold"),
            fg=WHITE,
            bg=DARK,
            pady=12,
        ).grid(row=0, column=0, sticky="w", padx=20)
        tk.Label(
            header,
            text="Rothschild Lab",
            font=("Arial", 10),
            fg="#bdc3c7",
            bg=DARK,
        ).grid(row=0, column=1, sticky="e", padx=20)

    def _build_pipeline_tab(self) -> None:
        self.pipeline_tab.columnconfigure(0, weight=1)
        inner = tk.Frame(self.pipeline_tab, bg=BG)
        inner.grid(row=0, column=0, sticky="new")
        inner.columnconfigure(0, weight=1)

        self._tool_card(inner)
        for number, title, desc, action_label in STEP_DEFS:
            if action_label == "Open SLEAP":
                actions = [("Open SLEAP", self.open_sleap)]
            elif action_label == "Train":
                actions = [("Train", self.train)]
            elif action_label == "Predict":
                actions = [("Predict", self.predict)]
            elif action_label == "Download":
                actions = [("Download Model", self.download_model), ("Download Predictions", self.download_predictions)]
            else:
                actions = [("Review Predictions", self.review_predictions)]
            self._step_card(inner, number, title, desc, actions)

    def _tool_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(parent, bg=WHITE, highlightbackground="#dfe6e9", highlightthickness=1)
        card.pack(fill="x", padx=14, pady=(12, 8))
        body = tk.Frame(card, bg=WHITE, padx=14, pady=12)
        body.pack(fill="x")
        tk.Label(body, text="Great Lakes Controls", font=("Arial", 12, "bold"), bg=WHITE, fg=DARK).pack(anchor="w")
        tk.Label(
            body,
            text="Tasks are stored under {GL scratch dir}/tasks/{task}. Bootstrap uploads scripts and checks the remote SLEAP environment.",
            font=("Arial", 9),
            bg=WHITE,
            fg=MUTED,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(4, 10))
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        for label, command in [
            ("Login GL / Bootstrap", self.login_gl),
            ("Show GL Tasks", self.show_gl_tasks),
            ("Show Slurm Jobs", self.show_slurm_jobs),
        ]:
            ttk.Button(
                row,
                text=label,
                command=command,
                style="Pipeline.TButton",
            ).pack(side="left", padx=(0, 8))

    def _step_card(self, parent: tk.Widget, number: str, title: str, desc: str, actions: list[tuple[str, object]]) -> None:
        card = tk.Frame(parent, bg=WHITE, highlightbackground="#dfe6e9", highlightthickness=1)
        card.pack(fill="x", padx=14, pady=7)
        tk.Frame(card, bg=BUTTON_BG, width=5).pack(side="left", fill="y")

        body = tk.Frame(card, bg=WHITE, padx=14, pady=12)
        body.pack(side="left", fill="both", expand=True)

        tk.Label(
            body,
            text=f"Step {number}: {title}",
            font=("Arial", 12, "bold"),
            bg=WHITE,
            fg=DARK,
        ).pack(anchor="w")

        tk.Label(
            body,
            text=desc,
            font=("Arial", 9),
            bg=WHITE,
            fg=MUTED,
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        button_frame = tk.Frame(card, bg=WHITE, padx=12)
        button_frame.pack(side="right", fill="y")
        for label, command in actions:
            ttk.Button(
                button_frame,
                text=label,
                command=command,
                style="Pipeline.TButton",
            ).pack(anchor="e", pady=3)

    def _set_status_text(self, text: str) -> None:
        self.after(0, lambda: self.status.set(text))

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
        inference_configs = lib.list_inference_configs()
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
            if key == "default_preset":
                ttk.Combobox(self.settings_tab, textvariable=var, values=inference_configs, state="readonly").grid(row=row, column=1, sticky="ew", pady=4)
            else:
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
        inference_configs = lib.list_inference_configs()
        if self.config_data.default_preset not in inference_configs:
            self.config_data.default_preset = inference_configs[0]
        for key, var in self.vars.items():
            var.set(str(getattr(self.config_data, key)))

    def save_settings(self) -> None:
        for key, var in self.vars.items():
            setattr(self.config_data, key, var.get().strip())
        inference_configs = lib.list_inference_configs()
        if self.config_data.default_preset not in inference_configs:
            self.config_data.default_preset = inference_configs[0]
        for message in lib.ensure_config_defaults(self.config_data):
            self.emit(message)
        self._load_config_to_ui()
        lib.save_config(self.config_data)
        lib.bootstrap_local_dirs(self.config_data)
        self.emit("Settings saved.")
        self._set_status_text("Settings saved")

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
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{line_count - LOG_MAX_LINES + 1}.0")
            self.log_text.see("end")
        self.after(100, self._drain_log_queue)

    def run_threaded(self, label: str, func, *, auth: bool = False) -> None:
        def worker() -> None:
            self._set_status_text(f"{label} running")
            try:
                if auth:
                    with lib.windows_auth_session(self.config_data):
                        func()
                else:
                    func()
                self._set_status_text(f"{label} finished")
            except Exception as exc:
                self.emit(f"ERROR: {exc}")
                self._set_status_text(f"{label} failed")
                self.after(0, lambda: messagebox.showerror(label, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def auth_input(self, prompt: str, secret: bool, default: str | None = None) -> str | None:
        done = threading.Event()
        result: dict[str, str | None] = {"value": None}

        def ask() -> None:
            result["value"] = simpledialog.askstring(
                "Great Lakes / Okta Authentication",
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
        self.emit("Connecting to Great Lakes. Authentication popups will show the terminal request when input is required.")

        def work() -> None:
            lib.bootstrap_local_dirs(self.config_data)
            local_gl_sync = Path(__file__).resolve().parent
            tasks_root = f"{self.config_data.gl_scratch_dir.rstrip('/')}/tasks"
            if sys.platform == "win32":
                lib.bootstrap_gl_sync_single_ssh(
                    self.config_data,
                    local_gl_sync,
                    tasks_root,
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
            else:
                remote_gl_sync = lib.upload_gl_sync(
                    self.config_data,
                    local_gl_sync,
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
                install_script = sh_quote(f"{remote_gl_sync}/install.sh")
                lib.ssh(
                    self.config_data,
                    (
                        "echo '[bootstrap  40%] Preparing Great Lakes task root on scratch.' && "
                        f"mkdir -p {sh_quote(tasks_root)} && "
                        "echo '[bootstrap  55%] Marking remote scripts executable.' && "
                        f"chmod +x {sh_quote(remote_gl_sync)}/*.sh && "
                        "echo '[bootstrap  70%] Checking Great Lakes SLEAP environment.' && "
                        f"if {install_script} --check; then "
                        "echo 'GL SLEAP environment already exists; skipping install.'; "
                        "else "
                        "echo 'GL SLEAP environment check failed; starting full install.'; "
                        f"{install_script}; "
                        "fi"
                    ),
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
            self.emit("GL SSH, gl_sync upload, environment check, and task root are ready.")

        self.run_threaded("Login GL", work, auth=True)

    def show_gl_tasks(self) -> None:
        self.save_settings()

        def work() -> None:
            tasks_root = f"{self.config_data.gl_scratch_dir.rstrip('/')}/tasks"
            result = lib.ssh(
                self.config_data,
                f"find {sh_quote(tasks_root)} -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort || true",
                emit=self.emit,
                input_callback=self.auth_input,
            )
            tasks = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if tasks:
                message = "GL tasks:\n\n" + "\n".join(tasks)
            else:
                message = f"No GL task folders found under:\n{tasks_root}"
            self.emit(message)
            self.after(0, lambda: messagebox.showinfo("Great Lakes Tasks", message))

        self.run_threaded("Show GL Tasks", work, auth=True)

    def show_slurm_jobs(self) -> None:
        self.save_settings()

        def work() -> None:
            result = lib.ssh(
                self.config_data,
                f"squeue -u {sh_quote(self.config_data.gl_user)} -o '%.18i %.9P %.40j %.8u %.2t %.10M %.6D %R'",
                emit=self.emit,
                input_callback=self.auth_input,
            )
            lines = [line.rstrip() for line in result.stdout.splitlines() if line.strip()]
            if lines:
                message = "Slurm jobs:\n\n" + "\n".join(lines)
            else:
                message = "No active Slurm jobs found for this GL user."
            self.emit(message)
            self.after(0, lambda: messagebox.showinfo("Slurm Jobs", message))

        self.run_threaded("Show Slurm Jobs", work, auth=True)

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
        label_files = sorted(labels_dir.glob("*.slp"), key=lambda path: path.stat().st_mtime, reverse=True)
        target_file = label_files[0] if label_files else None
        try:
            if target_file:
                subprocess.Popen([cmd, str(target_file)], env=lib.sleap_launch_env())
            else:
                subprocess.Popen([cmd], env=lib.sleap_launch_env())
        except FileNotFoundError as exc:
            messagebox.showerror("Open SLEAP", f"Could not launch SLEAP command: {cmd}\n{exc}")
            return
        self.emit(f"Opened SLEAP for {target_file or labels_dir}")
        messagebox.showinfo("Export Training Package", "After labeling, export the training package zip into this task's training_package folder.")

    def review_predictions(self) -> None:
        self.save_settings()
        initial_dir = self.config_data.local_project_path / "tasks"
        path = filedialog.askopenfilename(
            title="Select prediction .slp to review",
            initialdir=str(initial_dir if initial_dir.exists() else self.config_data.local_project_path),
            filetypes=[("SLEAP files", "*.slp"), ("All files", "*.*")],
        )
        cmd = self.config_data.sleap_label_cmd or lib.default_sleap_command()
        try:
            if path:
                subprocess.Popen([cmd, path], env=lib.sleap_launch_env())
                self.emit(f"Opened prediction for review: {path}")
            else:
                subprocess.Popen([cmd], env=lib.sleap_launch_env())
                self.emit("Opened SLEAP for review.")
        except FileNotFoundError as exc:
            messagebox.showerror("Review Predictions", f"Could not launch SLEAP command: {cmd}\n{exc}")
            return

    def train(self) -> None:
        self.save_settings()
        selection = TrainingPackageDialog(self, self.config_data).show()
        if selection is None:
            return
        task, zip_file, run_name = selection

        def work() -> None:
            remote_root = lib.remote_task_dir(self.config_data, task)
            if sys.platform == "win32":
                result = lib.submit_train_single_ssh(
                    self.config_data,
                    remote_root,
                    zip_file,
                    run_name,
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
            else:
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

        self.run_threaded("Train", work, auth=True)

    def download_model(self) -> None:
        self.save_settings()
        selection = ModelSelectionDialog(self, self.config_data, title="Select Model to Download").show()
        if selection is None:
            return
        task, run_name = selection

        def work() -> None:
            local_dir = lib.ensure_task(self.config_data, task) / "models" / run_name
            local_dir.mkdir(parents=True, exist_ok=True)
            remote_dir = f"{lib.remote_task_dir(self.config_data, task)}/models/{run_name}"
            if sys.platform == "win32":
                lib.download_remote_path_tar(
                    self.config_data,
                    remote_dir,
                    local_dir.parent,
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
            else:
                lib.sftp_batch(
                    self.config_data,
                    [f"get -r {sftp_quote(remote_dir)} {sftp_quote(local_dir.parent)}"],
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
            lib.mark_download(self.config_data, "model", {"task": task, "run_name": run_name, "path": str(local_dir)})
            self.after(0, self.refresh_history)

        self.run_threaded("Download Model", work, auth=True)

    def predict(self) -> None:
        self.save_settings()
        selection = ModelSelectionDialog(self, self.config_data, title="Select Model for Prediction").show()
        if selection is None:
            return
        task, model = selection
        videos = filedialog.askopenfilenames(title="Select videos")
        if not videos:
            return
        preset = PresetSelectionDialog(self, self.config_data.default_preset).show()
        if not preset:
            return
        self.config_data.default_preset = preset
        self.vars["default_preset"].set(preset)
        lib.save_config(self.config_data)

        def work() -> None:
            remote_root = lib.remote_task_dir(self.config_data, task)
            video_paths = [Path(video_name) for video_name in videos]
            local_model_dir = lib.local_model_dir(self.config_data, task, model)
            job_ids: list[str] = []
            if sys.platform == "win32":
                result = lib.submit_predict_existing_remote_single_ssh(
                    self.config_data,
                    remote_root,
                    video_paths,
                    model,
                    preset,
                    emit=self.emit,
                    input_callback=self.auth_input,
                )
                if result.returncode == 0:
                    job_ids = parse_job_ids(result.stdout)
                else:
                    missing_video_names = set(lib.predict_upload_video_names(result.stdout))
                    model_to_upload = None
                    if lib.predict_needs_model_upload(result.stdout):
                        if local_model_dir is None:
                            raise FileNotFoundError(f"Model not found on GL and local model folder is missing: tasks/{task}/models/{model}")
                        self.emit(f"GL is missing model; bundling local model for upload: {local_model_dir}")
                        model_to_upload = local_model_dir
                    videos_to_upload = [video for video in video_paths if video.name in missing_video_names]
                    for video in videos_to_upload:
                        self.emit(f"GL is missing or has a different-size video; bundling for upload: {video.name}")
                    if result.returncode != 42 and not missing_video_names and model_to_upload is None:
                        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
                    result = lib.submit_predict_single_ssh(
                        self.config_data,
                        remote_root,
                        video_paths,
                        videos_to_upload,
                        model,
                        preset,
                        model_to_upload,
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                    job_ids = parse_job_ids(result.stdout)
            else:
                lib.ssh(self.config_data, f"mkdir -p {remote_root}/videos {remote_root}/exports", emit=self.emit, input_callback=self.auth_input)
                remote_model = f"{remote_root}/models/{model}"
                model_check = lib.ssh(
                    self.config_data,
                    f"test -e {sh_quote(remote_model)}",
                    emit=self.emit,
                    check=False,
                    input_callback=self.auth_input,
                )
                if model_check.returncode:
                    if local_model_dir is None:
                        raise FileNotFoundError(f"Model not found on GL and local model folder is missing: tasks/{task}/models/{model}")
                    self.emit(f"Uploading missing model to GL: {model}")
                    lib.ssh(self.config_data, f"mkdir -p {remote_root}/models", emit=self.emit, input_callback=self.auth_input)
                    lib.sftp_batch(
                        self.config_data,
                        [f"put -r {sftp_quote(local_model_dir)} {sftp_quote(remote_root + '/models')}"],
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                for video in video_paths:
                    remote_video = f"{remote_root}/videos/{video.name}"
                    remote_size = lib.remote_file_size(
                        self.config_data,
                        remote_video,
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                    if remote_size == video.stat().st_size:
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
                    job_ids.append(parse_job_id(result.stdout))
            for index, video in enumerate(video_paths):
                lib.append_job(
                    self.config_data,
                    {
                        "type": "predict",
                        "task": task,
                        "model": model,
                        "video": video.name,
                        "preset": preset,
                        "job_id": job_ids[index] if index < len(job_ids) else "",
                        "expected_output": f"exports/{video.stem}.predicted.slp",
                    },
                )
            self.after(0, self.refresh_history)

        self.run_threaded("Predict", work, auth=True)

    def download_predictions(self) -> None:
        self.save_settings()
        selections = PredictionSelectionDialog(self, self.config_data, title="Select Predictions to Download").show()
        if not selections:
            return

        def work() -> None:
            downloaded = 0
            skipped = 0
            for task, remote_rel, file_name in selections:
                root = lib.ensure_task(self.config_data, task)
                local_exports = root / "exports"
                local_file = local_exports / file_name
                if local_file.exists():
                    skipped += 1
                    self.emit(f"skip download: {local_file} already exists")
                    continue
                remote_file = f"{lib.remote_task_dir(self.config_data, task)}/{remote_rel}"
                if sys.platform == "win32":
                    lib.download_remote_path_tar(
                        self.config_data,
                        remote_file,
                        local_exports,
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                else:
                    lib.sftp_batch(
                        self.config_data,
                        [f"get {sftp_quote(remote_file)} {sftp_quote(local_file)}"],
                        emit=self.emit,
                        input_callback=self.auth_input,
                    )
                lib.mark_download(
                    self.config_data,
                    "prediction",
                    {"task": task, "file": local_file.name, "remote_rel": remote_rel, "path": str(local_file)},
                )
                downloaded += 1
            self.emit(f"Prediction download complete: {downloaded} downloaded, {skipped} skipped.")
            self.after(0, self.refresh_history)

        self.run_threaded("Download Predictions", work, auth=True)


class PresetSelectionDialog:
    def __init__(self, parent: PipelineApp, initial: str) -> None:
        self.parent = parent
        self.result: str | None = None
        self.window: tk.Toplevel | None = None
        self.options = lib.list_inference_configs()
        self.preset_var = tk.StringVar(value=initial if initial in self.options else self.options[0])

    def show(self) -> str | None:
        self.window = tk.Toplevel(self.parent)
        self.window.title("Select Predict Config")
        self.window.transient(self.parent)
        self.window.grab_set()
        self.window.columnconfigure(1, weight=1)

        ttk.Label(self.window, text="Predict config").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        combo = ttk.Combobox(self.window, textvariable=self.preset_var, values=self.options, state="readonly")
        combo.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 6))

        button_row = ttk.Frame(self.window)
        button_row.grid(row=1, column=0, columnspan=2, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(button_row, text="Cancel", command=self.window.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(button_row, text="Select", command=self._confirm).grid(row=0, column=1)

        combo.focus_set()
        self.window.wait_window()
        return self.result

    def _confirm(self) -> None:
        if self.window is None:
            return
        preset = self.preset_var.get()
        if preset not in self.options:
            messagebox.showerror("Predict Config", "Select a predict config.", parent=self.window)
            return
        self.result = preset
        self.window.destroy()


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
            packages[task] = zips
        return packages

    def show(self) -> tuple[str, Path, str] | None:
        if not self.packages_by_task:
            task_name = simpledialog.askstring("Train", "Task name", parent=self.parent)
            if not task_name:
                return None
            self.packages_by_task[lib.safe_task_name(task_name)] = []

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
        ttk.Button(self.window, text="Browse", command=self._browse_package).grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=6)

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
        package_names = [self._package_label(task, path) for path in self.packages_by_task.get(task, [])]
        if self.package_combo is None:
            return
        self.package_combo.configure(values=package_names)
        if package_names:
            self.package_var.set(package_names[0])
        else:
            self.package_var.set("")
        self._sync_run_name()

    def _package_label(self, task: str, path: Path) -> str:
        try:
            return path.relative_to(lib.task_root(self.config, task)).as_posix()
        except ValueError:
            return str(path)

    def _browse_package(self) -> None:
        if self.window is None:
            return
        task = self.task_var.get()
        initial_dir = lib.task_root(self.config, task) if task else self.config.local_project_path
        path = filedialog.askopenfilename(
            title="Select training package zip",
            initialdir=str(initial_dir if initial_dir.exists() else self.config.local_project_path),
            filetypes=[("Training packages", "*.zip"), ("All files", "*.*")],
            parent=self.window,
        )
        if not path:
            return
        zip_file = Path(path)
        task_root = self.config.local_project_path / "tasks"
        matched_task = ""
        try:
            rel = zip_file.relative_to(task_root)
            if rel.parts:
                matched_task = lib.safe_task_name(rel.parts[0])
        except ValueError:
            matched_task = ""
        if matched_task:
            task = matched_task
        elif not task:
            task = lib.safe_task_name(zip_file.parent.name)
        self.packages_by_task.setdefault(task, [])
        if zip_file not in self.packages_by_task[task]:
            self.packages_by_task[task].insert(0, zip_file)
        self.task_var.set(task)
        self._sync_packages()
        self.package_var.set(self._package_label(task, zip_file))

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
        zip_file = next((path for path in self.packages_by_task.get(task, []) if self._package_label(task, path) == package_name), None)
        if zip_file is None:
            messagebox.showerror("Train", "Select a training package.", parent=self.window)
            return
        self.result = (task, zip_file, run_name)
        self.window.destroy()


class ModelSelectionDialog:
    def __init__(self, parent: PipelineApp, config: lib.PipelineConfig, title: str) -> None:
        self.parent = parent
        self.config = config
        self.title = title
        self.result: tuple[str, str] | None = None
        self.window: tk.Toplevel | None = None
        self.task_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self.model_combo: ttk.Combobox | None = None
        self.refs_by_task = self._load_refs()

    def _load_refs(self) -> dict[str, list[dict]]:
        refs_by_task: dict[str, list[dict]] = {}
        for ref in lib.list_model_refs(self.config):
            refs_by_task.setdefault(ref["task"], []).append(ref)
        return refs_by_task

    def show(self) -> tuple[str, str] | None:
        if not self.refs_by_task:
            messagebox.showwarning(
                self.title,
                "No model runs found. Submit a training job first, or download a model into tasks/{task}/models/.",
                parent=self.parent,
            )
            return None

        self.window = tk.Toplevel(self.parent)
        self.window.title(self.title)
        self.window.transient(self.parent)
        self.window.grab_set()
        self.window.columnconfigure(1, weight=1)

        ttk.Label(self.window, text="Task").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        task_combo = ttk.Combobox(self.window, textvariable=self.task_var, values=list(self.refs_by_task), state="readonly")
        task_combo.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 6))

        ttk.Label(self.window, text="Model / run").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        self.model_combo = ttk.Combobox(self.window, textvariable=self.model_var, state="readonly")
        self.model_combo.grid(row=1, column=1, sticky="ew", padx=12, pady=6)

        ttk.Label(self.window, textvariable=self.detail_var, wraplength=520, foreground="#555").grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=6
        )

        button_row = ttk.Frame(self.window)
        button_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(button_row, text="Cancel", command=self.window.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(button_row, text="Select", command=self._confirm).grid(row=0, column=1)

        task_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_models())
        self.model_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_detail())
        first_task = next(iter(self.refs_by_task))
        self.task_var.set(first_task)
        self._sync_models()

        self.window.wait_window()
        return self.result

    def _sync_models(self) -> None:
        if self.model_combo is None:
            return
        refs = self.refs_by_task.get(self.task_var.get(), [])
        self.model_combo.configure(values=[ref["model"] for ref in refs])
        self.model_var.set(refs[0]["model"] if refs else "")
        self._sync_detail()

    def _sync_detail(self) -> None:
        ref = self._selected_ref()
        if not ref:
            self.detail_var.set("")
            return
        parts = [f"Source: {ref.get('source', '')}"]
        if ref.get("time"):
            parts.append(f"Time: {ref['time']}")
        if ref.get("job_id"):
            parts.append(f"Job ID: {ref['job_id']}")
        if ref.get("path"):
            parts.append(f"Path: {ref['path']}")
        self.detail_var.set(" | ".join(parts))

    def _selected_ref(self) -> dict | None:
        task = self.task_var.get()
        model = self.model_var.get()
        return next((ref for ref in self.refs_by_task.get(task, []) if ref["model"] == model), None)

    def _confirm(self) -> None:
        if self.window is None:
            return
        ref = self._selected_ref()
        if not ref:
            messagebox.showerror(self.title, "Select a model/run.", parent=self.window)
            return
        self.result = (lib.safe_task_name(ref["task"]), lib.safe_task_name(ref["model"]))
        self.window.destroy()


class PredictionSelectionDialog:
    def __init__(self, parent: PipelineApp, config: lib.PipelineConfig, title: str) -> None:
        self.parent = parent
        self.config = config
        self.title = title
        self.result: list[tuple[str, str, str]] | None = None
        self.window: tk.Toplevel | None = None
        self.task_var = tk.StringVar()
        self.manual_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self.file_listbox: tk.Listbox | None = None
        self.refs_by_task = self._load_refs()

    def _load_refs(self) -> dict[str, list[dict]]:
        refs_by_task: dict[str, list[dict]] = {}
        for task in lib.list_tasks(self.config):
            refs_by_task.setdefault(task, [])
        for ref in lib.list_prediction_refs(self.config):
            refs_by_task.setdefault(ref["task"], []).append(ref)
        return refs_by_task

    def show(self) -> list[tuple[str, str, str]] | None:
        if not self.refs_by_task:
            task_name = simpledialog.askstring(self.title, "Task name", parent=self.parent)
            if not task_name:
                return None
            self.refs_by_task[lib.safe_task_name(task_name)] = []

        self.window = tk.Toplevel(self.parent)
        self.window.title(self.title)
        self.window.transient(self.parent)
        self.window.grab_set()
        self.window.columnconfigure(1, weight=1)

        ttk.Label(self.window, text="Task").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        task_combo = ttk.Combobox(self.window, textvariable=self.task_var, values=list(self.refs_by_task), state="readonly")
        task_combo.grid(row=0, column=1, sticky="ew", padx=12, pady=(12, 6))

        ttk.Label(self.window, text="Predictions").grid(row=1, column=0, sticky="nw", padx=12, pady=6)
        file_frame = ttk.Frame(self.window)
        file_frame.grid(row=1, column=1, sticky="nsew", padx=12, pady=6)
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self.file_listbox = tk.Listbox(file_frame, selectmode="extended", height=8, exportselection=False)
        file_scroll = ttk.Scrollbar(file_frame, orient="vertical", command=self.file_listbox.yview)
        self.file_listbox.configure(yscrollcommand=file_scroll.set)
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        file_scroll.grid(row=0, column=1, sticky="ns")

        ttk.Label(self.window, textvariable=self.detail_var, wraplength=560, foreground="#555").grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=6
        )

        ttk.Label(self.window, text="Extra remote paths").grid(row=3, column=0, sticky="w", padx=12, pady=6)
        ttk.Entry(self.window, textvariable=self.manual_var).grid(row=3, column=1, sticky="ew", padx=12, pady=6)
        ttk.Label(
            self.window,
            text="Optional. Separate multiple paths with commas, spaces, or new lines. Example: exports/video.predicted.slp",
            wraplength=560,
            foreground="#555",
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

        button_row = ttk.Frame(self.window)
        button_row.grid(row=5, column=0, columnspan=2, sticky="e", padx=12, pady=(8, 12))
        ttk.Button(button_row, text="Select All", command=self._select_all).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(button_row, text="Cancel", command=self.window.destroy).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(button_row, text="Download", command=self._confirm).grid(row=0, column=2)

        task_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_files())
        self.file_listbox.bind("<<ListboxSelect>>", lambda _event: self._sync_detail())
        first_task = next(iter(self.refs_by_task))
        self.task_var.set(first_task)
        self._sync_files()

        self.window.wait_window()
        return self.result

    def _sync_files(self) -> None:
        if self.file_listbox is None:
            return
        refs = self.refs_by_task.get(self.task_var.get(), [])
        self.file_listbox.delete(0, "end")
        for ref in refs:
            self.file_listbox.insert("end", self._file_label(ref))
        if refs:
            self.file_listbox.selection_set(0)
        self._sync_detail()

    def _file_label(self, ref: dict) -> str:
        remote_rel = ref.get("remote_rel") or ref.get("file", "")
        if ref.get("downloaded"):
            status = "Downloaded"
        elif ref.get("local_exists"):
            status = "Local"
        else:
            status = "Remote"
        return f"[{status}] {remote_rel}"

    def _plain_file_value(self, value: str) -> str:
        value = value.strip()
        if value.startswith("[") and "] " in value:
            return value.split("] ", 1)[1].strip()
        return value

    def _sync_detail(self) -> None:
        refs = self._selected_refs()
        if not refs:
            self.detail_var.set("Select known prediction files, or type remote paths below.")
            return
        if len(refs) > 1:
            self.detail_var.set(f"{len(refs)} prediction files selected.")
            return
        ref = refs[0]
        if ref.get("downloaded"):
            status = "Downloaded"
        elif ref.get("local_exists"):
            status = "Already local"
        else:
            status = "Not downloaded"
        parts = [f"Status: {status}", f"Source: {ref.get('source', '')}"]
        if ref.get("time"):
            parts.append(f"Time: {ref['time']}")
        if ref.get("job_id"):
            parts.append(f"Job ID: {ref['job_id']}")
        if ref.get("model"):
            parts.append(f"Model: {ref['model']}")
        if ref.get("video"):
            parts.append(f"Video: {ref['video']}")
        if ref.get("path"):
            parts.append(f"Path: {ref['path']}")
        self.detail_var.set(" | ".join(parts))

    def _selected_refs(self) -> list[dict]:
        if self.file_listbox is None:
            return []
        task = self.task_var.get()
        refs = self.refs_by_task.get(task, [])
        return [refs[index] for index in self.file_listbox.curselection() if index < len(refs)]

    def _select_all(self) -> None:
        if self.file_listbox is None:
            return
        self.file_listbox.selection_set(0, "end")
        self._sync_detail()

    def _manual_selections(self) -> list[tuple[str, str, str]]:
        task = lib.safe_task_name(self.task_var.get())
        raw = self.manual_var.get().replace(",", " ").replace(";", " ").replace("\n", " ")
        selections: list[tuple[str, str, str]] = []
        for value in raw.split():
            remote_rel = self._plain_file_value(value).replace("\\", "/")
            if not remote_rel:
                continue
            if "/" not in remote_rel:
                remote_rel = f"exports/{remote_rel}"
            selections.append((task, remote_rel, Path(remote_rel).name))
        return selections

    def _confirm(self) -> None:
        if self.window is None:
            return
        refs = self._selected_refs()
        selections = [
            (lib.safe_task_name(ref["task"]), ref.get("remote_rel", f"exports/{ref['file']}"), ref["file"])
            for ref in refs
        ]
        selections.extend(self._manual_selections())
        if not selections:
            messagebox.showerror(self.title, "Select one or more prediction outputs.", parent=self.window)
            return
        self.result = selections
        self.window.destroy()


def parse_job_id(output: str) -> str:
    ids = parse_job_ids(output)
    return ids[0] if ids else ""


def parse_job_ids(output: str) -> list[str]:
    job_ids: list[str] = []
    for token in output.replace(".", " ").split():
        if token.isdigit() and len(token) >= 5:
            job_ids.append(token)
    return job_ids


def sh_quote(value: str | os.PathLike[str]) -> str:
    import shlex

    return shlex.quote(str(value))


def sftp_quote(value: str | os.PathLike[str]) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


if __name__ == "__main__":
    PipelineApp().mainloop()

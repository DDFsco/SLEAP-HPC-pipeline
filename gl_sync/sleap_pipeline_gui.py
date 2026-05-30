from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
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
        self.after(100, self._drain_log_queue)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")

        self.pipeline_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        self.log_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.pipeline_tab, text="Pipeline")
        notebook.add(self.settings_tab, text="Settings")
        notebook.add(self.log_tab, text="Logs")

        self._build_pipeline_tab()
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
        lib.save_config(self.config_data)
        lib.bootstrap_local_dirs(self.config_data)
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

    def login_gl(self) -> None:
        self.save_settings()

        def work() -> None:
            lib.bootstrap_local_dirs(self.config_data)
            if not lib.ssh_check(self.config_data, emit=self.emit):
                raise RuntimeError("SSH check failed. Configure SSH keys for Great Lakes first.")
            remote_tasks = f"mkdir -p {lib.remote_task_dir(self.config_data, '_bootstrap_check').rsplit('/', 1)[0]}"
            lib.ssh(self.config_data, remote_tasks, emit=self.emit)
            self.emit("GL SSH and task root are reachable.")
            self.emit("Note: remote install checks require gl_sync/install.sh to exist on GL.")

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
        zips = lib.list_training_zips(self.config_data)
        if not zips:
            messagebox.showwarning("Train", "No training zip found under tasks/*/training_package/.")
            return
        zip_path = filedialog.askopenfilename(
            title="Select training package",
            initialdir=str(zips[0].parent),
            filetypes=[("Training package", "*.zip"), ("All files", "*.*")],
        )
        if not zip_path:
            return
        zip_file = Path(zip_path)
        task = zip_file.parents[1].name
        run_name = simpledialog.askstring("Run name", "Run name", initialvalue=f"{task}_v001", parent=self)
        if not run_name:
            return

        def work() -> None:
            remote_root = lib.remote_task_dir(self.config_data, task)
            lib.ssh(self.config_data, f"mkdir -p {remote_root}/training_package", emit=self.emit)
            lib.sftp_batch(
                self.config_data,
                [f"put {sftp_quote(zip_file)} {sftp_quote(remote_root + '/training_package/' + zip_file.name)}"],
                emit=self.emit,
            )
            remote_cmd = (
                f"SLEAP_SCRATCH_DIR={sh_quote(remote_root)} "
                f"bash {self.config_data.gl_sync_remote}/train.sh {sh_quote(zip_file.name)} {sh_quote(run_name)}"
            )
            result = lib.ssh(self.config_data, remote_cmd, emit=self.emit)
            job_id = parse_job_id(result.stdout)
            lib.append_job(self.config_data, {"type": "train", "task": task, "run_name": run_name, "job_id": job_id})

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
            lib.sftp_batch(self.config_data, [f"get -r {sftp_quote(remote_dir)} {sftp_quote(local_dir.parent)}"], emit=self.emit)
            lib.mark_download(self.config_data, "model", {"task": task, "run_name": run_name, "path": str(local_dir)})

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
            lib.ssh(self.config_data, f"mkdir -p {remote_root}/videos {remote_root}/exports", emit=self.emit)
            for video_name in videos:
                video = Path(video_name)
                remote_video = f"{remote_root}/videos/{video.name}"
                check = lib.ssh(self.config_data, f"test -f {sh_quote(remote_video)} && stat -c %s {sh_quote(remote_video)} || echo missing", emit=self.emit, check=False)
                if str(video.stat().st_size) in check.stdout.split():
                    self.emit(f"skip upload: {video.name} (same size on GL)")
                else:
                    lib.sftp_batch(self.config_data, [f"put {sftp_quote(video)} {sftp_quote(remote_video)}"], emit=self.emit)
                remote_cmd = (
                    f"SLEAP_SCRATCH_DIR={sh_quote(remote_root)} "
                    f"bash {self.config_data.gl_sync_remote}/predict.sh --preset {sh_quote(preset)} "
                    f"videos/{sh_quote(video.name)} models/{sh_quote(model)}"
                )
                result = lib.ssh(self.config_data, remote_cmd, emit=self.emit)
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
            listing = lib.ssh(self.config_data, f"ls {remote_exports}/*.predicted.slp 2>/dev/null || true", emit=self.emit)
            for remote_file in [line.strip() for line in listing.stdout.splitlines() if line.strip().endswith(".slp")]:
                local_file = local_exports / Path(remote_file).name
                if local_file.exists():
                    continue
                lib.sftp_batch(self.config_data, [f"get {sftp_quote(remote_file)} {sftp_quote(local_file)}"], emit=self.emit)
                lib.mark_download(self.config_data, "prediction", {"task": task, "file": local_file.name, "path": str(local_file)})

        self.run_threaded("Download Predictions", work)


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

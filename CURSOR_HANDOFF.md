# Cursor Handoff: SLEAP Local GUI Workflow

## Current State

The old Cursor project path was:

`/Users/ddfsco/Desktop/UMICH/Rothschild Lab/Great-Lakes`

That directory is unstable from command-line access: `stat` works, but `ls`, `find`, `sed`, and even `mv` can hang. Do not continue active development there until the filesystem issue is resolved.

Use this stable local workspace instead:

`/Users/ddfsco/Projects/Great-Lakes`

## Goal

Land the "SLEAP Local GUI Workflow" as a real, runnable local GUI wrapper around the existing Great Lakes SLEAP pipeline.

Target user: lab members with little or no Great Lakes/SLEAP experience.

Main workflow:

1. Configure Great Lakes account.
2. Bootstrap local and remote SLEAP environments.
3. Create/select a task.
4. Open local SLEAP for labeling.
5. Export training package.
6. Upload to Great Lakes and submit Slurm training.
7. Download trained model.
8. Upload videos and submit prediction.
9. Download prediction `.slp` files.

## Files To Implement

Create these files under `gl_sync/`:

- `install_local_gui.sh`
- `install_local_gui.ps1`
- `pipeline_lib.py`
- `sleap_pipeline_gui.py`
- `README_LOCAL_GUI.md`

The repository now includes these Great Lakes scripts and the GUI bootstrap uploads them to `~/gl_sync`:

- `install.sh`
- `train.sh`
- `predict.sh`
- `sleap_common.sh`

If the old Desktop copy becomes readable later, compare it against these scripts before replacing anything.

## Design Constraints

- Keep GPU training and prediction on Great Lakes only.
- Local environment is for SLEAP GUI labeling and exporting training packages.
- Prefer SLEAP 1.6 unified CLI (`sleap`) and keep `sleap-label` as a fallback.
- Use per-task isolation through `SLEAP_SCRATCH_DIR`.
- Keep task layout mirrored locally and remotely:

```text
tasks/{task_name}/
  labels/
  training_package/
  models/
  videos/
  exports/
```

## Known Design Risks To Fix

- The old plan says "tkinter GUI" but installs `PySide6`; decide explicitly whether the wrapper GUI is tkinter or Qt. A pragmatic path is tkinter for the wrapper, PySide6 only for SLEAP.
- Do not rely only on video filename for remote upload de-dupe; compare size at minimum.
- Do not make users manually type model names when job/log data already has `run_name`.
- Add job/status checks or clear messaging before model/prediction download.
- Avoid automatically running long remote installs without user confirmation and clear logs.

## Suggested Implementation Order

1. `pipeline_lib.py`: config, task paths, JSON log, subprocess wrapper, ssh/sftp helpers.
2. `install_local_gui.sh` and `.ps1`: create local env, install SLEAP, verify CLI/imports.
3. `sleap_pipeline_gui.py`: minimal working buttons and threaded logs.
4. `README_LOCAL_GUI.md`: zero-experience walkthrough.
5. Dry-run checks without requiring GL credentials.

## Coordination Note

Codex will implement and verify in this new path. Cursor should open:

`/Users/ddfsco/Projects/Great-Lakes`

and treat this file as the current handoff source.

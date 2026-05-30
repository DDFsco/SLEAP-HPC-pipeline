# SLEAP Local GUI + Great Lakes Workflow

This GUI helps lab members run a SLEAP workflow without manually typing SSH, SFTP, or Slurm commands.

The intended flow is:

1. Configure Great Lakes account details.
2. Check SSH access.
3. Create a task.
4. Open SLEAP locally for labeling.
5. Export a training package zip into the task folder.
6. Submit training on Great Lakes.
7. Download the trained model.
8. Upload videos and submit prediction on Great Lakes.
9. Download predicted `.slp` files.

## Project Layout

Each experiment is a task:

```text
tasks/{task_name}/
  labels/
  training_package/
  models/
  videos/
  exports/
```

The GUI mirrors the same task layout on Great Lakes under:

```text
{SLEAP_SCRATCH_DIR}/tasks/{task_name}/
```

## Install Local SLEAP GUI Environment

macOS/Linux:

```bash
bash gl_sync/install_local_gui.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File gl_sync/install_local_gui.ps1
```

The local environment is for SLEAP labeling and exporting training packages. GPU training and prediction stay on Great Lakes.

## Start the GUI

```bash
/Users/ddfsco/anaconda3/bin/python3.13 gl_sync/sleap_pipeline_gui.py
```

On this Mac, avoid `/usr/bin/python3` for the GUI. It is Xcode Python 3.9 and its Tk framework may fail. Use Python 3.11+ with working Tk, such as the Anaconda Python 3.13 shown above.

On first launch, open the Settings tab and fill:

- `GL uniqname`
- `SLURM account`
- `GL scratch dir`
- `Local project`
- `SLEAP command`

Then click `Save Settings`.

## Great Lakes Setup

The GUI expects SSH access to work:

```bash
ssh uniqname@greatlakes.arc-ts.umich.edu echo ok
```

It also expects the Great Lakes helper scripts to exist at:

```text
~/gl_sync/install.sh
~/gl_sync/train.sh
~/gl_sync/predict.sh
```

The GUI calls:

```bash
SLEAP_SCRATCH_DIR=/scratch/.../tasks/{task} bash ~/gl_sync/train.sh ...
SLEAP_SCRATCH_DIR=/scratch/.../tasks/{task} bash ~/gl_sync/predict.sh ...
```

## Full Workflow

1. Click `Login GL / Bootstrap`.
2. Click `Open SLEAP`.
3. Create or select a task.
4. Label in SLEAP.
5. Export the training package zip to:

```text
tasks/{task}/training_package/
```

6. Click `Train`, select the zip, and enter a run name.
7. Wait for the Great Lakes Slurm job to finish.
8. Click `Download Model` and enter the run name.
9. Click `Predict`, select videos, model, and preset.
10. Wait for prediction jobs to finish.
11. Click `Download Predictions`.

## Notes

- The GUI writes configuration to `~/.sleap_pipeline.json`.
- Job and download history is stored in `{local_project}/pipeline.log.json`.
- Video upload de-dupe compares remote file size before skipping an upload.
- This first version does not automatically poll Slurm job completion.

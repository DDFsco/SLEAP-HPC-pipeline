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

The installer lets `sleap[nn]==1.6.0` resolve its own compatible Qt/PySide dependencies. Do not pin an older `PySide6` on Python 3.13.

## Start the GUI

```bash
/Users/ddfsco/anaconda3/bin/python3.13 gl_sync/sleap_pipeline_gui.py
```

On this Mac, avoid `/usr/bin/python3` for the GUI. It is Xcode Python 3.9 and its Tk framework may fail. Use Python 3.11+ with working Tk, such as the Anaconda Python 3.13 shown above.

On first launch, open the Settings tab and fill:

- `GL uniqname`
- `SLURM account`
- `Local project`
- `SLEAP command`

Then click `Save Settings`.

If `GL scratch dir` is blank, the GUI defaults it to:

```text
/scratch/gid_root/gid0/{uniqname}/sleap_rat
```

During `Login GL / Bootstrap`, SSH/SFTP may ask for a password, Duo passcode, verification code, or host-key confirmation. The GUI watches the terminal session and opens a popup when one of these prompts appears. Enter the requested value and click OK; the GUI sends it back to the SSH/SFTP process.

After the first successful login, the GUI reuses an OpenSSH ControlMaster connection for 15 minutes. That should avoid repeated password/Duo prompts during the same bootstrap/train/predict sequence.

Use `Show GL Tasks` to list remote task folders stored under:

```text
{GL scratch dir}/tasks/
```

Use `Show Slurm Jobs` to check active Great Lakes jobs for the configured uniqname. It runs:

```bash
squeue -u {GL uniqname}
```

If Great Lakes rejects the login immediately with `Permission denied (publickey,keyboard-interactive)` and no popup appears, SSH is not offering an interactive prompt to the client. In that case, first confirm that normal Terminal login works:

```bash
ssh uniqname@greatlakes.arc-ts.umich.edu
```

## Great Lakes Setup

The GUI expects SSH access to work:

```bash
ssh uniqname@greatlakes.arc-ts.umich.edu echo ok
```

When you click `Login GL / Bootstrap`, the GUI uploads the local `gl_sync/` directory to:

```text
~/gl_sync/
```

Then it runs:

```bash
bash ~/gl_sync/install.sh --check
```

If the check fails, it automatically runs:

```bash
bash ~/gl_sync/install.sh
```

On Great Lakes, `install.sh` creates only the remote training/prediction environment:

```text
{SLEAP_SCRATCH_DIR}/env/sleap_env
```

For Great Lakes V100 GPUs, the remote installer pins PyTorch to CUDA 12.1 wheels:

```text
torch==2.5.1+cu121
torchvision==0.20.1+cu121
```

This avoids newer CUDA 13 PyTorch wheels that do not include kernels for V100 compute capability 7.0.

The SLEAP GUI environment is local-only and is installed by `install_local_gui.sh` or `install_local_gui.ps1`.

Training and prediction then call:

```bash
SLEAP_SCRATCH_DIR=/scratch/.../tasks/{task} bash ~/gl_sync/train.sh ...
SLEAP_SCRATCH_DIR=/scratch/.../tasks/{task} bash ~/gl_sync/predict.sh ...
```

The scripts uploaded by the GUI are:

- `gl_sync/install.sh`
- `gl_sync/train.sh`
- `gl_sync/predict.sh`
- `gl_sync/sleap_common.sh`

## Full Workflow

1. Click `Login GL / Bootstrap` in the Great Lakes Controls card.
2. Click `Open SLEAP` in Step 1.
3. Create or select a task.
4. Label in SLEAP.
5. Export the training package zip to:

```text
tasks/{task}/training_package/
```

6. Click `Train` in Step 2, select a task and training package zip from the popup, and confirm the generated run name.
7. Wait for the Great Lakes Slurm job to finish.
8. Click `Download Model` in Step 4 and select the trained model/run from the popup.
9. Click `Predict` in Step 3, select videos, model, and preset.
10. Wait for prediction jobs to finish.
11. Click `Download Predictions` in Step 4 and select the prediction output from the popup.
12. Click `Review Predictions` in Step 5 to open a downloaded `.slp` file in SLEAP for correction.

## Notes

- The GUI writes configuration to `~/.sleap_pipeline.json`.
- Job and download history is stored in `{local_project}/pipeline.log.json`.
- The `History` tab records submitted training/prediction jobs, run names, package/video names, job IDs, and downloaded files for later model download or prediction reference.
- `Download Model` and `Predict` show model selection popups populated from training history and local `tasks/{task}/models/` folders, so users do not need to remember run names.
- `Download Predictions` shows a prediction selection popup populated from prediction history and local `tasks/{task}/exports/` files.
- Video upload de-dupe compares remote file size before skipping an upload.
- This first version does not automatically poll Slurm job completion.

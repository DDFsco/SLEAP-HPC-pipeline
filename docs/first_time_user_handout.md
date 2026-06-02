# SLEAP Pipeline Manager: First-Time User Handout

This handout walks you through the first full SLEAP workflow using the SLEAP Pipeline Manager and the University of Michigan Great Lakes cluster.

The app is designed so you do not need to type SSH, SFTP, Slurm, or Great Lakes setup commands by hand. You will label videos locally, then use Great Lakes for GPU training and prediction.

## What You Need Before Starting

- A working University of Michigan Great Lakes account.
- Your Great Lakes uniqname.
- Your Slurm account name.
- Access to Duo or any other Great Lakes login verification method.
- The SLEAP Pipeline Manager project folder on your computer.
- A local SLEAP GUI environment installed by the project installer.
- Video files you want to label or predict.

If Great Lakes login does not work in a normal terminal, fix that first:

```bash
ssh uniqname@greatlakes.arc-ts.umich.edu
```

## Start the App

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File run_gui.ps1
```

On macOS or Linux:

```bash
./run_gui.sh
```

If you are using the Windows executable, double-click:

```text
dist\SLEAP-Pipeline-Manager.exe
```

## First-Time Settings

Open the `Settings` tab and fill in:

- `GL uniqname`: your Great Lakes uniqname.
- `SLURM account`: the account used for GPU jobs.
- `GL host`: usually `greatlakes.arc-ts.umich.edu`.
- `GL scratch dir`: optional. If blank, the app uses `/scratch/gid_root/gid0/{uniqname}/sleap_rat`.
- `Local project`: the folder where local tasks, labels, videos, models, and predictions will be stored.
- `SLEAP command`: optional. Leave blank unless your local SLEAP launch command is custom.

Click `Save Settings`.

## Log Into Great Lakes

Click `Login GL / Bootstrap`.

The app will:

- log into Great Lakes,
- upload the current `gl_sync` scripts,
- create the remote task folders,
- check or install the remote SLEAP environment,
- prepare Great Lakes for training and prediction.

During login, Great Lakes may ask for a password, Duo approval, passcode, or host-key confirmation. The app will show a popup when it needs input.

On Windows, the app tries to ask for the Great Lakes password once per GUI session and reuse it for later actions. Duo prompts may still appear when Great Lakes requires verification.

## Task Folder Layout

Each experiment is stored as a task.

Local task folders look like this:

```text
tasks/{task_name}/
  labels/
  training_package/
  models/
  videos/
  exports/
```

Great Lakes mirrors the same task under:

```text
{GL scratch dir}/tasks/{task_name}/
```

Use clear task names such as:

```text
rat_241
mouse_day3_cameraA
baseline_test1
```

## Step 1: Label Videos Locally

Click `Open SLEAP`.

In SLEAP:

1. Open or create your labels project.
2. Add videos.
3. Label frames.
4. Save the label file in:

```text
tasks/{task_name}/labels/
```

You can create the task folder before labeling, or choose/create the task when exporting the training package.

## Step 2: Export a Training Package

After labeling in SLEAP, export a training job package zip.

Save it into:

```text
tasks/{task_name}/training_package/
```

The file should look similar to:

```text
labels.my_task.slp.training_job.zip
```

This zip contains the labels and training configuration that Great Lakes will use.

## Step 3: Train a Model on Great Lakes

In SLEAP Pipeline Manager, click `Train`.

In the popup:

1. Select the task.
2. Select the training package zip.
3. Confirm or edit the run name.
4. Click `Train`.

The app uploads the training package and submits a Slurm GPU job on Great Lakes.

If the training package references a local pretrained checkpoint, the app automatically bundles that checkpoint and rewrites the path so Great Lakes can read it.

Use `Show Slurm Jobs` to check whether the job is still running.

## Step 4: Download the Trained Model

When the training job finishes, click `Download Model`.

Select the task and model run. The model will download to:

```text
tasks/{task_name}/models/{run_name}/
```

This local model folder can also be used later for prediction. If Great Lakes is missing a selected model during prediction, the app can upload the local model automatically.

## Step 5: Run Prediction on Great Lakes

Click `Predict`.

In the popups:

1. Select the task.
2. Select the trained model.
3. Select one or more videos.
4. Select a prediction config.

The app uploads the selected videos and submits prediction jobs on Great Lakes.

Prediction configs come from:

```text
gl_sync/inference/
```

Common choices include:

- `default`
- `aggressive`
- `sensitive`

If the app says it is bundling or uploading a model, that means the selected model exists locally but is missing on Great Lakes. This is normal. Watch the upload progress in the log window.

## Step 6: Download Predictions

After prediction jobs finish, click `Download Predictions`.

Select the task and prediction output. Files download to:

```text
tasks/{task_name}/exports/
```

Predicted files usually end in:

```text
.slp
```

## Step 7: Review Predictions

Click `Review Predictions`.

Choose a downloaded `.slp` prediction file. The app opens it in SLEAP so you can inspect, correct, or continue labeling.

## What to Watch in the Log

Useful normal messages include:

```text
Settings saved.
GL SSH, gl_sync upload, environment check, and task root are ready.
Submitted batch job 12345678
Checking GL model: ...
Model already exists on GL: ...
GL is missing model; bundling local model for upload: ...
Upload progress: ...
Downloaded: ...
```

If you see `Submitted batch job`, the Great Lakes job was submitted successfully.

## Common Problems

### Permission denied during login

Error example:

```text
Permission denied (publickey,keyboard-interactive)
```

Try logging into Great Lakes from a normal terminal first:

```bash
ssh uniqname@greatlakes.arc-ts.umich.edu
```

If that fails, the issue is with the Great Lakes account, password, Duo, SSH setup, or access permissions.

### The app asks for verification more than once

Great Lakes controls authentication. The app reduces repeated prompts where possible, but Duo or verification prompts may still appear when Great Lakes requires them.

On Windows, keep the same GUI window open during the workflow so the temporary password cache can be reused.

### Training cannot find a checkpoint

If a training package references a local checkpoint, the current app automatically bundles it before upload. If this still fails, export a fresh training package and submit again.

### Prediction seems stuck after upload starts

Large videos and model folders can take time to transfer. Check the log for:

```text
Streaming upload:
Upload progress:
```

If progress is changing, the app is still working.

### A dropdown does not show the file you expect

Make sure the file is inside the correct task folder:

- training packages: `tasks/{task}/training_package/`
- local models: `tasks/{task}/models/`
- prediction outputs: `tasks/{task}/exports/`

Then reopen the popup or restart the app.

## Recommended First Test

For a first run, use a small test video and a small label set.

Do one complete cycle:

1. Label a few frames.
2. Export one training package.
3. Submit one training job.
4. Download the model.
5. Predict one short video.
6. Download and review one prediction file.

After this test works, move to larger datasets.

## Quick Workflow Checklist

- Save settings.
- Click `Login GL / Bootstrap`.
- Click `Open SLEAP`.
- Label frames locally.
- Export training package to `tasks/{task}/training_package/`.
- Click `Train`.
- Wait for the Slurm training job to finish.
- Click `Download Model`.
- Click `Predict`.
- Wait for the Slurm prediction job to finish.
- Click `Download Predictions`.
- Click `Review Predictions`.


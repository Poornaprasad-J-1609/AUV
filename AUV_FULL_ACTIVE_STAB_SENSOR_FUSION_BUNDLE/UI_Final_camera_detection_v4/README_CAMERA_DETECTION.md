# Camera Detection page

New sidebar entry (camera icon, below Docking) → **Camera Detection**.

## One-time setup (Windows)
Open Command Prompt in this folder, activate the same Python environment you use for `main.py`, then:

    pip install -r requirements_camera_detection.txt

Start the app as usual: `python main.py`

## GPU (RTX) — higher FPS
Double-click **install_gpu_support.bat** once (it installs the CUDA build of PyTorch and checks it works),
then restart the app. The Camera Detection header shows `Compute: <GPU name> · GPU FP16`, and the
TARGET panel shows COMPUTE = GPU. If the header turns orange, it tells you what is missing.
`Compute` in the DETECTION card can force CPU.

Speed-ups in this version (on GPU and CPU): video is decoded in a background thread while the
previous frame is being detected; frames larger than 1280 px (1080p / 4K) are downscaled once for
all processing; colour correction is only computed when it is needed; the model is warmed up for
every input size before the first frame.

## Sources
Video file · USB/built-in camera (index 0, 1, …) · Network stream (RTSP/HTTP/UDP URL) · Image · Image folder.
You can also drag a file or folder onto the video area.

## Controls
- START / PAUSE / STOP, STEP (one frame while paused), seek bar, speed 0.25×–4× or Max
- Playback: **Real-time** keeps video at true speed by skipping frames when the computer can't keep up;
  **Every frame** processes all frames. Recording always processes every frame.
- Confidence / Overlap / Input size apply live; while paused the current frame is re-processed.
  Keep Input size at 320 for the included model.
- TARGET panel: DOCK LOCKED after 5 consecutive detections, smoothed X/Y offset (−1…+1 from image centre),
  box area %, inference time, processing FPS.
- RECORD saves the annotated video (.mp4) to the folder shown; SNAPSHOT saves a PNG; EXPORT CSV saves the log.

## Nose camera (forward-facing)
CAMERA MOUNT card: horizontal field of view (from the camera datasheet), yaw / pitch misalignment,
**dock size** (measure the dock's largest outer dimension), and upside-down mounting.
From these the page reports **BEARING** (+ = dock to starboard), **ELEVATION** (+ = above) and an
estimated **RANGE** from the dock's apparent size (shown as "≤" when the dock is cut off by the frame
edge, because the range is then only an upper bound). These are shown on the video, in the TARGET
panel, and passed to the Sensor Fusion page.

## Air and underwater
**Environment = Auto** (default) works in both without switching. Every frame it measures the colour
cast and brightness: in air, frames go to the model untouched; underwater (red light missing →
blue/green cast) or in low light, colour & contrast correction is applied first. If nothing is found,
the other variant is tried as well, so a wrong guess never costs a detection. The TARGET panel shows
the detected **ENVIRONMENT**. Air / Underwater force a mode; No correction disables it.

Tested on a mixed clip (simulated): air 18/18, bright air 18/18, dim air 17/18 (6/18 without
correction), blue water 18/18, green water 18/18.

## If the dock is not detected
The model was trained on ~740 images of the dock in a dry, well-lit workshop. Footage that looks
different (underwater colour cast, murky/dark water, dock far away, new background) can drop
detection to zero. In order of effort:

1. Keep **Environment = Auto**, **Distant target search** and **Target tracking** ON and Input size
   on **Auto (model)**. (On a simulated underwater approach clip this took detection from 0 % to 100 %.)
2. Watch **RAW TOP SCORE**: the model's best guess even below the threshold.
   - Around 0.10–0.25 → lower Confidence a little.
   - Below 0.10 for long stretches → the scene is too unfamiliar; retrain (next step).
3. **Retrain on your own footage** (most effective):
   a. Play your video with **CAPTURE FRAMES FOR RETRAINING** on (every 10–15 frames). Frames are saved
      with pre-filled boxes from the current model in `training_capture_<date>` (inside the recording folder).
   b. Upload that folder to your Roboflow project, fix/add the boxes (missed docks especially), export
      in the same export format you used for the original dataset — or just correct the `.txt` files with any labelling tool.
   c. Double-click **train_camera_model.bat**, drag in your original dataset folder and the reviewed
      capture folder (one per line), press Enter on an empty line. On the RTX 3050 expect ~15–30 min.
      It adds synthetic underwater / low-light / distant copies automatically, fine-tunes at 640 px,
      then tests the OLD and NEW model separately on in-air and underwater images and **only installs
      the new model if it is not worse in air** (backing up the old one). Always include your original
      in-air dataset so the model keeps working in air. Restart the app; leave Input size on Auto.
   If you see "CUDA out of memory", run: `train_camera_model.bat --batch 8`

## Files added / changed
- `src/pages/camera_detection_page.py` (new page)
- `src/models/camera_detection.pt` (detection model)
- `src/widgets.py` (sidebar button + icon), `src/app.py` (page registration, clean shutdown),
  `src/pages/settings_page.py` (theme support), `main.py` (loads the compute library before Qt to avoid a
  Windows DLL error)
- `requirements_camera_detection.txt`
- `train_camera_model.py` / `train_camera_model.bat` (retraining on your GPU)
- `install_gpu_support.bat` (GPU setup)
- `src/pages/sensor_fusion_page.py` (new Sensor Fusion page); `src/app.py` feeds it every telemetry packet

Other modules can read the latest result from `window.camera_detection_state`
(`detected`, `locked`, `offset_x`, `offset_y`, `confidence`, `area_pct`, `timestamp`).


# Sensor Fusion page
Sidebar: the icon with four dots around a centre (below Camera Detection).

Four sensor cards — **1) GPS, 2) Magnetometer, 3) Sonar, 4) Camera Detection** — each with:
- **Use in fusion** (untick = monitor only: still shown and plotted, not used),
- live range / bearing / age and status (ACTIVE, MONITOR ONLY, STALE, OUT OF RANGE, NO DATA),
- **Active range** (the distance window in which the sensor is trusted), **Stale after**, and 1σ
  uncertainties.

Sensor details
- **GPS**: vehicle fix vs. docking-station coordinates ("USE DOCKING STATION FROM PLAN" copies them).
  Ignored below the minimum satellite count, or when telemetry reports depth > Max depth.
- **Magnetometer** (RM3100, dock EMF coil): press **CAPTURE BASELINE** away from the coil, then
  **CALIBRATE AT** a known distance. Range follows the 1/r³ coil law. Bearing from field direction is
  optional and off by default (coil fields curve). Starts *not* in fusion until you have tested it.
- **Sonar** (Ping360): target range/bearing; mount offset and minimum confidence.
- **Camera Detection**: bearing (+ range from dock size) from the nose camera; options for
  "only when DOCK LOCKED" and minimum confidence. START CAMERA / OPEN PAGE buttons.

Fusion control
- **Staged handover** (default, Trinetra plan): the first sensor in the priority list that is enabled,
  fresh and inside its active range is used — defaults: Magnetometer ≤ 3 m → Sonar ≤ 5 m →
  Camera ≤ 8 m → GPS. "Camera refines bearing" replaces the bearing whenever the camera sees the dock.
  A sensor without bearing (magnetometer) borrows the bearing of the next sensor that has one.
- **Weighted fusion (Kalman filter)**: blends all valid sensors by their uncertainty; Process noise
  and Outlier gate tune it.
- If no sensor gives range, a **bearing-only** steering cue is still given.
- **Simulated sensors (dry test)** runs a simulated approach with all four sensors so you can try
  settings without the vehicle (simulated test: ~0.1 m range / ~0.5° bearing error inside 5 m).

Output: range, relative and true bearing, 1σ uncertainty, sources, stage, and a LEFT / AHEAD / RIGHT
steering cue (dead-band adjustable). **LOG TO CSV** records every sensor and the fused result at 10 Hz
(MarineGS_Recordings/sensor_fusion). **Send fused target to vehicle** (off by default) transmits

    $FUSE,<valid 0/1>,<range m>,<relative bearing °>,<1σ m>,<sources G/M/S/C>,<stage>

at the chosen rate — the vehicle code must be updated to parse this sentence before enabling it.
Other modules can read `window.sensor_fusion_state`.

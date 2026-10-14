"""
Retrain the Camera Detection model on your GPU.

    python train_camera_model.py  <dataset folder> [<more dataset folders> ...]  [options]

A dataset folder can be:
  • a Roboflow export (train/ valid/ test/ subfolders with images/ + labels/), or
  • a training-capture folder from the Camera Detection page (images/ + labels/),
    AFTER you have reviewed/corrected the boxes (e.g. in Roboflow, exported in the same format as your dataset).

What it does:
  1. Merges all datasets (15 % of capture-style folders are held out for validation).
  2. Adds synthetic underwater (blue/green cast, haze), low-light and distance variants
     of every training image so the model stops depending on dry, bright, close-up scenes.
  3. Fine-tunes the current model (src/models/camera_detection.pt) at 640 px.
  4. Tests OLD vs NEW model separately on in-air images, real underwater images (if any)
     and synthetic underwater images.
  5. Installs the new model only if it is NOT worse in air (use --force to override),
     backing up the old one. Restart the app to use it.
"""
import argparse
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "src" / "models" / "camera_detection.pt"
WORK = ROOT / "training_workspace"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# --------------------------------------------------------------------- dataset
def pairs_in(img_dir):
    img_dir = Path(img_dir)
    lab_dir = img_dir.parent / "labels"
    out = []
    for f in sorted(img_dir.iterdir()):
        if f.suffix.lower() in IMG_EXTS:
            lab = lab_dir / (f.stem + ".txt")
            out.append((f, lab if lab.exists() else None))
    return out


def collect(folder):
    folder = Path(folder)
    train, val = [], []
    if (folder / "train" / "images").is_dir():                       # Roboflow layout
        train += pairs_in(folder / "train" / "images")
        for v in ("valid", "val", "test"):
            if (folder / v / "images").is_dir():
                val += pairs_in(folder / v / "images")
    elif (folder / "images").is_dir():                                # capture layout
        p = pairs_in(folder / "images")
        random.Random(0).shuffle(p)
        k = max(1, int(len(p) * 0.15))
        val += p[:k]
        train += p[k:]
    else:
        sys.exit(f"[x] Not a dataset folder (no train/images or images/): {folder}")
    return train, val


def underwater(img, rng, cv2, np):
    f = img.astype(np.float32)
    f[..., 2] *= rng.uniform(0.25, 0.6)                     # red absorbed first
    f[..., 1] *= rng.uniform(0.8, 1.0)
    haze = np.array([rng.uniform(70, 120), rng.uniform(60, 110), rng.uniform(0, 30)], np.float32)
    a = rng.uniform(0.15, 0.45)
    f = f * (1 - a) + haze * a
    if rng.random() < 0.5:
        k = rng.choice([3, 5])
        f = cv2.GaussianBlur(f, (k, k), 0)
    f += np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, rng.uniform(2, 8), f.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def lowlight(img, rng, cv2, np):
    f = img.astype(np.float32) * rng.uniform(0.2, 0.5)
    f += np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 4, f.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def shrink(img, labels, rng, cv2, np):
    """Place a scaled-down copy on a neutral canvas → simulates a distant dock."""
    h, w = img.shape[:2]
    s = rng.uniform(0.3, 0.6)
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    canvas = np.full_like(img, int(rng.uniform(60, 140)))
    ox = rng.randint(0, w - small.shape[1])
    oy = rng.randint(0, h - small.shape[0])
    canvas[oy:oy + small.shape[0], ox:ox + small.shape[1]] = small
    out = []
    for ln in labels:
        p = ln.split()
        if len(p) != 5:
            continue
        c, x, y, bw, bh = p[0], *map(float, p[1:])
        out.append(f"{c} {(x * small.shape[1] + ox) / w:.6f} {(y * small.shape[0] + oy) / h:.6f} "
                   f"{bw * s:.6f} {bh * s:.6f}")
    return canvas, out


def build_dataset(folders, synthetic):
    import cv2
    import numpy as np
    rng = random.Random(42)
    ds = WORK / f"dataset_{time.strftime('%Y%m%d_%H%M%S')}"
    for sp in ("train", "valid"):
        (ds / sp / "images").mkdir(parents=True, exist_ok=True)
        (ds / sp / "labels").mkdir(parents=True, exist_ok=True)
    names = None
    n = {"train": 0, "valid": 0, "synthetic": 0}
    for i, folder in enumerate(folders):
        tr, va = collect(folder)
        y = Path(folder) / "data.yaml"
        if names is None and y.exists():
            import yaml
            names = (yaml.safe_load(y.read_text()) or {}).get("names")
        for split, items in (("train", tr), ("valid", va)):
            for img, lab in items:
                stem = f"s{i}_{img.stem}"[:150]
                shutil.copy2(img, ds / split / "images" / f"{stem}{img.suffix.lower()}")
                lines = lab.read_text().strip().splitlines() if lab else []
                (ds / split / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
                n[split] += 1
                if split == "train" and synthetic:
                    im = cv2.imdecode(np.fromfile(str(img), np.uint8), cv2.IMREAD_COLOR)
                    if im is None:
                        continue
                    variants = [("uw", underwater(im, rng, cv2, np), lines)]
                    if rng.random() < 0.5:
                        variants.append(("dark", lowlight(im, rng, cv2, np), lines))
                    if lines and rng.random() < 0.5:
                        sm, sl = shrink(im, lines, rng, cv2, np)
                        variants.append(("far", underwater(sm, rng, cv2, np) if rng.random() < 0.5 else sm, sl))
                    for tag, vim, vl in variants:
                        vs = f"{stem}_{tag}"
                        cv2.imencode(".jpg", vim, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(
                            str(ds / "train" / "images" / f"{vs}.jpg"))
                        (ds / "train" / "labels" / f"{vs}.txt").write_text("\n".join(vl) + ("\n" if vl else ""))
                        n["synthetic"] += 1
    names = names or ["dock"]
    yml = ds / "data.yaml"
    yml.write_text(f"path: {ds.as_posix()}\ntrain: train/images\nval: valid/images\n"
                   f"nc: {len(names)}\nnames: {list(names)}\n")
    print(f"[i] Dataset: {n['train']} train + {n['synthetic']} synthetic, {n['valid']} validation images")
    if n["valid"] == 0:
        sys.exit("[x] No validation images found.")

    # Per-environment test sets: real in-air, real underwater, synthetic underwater
    sets = {"air": [], "underwater_real": [], "underwater_synthetic": []}
    for img in sorted((ds / "valid" / "images").iterdir()):
        im = cv2.imdecode(np.fromfile(str(img), np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            continue
        lab = ds / "valid" / "labels" / f"{img.stem}.txt"
        small = cv2.resize(im, (64, 36), interpolation=cv2.INTER_AREA).reshape(-1, 3)
        b, g, r = small.mean(0)
        env = "underwater_real" if r / max(g, b, 1.0) < 0.70 else "air"
        sets[env].append((img, lab, None))
        sets["underwater_synthetic"].append((img, lab, underwater(im, rng, cv2, np)))
    eval_yamls = {}
    for env, items in sets.items():
        if not items:
            continue
        d = WORK / f"{ds.name}_eval_{env}"
        (d / "images").mkdir(parents=True, exist_ok=True)
        (d / "labels").mkdir(parents=True, exist_ok=True)
        for img, lab, arr in items:
            if arr is None:
                shutil.copy2(img, d / "images" / img.name)
            else:
                cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(str(d / "images" / f"{img.stem}.jpg"))
            if lab.exists():
                shutil.copy2(lab, d / "labels" / lab.name)
        y = d / "data.yaml"
        y.write_text(f"path: {d.as_posix()}\ntrain: images\nval: images\nnc: {len(names)}\nnames: {list(names)}\n")
        eval_yamls[env] = (y, len(items))
    counts = ", ".join(f"{k.replace('_', ' ')} {v[1]}" for k, v in eval_yamls.items())
    print(f"[i] Test sets: {counts}")
    if "air" not in eval_yamls:
        print("[!] No in-air validation images found — in-air accuracy cannot be checked.")
    return yml, eval_yamls


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Retrain the Camera Detection model")
    ap.add_argument("datasets", nargs="*", help="dataset folder(s)")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16, help="lower to 8 if you get 'CUDA out of memory'")
    ap.add_argument("--no-synthetic", action="store_true", help="skip synthetic underwater/low-light/far variants")
    ap.add_argument("--weights", default=str(MODEL), help="starting model (default: current model)")
    ap.add_argument("--force", action="store_true", help="install the new model even if it is worse in air")
    a = ap.parse_args()

    folders = a.datasets
    if not folders:
        print("Drag a dataset folder into this window (or paste its path) and press Enter.")
        print("Add more folders one per line; press Enter on an empty line to start.\n")
        while True:
            p = input("Dataset folder: ").strip().strip('"')
            if not p:
                break
            folders.append(p)
    if not folders:
        sys.exit("[x] No dataset given.")

    import torch
    cuda = torch.cuda.is_available()
    if cuda:
        print(f"[i] GPU: {torch.cuda.get_device_name(0)}")
    else:
        print("[!] No CUDA GPU visible to PyTorch — training on CPU will be VERY slow.")
        print("    Install the GPU build:  pip install torch torchvision --index-url "
              "https://download.pytorch.org/whl/cu130 --force-reinstall")
        if input("    Continue on CPU anyway? [y/N] ").strip().lower() != "y":
            return

    if not any((Path(f) / "train" / "images").is_dir() for f in folders):
        print("[!] None of the folders is your original dataset (train/valid/test). Include it as well,")
        print("    otherwise the model can forget how the dock looks in air.")
        if input("    Continue anyway? [y/N] ").strip().lower() != "y":
            return

    yml, eval_yamls = build_dataset(folders, synthetic=not a.no_synthetic)

    from ultralytics import YOLO as _Detector
    model = _Detector(a.weights)
    name = f"camera_detection_{time.strftime('%Y%m%d_%H%M%S')}"
    model.train(
        data=str(yml), epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=0 if cuda else "cpu",
        patience=30, workers=2, cos_lr=True, close_mosaic=15,
        hsv_h=0.03, hsv_s=0.8, hsv_v=0.6, degrees=5.0, translate=0.2, scale=0.6, mixup=0.1,
        project=str(WORK / "runs"), name=name, exist_ok=True, plots=True,
    )
    best = WORK / "runs" / name / "weights" / "best.pt"
    if not best.exists():
        sys.exit("[x] Training finished without best.pt")

    dev = 0 if cuda else "cpu"

    def evaluate(weights, size):
        m = _Detector(str(weights))
        out = {}
        for env, (y, _) in eval_yamls.items():
            r = m.val(data=str(y), imgsz=size, device=dev, verbose=False, plots=False,
                      project=str(WORK / "runs"), name=f"{name}_val_{env}", exist_ok=True)
            out[env] = (float(r.box.map50), float(r.box.mr))
        return out

    old_size = 640
    try:
        old_size = int(_Detector(a.weights).ckpt.get("train_args", {}).get("imgsz") or 640)
    except Exception:
        pass
    print("\n[i] Comparing old and new model (mAP@50 / recall) ...")
    old = evaluate(a.weights, old_size)
    new = evaluate(best, a.imgsz)
    print(f"\n    {'test set':24s} {'old model':>16s} {'new model':>16s}")
    for env in eval_yamls:
        o, nw = old[env], new[env]
        print(f"    {env.replace('_', ' '):24s} {o[0]:7.3f} / {o[1]:5.3f}  {nw[0]:7.3f} / {nw[1]:5.3f}")

    air_ok = "air" not in new or new["air"][0] >= old["air"][0] - 0.02
    if not air_ok and not a.force:
        print("\n[!] The new model is WORSE in air than the current one — NOT installed.")
        print("    Make sure your original (in-air) dataset is included, or install anyway with --force.")
        print(f"    New model kept at: {best}")
        return

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    if MODEL.exists():
        bak = MODEL.with_name(f"camera_detection_backup_{time.strftime('%Y%m%d_%H%M%S')}.pt")
        shutil.copy2(MODEL, bak)
        print(f"\n[i] Old model backed up to {bak.name}")
    shutil.copy2(best, MODEL)
    print(f"[✓] New model installed: {MODEL}")
    print("    Restart the ground station. Leave Input size on 'Auto (model)' and Environment on 'Auto'.")


if __name__ == "__main__":       # required on Windows (data loader workers)
    main()

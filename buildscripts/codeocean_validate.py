#!/usr/bin/env python3
"""Check the capsule's ANTs build: installed, used by commandants, patched, correct and fast.

    python3 codeocean_validate.py                 # 128^3 test pair, a few minutes
    python3 codeocean_validate.py --size 64       # quicker smoke test
    python3 codeocean_validate.py --ants-dir DIR  # check a build somewhere other than /opt/ants/bin

Needs commandants, numpy and SimpleITK (``pip install 'commandants[io]'``). Outputs and a
summary go to /results/ants_validation when /results exists (Code Ocean), else a temp folder.
Exits non-zero if any check fails.

What it checks:
  1. install      /opt/ants/ANTS_COMMIT is there, antsRegistration runs
  2. PATH         antsRegistration on PATH is this build
  3. commandants  commandants resolves antsRegistration to this build
  4. patches      ANTS_STEP_TIMING=1 prints per-step timings (only the patched ITK does)
  5. CC fast path same result with the fast path off (ITK_ANTS_CC_FAST_PATH=0), and faster with it on
  6. registration CC and Mattes SyN both undo the known shift between the test images
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict

import numpy as np
import SimpleITK as sitk
from commandants import CC, AntsRegistration, Convergence, Mattes, SyN
from commandants.core.executable import clear_cache, resolve_binary, version

DEFAULT_DIR = "/opt/ants/bin"
results = []  # (status, check, detail)


def report(status, check, detail):
    results.append((status, check, detail))
    print("[%-4s] %-13s %s" % (status, check, detail), flush=True)


def same_file(a, b):
    return a is not None and b is not None and os.path.realpath(a) == os.path.realpath(b)


def make_pair(n, work):
    """Smoothed noise plus a central sphere; moving is fixed shifted 5% along x (seeded, so reproducible)."""
    f, m = os.path.join(work, "fixed_%d.nii.gz" % n), os.path.join(work, "moving_%d.nii.gz" % n)
    rng = np.random.default_rng(0)
    base = rng.random((n, n, n)).astype(np.float32)
    base = sitk.GetArrayFromImage(sitk.SmoothingRecursiveGaussian(sitk.GetImageFromArray(base), 2.0))
    zz, yy, xx = np.meshgrid(*[np.arange(n)] * 3, indexing="ij")
    blob = ((xx - n / 2) ** 2 + (yy - n / 2) ** 2 + (zz - n / 2) ** 2) < (n * 0.35) ** 2
    fix = (base + blob * 2.0).astype(np.float32)
    shift = int(round(n * 0.05))
    sitk.WriteImage(sitk.GetImageFromArray(fix), f)
    sitk.WriteImage(sitk.GetImageFromArray(np.roll(fix, shift, axis=2)), m)
    return f, m, shift


def register(ants_path, fixed, moving, metric, prefix, threads, extra_env=None):
    reg = AntsRegistration(3, output=prefix, warped_output=prefix + "Warped.nii.gz", ants_path=ants_path)
    m = CC(fixed, moving, radius=4) if metric == "cc" else Mattes(fixed, moving, bins=32)
    # The threshold is never reached, so every run does all 50x20x10 iterations and times compare fairly.
    reg.add_stage(SyN(0.2, 3.0, 0.0), m, Convergence([50, 20, 10], 1e-20, 8), [4, 2, 1], [2, 1, 0])
    env = dict(os.environ, ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=str(threads), ANTS_STEP_TIMING="1",
               **(extra_env or {}))
    t0 = time.perf_counter()
    res = reg.run(env=env, check=False)
    wall = time.perf_counter() - t0
    if res.returncode != 0:
        tail = "\n".join(((res.stderr or "") + (res.stdout or "")).splitlines()[-15:])
        raise RuntimeError("antsRegistration exited with %d:\n%s" % (res.returncode, tail))
    steps = defaultdict(float)
    for line in (res.stderr or "").splitlines():
        if line.startswith("[step] ") and " name=" in line and " wall_s=" in line:
            fields = dict(kv.split("=", 1) for kv in line[7:].split() if "=" in kv)
            steps[fields["name"]] += float(fields["wall_s"])
    return wall, steps


def read(path):
    return sitk.GetArrayFromImage(sitk.ReadImage(path)).astype(np.float64)


def max_output_diff(prefix_a, prefix_b):
    worst = 0.0
    for suffix in ("0Warp.nii.gz", "0InverseWarp.nii.gz", "Warped.nii.gz"):
        worst = max(worst, float(np.max(np.abs(read(prefix_a + suffix) - read(prefix_b + suffix)))))
    return worst


def check_registration(label, fixed, moving, prefix, n, shift):
    f, m, w = read(fixed).ravel(), read(moving).ravel(), read(prefix + "Warped.nii.gz").ravel()
    before, after = np.corrcoef(f, m)[0, 1], np.corrcoef(f, w)[0, 1]
    warp = read(prefix + "0Warp.nii.gz")  # (z, y, x, 3); component 0 is the x displacement
    # Measure the shift on the sphere's surface where it faces along x: its edge is the strongest
    # feature, so every metric has to move it (the faint texture inside is matched less consistently).
    zz, yy, xx = np.meshgrid(*[np.arange(n)] * 3, indexing="ij")
    r = np.sqrt((xx - n / 2) ** 2 + (yy - n / 2) ** 2 + (zz - n / 2) ** 2)
    caps = (np.abs(r - 0.35 * n) < 2) & (np.abs(xx - n / 2) > 0.25 * n)
    dx = float(np.median(warp[..., 0][caps]))
    ok = after > 0.95 and after > before + 0.05 and 0.4 * shift < dx < 1.5 * shift
    report("PASS" if ok else "FAIL", "registration",
           "%s: correlation with fixed %.3f -> %.3f; sphere edge moved %.2f of the %d-voxel shift"
           % (label, before, after, dx, shift))


def check_fast_path(ants_path, fixed, moving, fast, t_fast, threads, out):
    slow = os.path.join(out, "cc_nofast_")
    t_slow, _ = register(ants_path, fixed, moving, "cc", slow, threads, {"ITK_ANTS_CC_FAST_PATH": "0"})
    diff = max_output_diff(fast, slow)
    if diff == 0:
        report("PASS", "CC fast path", "outputs bit-identical with the fast path off")
    else:
        report("WARN" if diff < 1e-3 else "FAIL", "CC fast path",
               "outputs differ from the fast-path-off run by up to %.3g (expected 0 on this unit-spacing pair)" % diff)
    speedup = t_slow / t_fast
    report("PASS" if speedup >= 2 else "WARN", "CC fast path",
           "%.1f s with it, %.1f s without (%.1fx faster)" % (t_fast, t_slow, speedup))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ants-dir", help="ANTs bin directory to check (default: %s, via PATH)" % DEFAULT_DIR)
    ap.add_argument("--size", type=int, default=128, help="test volume edge length (default 128)")
    ap.add_argument("--threads", type=int, help="ITK threads (default: all CPUs available to this process)")
    ap.add_argument("--out", help="output folder (default /results/ants_validation, or a temp folder)")
    args = ap.parse_args()

    explicit = args.ants_dir is not None
    ants_dir = os.path.abspath(args.ants_dir or DEFAULT_DIR)
    threads = args.threads or (len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count())
    out = args.out or ("/results/ants_validation" if os.path.isdir("/results") else
                       tempfile.mkdtemp(prefix="ants_validation_"))
    os.makedirs(out, exist_ok=True)
    print("Checking ANTs in %s with %d threads; outputs in %s\n" % (ants_dir, threads, out), flush=True)

    # 1. install
    expected = shutil.which("antsRegistration", path=ants_dir)
    if expected is None:
        report("FAIL", "install", "no antsRegistration in %s" % ants_dir)
        return finish(out)
    commit_file = os.path.join(os.path.dirname(ants_dir), "ANTS_COMMIT")
    commit = open(commit_file).read().strip() if os.path.exists(commit_file) else None
    ver = version(ants_path=ants_dir).splitlines()
    report("PASS" if commit or explicit else "FAIL", "install",
           "commit %s; %s" % (commit or "unknown (no %s)" % commit_file, ver[0] if ver else "no --version output"))

    # 2. PATH and 3. commandants. With --ants-dir, commandants is checked through ants_path= instead.
    on_path = shutil.which("antsRegistration")
    if explicit:
        report("INFO", "PATH", "antsRegistration on PATH: %s" % on_path)
    else:
        report("PASS" if same_file(on_path, expected) else "FAIL", "PATH", "antsRegistration on PATH: %s" % on_path)
    clear_cache()
    try:
        resolved = resolve_binary("antsRegistration", ants_path=ants_dir if explicit else None)
    except Exception as exc:  # noqa: BLE001 - report whatever commandants raised
        resolved = "error: %s" % exc
    report("PASS" if same_file(resolved, expected) else "FAIL", "commandants",
           "resolves antsRegistration to %s" % resolved)
    ants_path = ants_dir if explicit else None  # the runs below go through commandants' normal lookup

    fixed, moving, shift = make_pair(args.size, out)
    n = args.size
    try:
        # 4. patches, 5. fast path, 6. registration
        fast = os.path.join(out, "cc_fast_")
        t_fast, steps = register(ants_path, fixed, moving, "cc", fast, threads)
        if steps:
            top = sorted(((v, k) for k, v in steps.items() if k not in ("optimize",)), reverse=True)[:4]
            report("PASS", "patches", "step timings present; slowest steps: %s"
                   % ", ".join("%s %.1fs" % (k, v) for v, k in top))
        else:
            report("FAIL", "patches", "no [step] lines with ANTS_STEP_TIMING=1: this build lacks the ITK patches")
        check_registration("CC", fixed, moving, fast, n, shift)

        if steps:
            check_fast_path(ants_path, fixed, moving, fast, t_fast, threads, out)
        else:
            report("SKIP", "CC fast path", "unpatched build, so ITK_ANTS_CC_FAST_PATH has no effect")

        mattes = os.path.join(out, "mattes_")
        t_mattes, _ = register(ants_path, fixed, moving, "mattes", mattes, threads)
        check_registration("Mattes (%.1f s)" % t_mattes, fixed, moving, mattes, n, shift)
    except RuntimeError as exc:
        report("FAIL", "run", str(exc))
    return finish(out)


def finish(out):
    failed = [r for r in results if r[0] == "FAIL"]
    warned = [r for r in results if r[0] == "WARN"]
    verdict = "FAILED (%d)" % len(failed) if failed else ("PASSED with warnings" if warned else "ALL PASSED")
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.writelines("[%s] %s: %s\n" % r for r in results)
        fh.write(verdict + "\n")
    print("\n%s  (summary: %s)" % (verdict, os.path.join(out, "summary.txt")))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

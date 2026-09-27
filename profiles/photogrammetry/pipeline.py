#!/usr/bin/env python3
"""KaggleCompute photogrammetry pipeline - pycolmap (CUDA) stage implementations.

Called from run.sh as: pipeline.py <stage> <work_dir>; params in $KC_PARAMS (JSON).
Replaces the colmap CLI (Debian build is CPU-only and crashes headless).
"""
import inspect
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

STAGE = sys.argv[1]
WORK = Path(sys.argv[2])
P = json.loads(os.environ.get("KC_PARAMS", "{}"))

DB = WORK / "db.db"
IMAGES = WORK / "images"
SPARSE = WORK / "sparse"
DENSE = WORK / "dense"
OUT = WORK / "output"
OUT.mkdir(exist_ok=True)

import pycolmap  # noqa: E402


def dcall(fn, *args, **kw):
    """Call a pybind function with only the kwargs its signature accepts.

    Shields against pycolmap API drift between wheel versions: unknown
    kwargs are dropped, and device=pycolmap.Device.cuda is added when the
    function takes a device argument and CUDA is available.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kw = {k: v for k, v in kw.items() if k in params}
    if "device" in params and hasattr(pycolmap, "Device"):
        kw.setdefault("device", pycolmap.Device.cuda)
    return fn(*args, **kw)


def str_paths(*paths):
    return [str(p) for p in paths]


def stage_extract():
    opts = {}
    try:
        sift = pycolmap.SiftExtractionOptions()
        sift.max_image_size = int(P.get("max_image_size", 3200))
        opts["sift_options"] = sift
    except Exception:
        pass
    t0 = time.time()
    dcall(pycolmap.extract_features, *str_paths(DB, IMAGES), **opts)
    con = sqlite3.connect(DB)
    n, k = con.execute("select count(*), coalesce(sum(rows),0) from keypoints").fetchone()
    print(f"extract: {time.time()-t0:.1f}s | {n} images, {k} keypoints")


def stage_match():
    matcher = P.get("matcher", "exhaustive")
    fn = {
        "exhaustive": pycolmap.match_exhaustive,
        "sequential": getattr(pycolmap, "match_sequential", pycolmap.match_exhaustive),
        "vocab_tree": getattr(pycolmap, "match_vocabtree", pycolmap.match_exhaustive),
    }.get(matcher, pycolmap.match_exhaustive)
    if fn is not pycolmap.match_exhaustive and matcher != "exhaustive":
        print(f"matcher {matcher} unavailable in this wheel, using exhaustive")
        fn = pycolmap.match_exhaustive
    t0 = time.time()
    dcall(fn, str(DB))
    con = sqlite3.connect(DB)
    m = con.execute("select count(*) from two_view_geometries").fetchone()[0]
    print(f"match: {time.time()-t0:.1f}s | {m} matched pairs")


def stage_sfm():
    SPARSE.mkdir(exist_ok=True)
    t0 = time.time()
    recs = dcall(pycolmap.incremental_mapping, *str_paths(DB, IMAGES, SPARSE))
    print(f"sfm: {time.time()-t0:.1f}s | {len(recs)} model(s)")
    best, best_key = None, None
    for key, rec in recs.items():
        if best is None or rec.num_reg_images() > best.num_reg_images():
            best, best_key = rec, key
    if best is None or best.num_reg_images() < 3:
        print(f"WARN: weak reconstruction (reg images: {best.num_reg_images() if best else 0})")
    if best is not None:
        dst = OUT / "sparse" / "0"
        dst.mkdir(parents=True, exist_ok=True)
        best.write(str(dst))
        print(f"best model: {best.num_reg_images()} images, {best.num_points3D()} points3D -> {dst}")


def stage_undistort():
    src = SPARSE / "0"
    if not src.exists():
        # model dir written directly by incremental_mapping
        cands = sorted(p for p in SPARSE.iterdir() if p.is_dir())
        src = cands[0] if cands else src
    dcall(pycolmap.undistort_images, *str_paths(DENSE, src, IMAGES))
    print(f"undistorted workspace: {DENSE}")


def stage_stereo():
    t0 = time.time()
    dcall(pycolmap.patch_match_stereo, str(DENSE))
    print(f"patch match stereo: {time.time()-t0:.1f}s")


def stage_fusion():
    t0 = time.time()
    fused = DENSE / "fused.ply"
    try:
        dcall(pycolmap.stereo_fusion, *str_paths(fused, DENSE), input_type="geometric")
    except Exception as e:
        print(f"geometric fusion failed ({e}), retrying photometric")
        dcall(pycolmap.stereo_fusion, *str_paths(fused, DENSE))
    shutil.copy(fused, OUT / "fused.ply")
    print(f"fusion: {time.time()-t0:.1f}s -> {OUT/'fused.ply'}")


def stage_mesh():
    fused = OUT / "fused.ply"
    mesh = OUT / "mesh.ply"
    mesher = P.get("mesher", "poisson")
    if mesher == "poisson" and hasattr(pycolmap, "poisson_meshing"):
        dcall(pycolmap.poisson_meshing, *str_paths(fused, mesh))
    elif mesher == "delaunay" and hasattr(pycolmap, "delaunay_meshing"):
        dcall(pycolmap.delaunay_meshing, *str_paths(DENSE, mesh))
    else:
        print("wheel exposes no mesher; falling back to Open3D Poisson")
        import open3d as o3d
        pcd = o3d.io.read_point_cloud(str(fused))
        pcd.estimate_normals()
        m, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=9)
        o3d.io.write_triangle_mesh(str(mesh), m)
    print(f"mesh: {mesh} ({mesh.stat().st_size/1e6:.1f} MB)")


locals()["stage_" + STAGE]()

#!/usr/bin/env python3
"""KaggleCompute photogrammetry pipeline - pycolmap (CUDA) stage implementations.

Called from run.sh as: pipeline.py <stage> <work_dir>; params in $KC_PARAMS (JSON).
Replaces the colmap CLI (Debian build is CPU-only and crashes headless).

Call signatures verified against the pycolmap-cuda12 wheel stubs (COLMAP 4.x):
- extract_features(db, imgs, extraction_options=..., device=Device.cuda)
- match_exhaustive(db, device=Device.cuda)
- incremental_mapping(db, imgs, out_dir) -> {idx: Reconstruction}
- undistort_images(out_dir, input_model_dir, imgs)
- patch_match_stereo(workspace, options=PatchMatchOptions())  # CUDA only
- stereo_fusion(out_ply, workspace, input_type=..., output_type="ply")
  (default output_type="bin" treats output_path as a model DIRECTORY)
- poisson_meshing(in_ply, out_ply)
"""
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

CUDA = pycolmap.Device.cuda if hasattr(pycolmap, "Device") else None
GPU_INDEX = "0,1"  # T4x2


def stage_extract():
    opts = pycolmap.FeatureExtractionOptions()
    opts.max_image_size = int(P.get("max_image_size", 3200))
    opts.gpu_index = GPU_INDEX
    t0 = time.time()
    pycolmap.extract_features(DB, IMAGES, extraction_options=opts, device=CUDA)
    con = sqlite3.connect(DB)
    n, k = con.execute("select count(*), coalesce(sum(rows),0) from keypoints").fetchone()
    print(f"extract: {time.time()-t0:.1f}s | {n} images, {k} keypoints")


def stage_match():
    matcher = P.get("matcher", "exhaustive")
    if matcher == "exhaustive":
        t0 = time.time()
        pycolmap.match_exhaustive(DB, device=CUDA)
    elif matcher == "sequential" and hasattr(pycolmap, "match_sequential"):
        t0 = time.time()
        pycolmap.match_sequential(DB, device=CUDA)
    else:
        print(f"matcher {matcher} unavailable in this wheel, using exhaustive")
        t0 = time.time()
        pycolmap.match_exhaustive(DB, device=CUDA)
    con = sqlite3.connect(DB)
    m = con.execute("select count(*) from two_view_geometries").fetchone()[0]
    print(f"match: {time.time()-t0:.1f}s | {m} matched pairs")


def stage_sfm():
    SPARSE.mkdir(exist_ok=True)
    t0 = time.time()
    recs = pycolmap.incremental_mapping(DB, IMAGES, SPARSE)
    print(f"sfm: {time.time()-t0:.1f}s | {len(recs)} model(s)")
    best = None
    for rec in recs.values():
        if best is None or rec.num_reg_images() > best.num_reg_images():
            best = rec
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
        cands = sorted(p for p in SPARSE.iterdir() if p.is_dir())
        src = cands[0] if cands else src
    pycolmap.undistort_images(DENSE, src, IMAGES)
    print(f"undistorted workspace: {DENSE}")


def stage_stereo():
    opts = pycolmap.PatchMatchOptions()
    opts.gpu_index = GPU_INDEX
    t0 = time.time()
    pycolmap.patch_match_stereo(DENSE, options=opts)
    print(f"patch match stereo: {time.time()-t0:.1f}s")


def stage_fusion():
    t0 = time.time()
    fused = DENSE / "fused.ply"
    try:
        pycolmap.stereo_fusion(fused, DENSE, input_type="geometric", output_type="ply")
    except Exception as e:
        print(f"geometric fusion failed ({type(e).__name__}: {str(e)[:150]}), retrying photometric")
        pycolmap.stereo_fusion(fused, DENSE, input_type="photometric", output_type="ply")
    shutil.copy(fused, OUT / "fused.ply")
    print(f"fusion: {time.time()-t0:.1f}s -> {OUT/'fused.ply'}")


def stage_mesh():
    fused = OUT / "fused.ply"
    mesh = OUT / "mesh.ply"
    mesher = P.get("mesher", "poisson")
    if mesher == "poisson" and hasattr(pycolmap, "poisson_meshing"):
        pycolmap.poisson_meshing(fused, mesh)
    elif mesher == "delaunay" and hasattr(pycolmap, "delaunay_meshing"):
        pycolmap.delaunay_meshing(DENSE, mesh)
    else:
        print("wheel exposes no mesher; falling back to Open3D Poisson")
        import open3d as o3d
        pcd = o3d.io.read_point_cloud(str(fused))
        pcd.estimate_normals()
        m, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=9)
        o3d.io.write_triangle_mesh(str(mesh), m)
    print(f"mesh: {mesh} ({mesh.stat().st_size/1e6:.1f} MB)")


locals()["stage_" + STAGE]()

import glob
import json
import os
from pathlib import Path

import hydra
import lightning as L
import numpy as np
import trimesh
from omegaconf import DictConfig, OmegaConf

from lib.evaluate import compute_chamfer_distance
from lib.generate import reconstruct_training_data
from lib.models.deepsdf import DeepSDF


def load_run_config(path: str):
    return OmegaConf.load(Path(path, ".hydra/config.yaml"))


def load_checkpoint_path(path: str):
    paths = glob.glob(path + "/**/last.ckpt", recursive=True)
    if paths:
        return paths[0]
    raise ValueError(f"Could not find a last.ckpt file in {path}")


def load_shape2idx(path: str):
    with open(path + "/shape2idx.json", "r") as f:
        shape2idx = json.load(f)

    return shape2idx


def identify_matching_objs(gt_path, rec_paths):
    matches = list()
    for rec in rec_paths:
        # find gt
        folder = rec.split("/")[-1].replace("_", "/").split("/")[:-1]
        full_gt_path = os.path.join(gt_path, *folder, "model_unit_sphere.obj")

        matches.append((full_gt_path, rec))
    return matches


@hydra.main(version_base=None, config_path="../conf", config_name="reconstruct_deepsdf")
def reconstruct(cfg: DictConfig) -> None:
    if not os.path.exists(cfg.log_path):
        raise ValueError("Please provide a log path")

    L.seed_everything(cfg.seed)
    run_config = load_run_config(cfg.log_path)

    shape2idx = load_shape2idx(run_config.data.data_dir)
    idx2shape = {v: k for k, v in shape2idx.items()}

    ckpt_path = (
        cfg.ckpt_path
        if cfg.ckpt_path is not None
        else load_checkpoint_path(cfg.log_path)
    )

    rec_paths = reconstruct_training_data(
        DeepSDF, ckpt_path, idx2shape, cfg.resolution_list
    )

    if cfg.chamfer:
        matches = identify_matching_objs(run_config.data.data_dir, rec_paths)
        chamfers = dict()
        for match in matches:
            # load meshes
            gt = trimesh.load(match[0], force="mesh")
            rec = trimesh.load(match[1], force="mesh")
            chamfer = compute_chamfer_distance(
                gt, rec, n_samples=cfg.chamfer_num_samples
            )
            idx = match[0].split("/")[-2]
            chamfers[idx] = chamfer
        chamfers["mean_chamfer"] = np.mean(list(chamfers.values()))
        chamfers["num_samples"] = cfg.chamfer_num_samples

        np.savez(cfg.log_path + "/chamfers.npz", chamfers)
        print(f"Mean Chamfer distance over all objects is: {chamfers['mean_chamfer']}")


if __name__ == "__main__":
    reconstruct()
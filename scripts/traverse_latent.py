from pathlib import Path
from typing import List

import hydra
import lightning as L
import numpy as np
import open3d as o3d
import pandas as pd
import wandb
from lightning import Callback, LightningDataModule, LightningModule, Trainer
from lightning.pytorch.loggers import WandbLogger
from omegaconf import DictConfig
from torch.utils.data import DataLoader

from lib.data.metainfo import MetaInfo
from lib.utils import (
    create_logger,
    instantiate_callbacks,
    instantiate_loggers,
    log_hyperparameters,
)

log = create_logger("traverse_latent")


@hydra.main(version_base=None, config_path="../conf", config_name="traverse_latent")
def optimize(cfg: DictConfig) -> None:
    log.info("==> loading config ...")
    L.seed_everything(cfg.seed)

    log.info(f"==> initializing model <{cfg.model._target_}>")
    model: LightningModule = hydra.utils.instantiate(cfg.model)

    log.info("==> initializing logger ...")
    logger: WandbLogger = instantiate_loggers(cfg.get("logger"))

    log.info(f"==> initializing trainer <{cfg.trainer._target_}>")
    trainer: Trainer = hydra.utils.instantiate(cfg.trainer, logger=logger)

    object_dict = {
        "cfg": cfg,
        "model": model,
        "logger": logger,
        "trainer": trainer,
    }
    if logger:
        log.info("==> logging hyperparameters ...")
        log_hyperparameters(object_dict)

    log.info("==> start traversal ...")
    steps = np.linspace(0, 1, num=cfg.traversal_steps)
    dataloader = DataLoader(steps, batch_size=1)  # type: ignore
    trainer.validate(model=model, dataloaders=dataloader)

    if cfg.save_mesh and (meshes := model.meshes):
        log.info("==> save meshes ...")
        for m in meshes:
            t, mesh = m["t"], m["mesh"]
            path = Path(cfg.paths.mesh_dir, f"{t}.obj")
            path.parent.mkdir(parents=True, exist_ok=True)
            o3d.io.write_triangle_mesh(
                path.as_posix(),
                mesh=mesh,
                write_triangle_uvs=False,
            )


if __name__ == "__main__":
    optimize()

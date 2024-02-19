import torch
from lightning import LightningModule
from torch.nn.functional import cosine_similarity
from torchvision.models import resnet18
from torchvision.models.resnet import ResNet18_Weights


class BarlowTwins(LightningModule):
    def __init__(
        self,
        gamma: float = 5e-03,
        embedding_size: int = 256,
        pretrained: bool = True,
        lr_head: float = 1e-03,
        lr_backbone: float = 1e-05,
        scheduler=None,
    ) -> None:
        super().__init__()
        self.save_hyperparameters(logger=False)
        self.lr_head = lr_head
        self.lr_backbone = lr_backbone
        self.support_latent = False

        # load the resnet18 backbone
        weights = ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
        self.backbone = resnet18(weights=weights)
        self.backbone.fc = torch.nn.Identity()
        self.head = torch.nn.Linear(in_features=512, out_features=embedding_size)

    def embedding(self, batch):
        return self.forward(batch)

    def compute(self, emb1, emb2):
        return 1 - cosine_similarity(emb1, emb2)

    def forward(self, batch):
        x = self.backbone(batch)
        return self.head(x)

    def get_all_triplets_indices(self, labels):
        labels1 = labels.unsqueeze(1)
        labels2 = labels.unsqueeze(0)
        matches = (labels1 == labels2).byte()
        diffs = matches ^ 1
        matches.fill_diagonal_(0)
        triplets = matches.unsqueeze(2) * diffs.unsqueeze(1)
        return torch.where(triplets)

    def get_augmentations_idx(self, labels):
        y_a = torch.argsort(labels)[::2]
        y_b = torch.argsort(labels)[1::2]
        assert len(y_a) == len(y_b)
        return y_a, y_b

    def model_step(self, batch, split: str = "train"):
        # compute embeddings
        N = batch["image"].shape[0] // 2
        emb = self.forward(batch["image"])  # (2*N, D)

        # calculate the anchor, positive, negative indx
        a_idx, p_idx, n_idx = self.get_all_triplets_indices(batch["label"])

        # calculate the triplet loss
        d_ap = torch.norm(emb[a_idx] - emb[p_idx], dim=-1)
        self.log(f"{split}/distance_anchor_positive", d_ap.mean())
        d_an = torch.norm(emb[a_idx] - emb[n_idx], dim=-1)
        self.log(f"{split}/distance_anchor_negative", d_an.mean())

        # calculate how many pairs would be classified wrong
        incorrect_count = ((d_ap - d_an) > 0).sum().float()
        self.log(f"{split}/incorrect_count", incorrect_count, prog_bar=True)

        # normalize repr. along the batch dimension
        a_idx, b_idx = self.get_augmentations_idx(batch["label"])
        z_a_norm = (emb[a_idx] - emb[a_idx].mean(0)) / emb[a_idx].std(0)  # NxD
        z_b_norm = (emb[b_idx] - emb[b_idx].mean(0)) / emb[b_idx].std(0)  # NxD

        # cross-correlation matrix
        c = torch.mm(z_a_norm.T, z_b_norm) / N  # DxD
        diag_mask = torch.eye(*c.shape, dtype=torch.bool)

        # loss
        invariance_loss = (1 - c[diag_mask]).pow(2).sum()
        self.log(f"{split}/invariance_loss", invariance_loss)

        redundancy_loss = (c[~diag_mask]).pow(2).sum() * self.hparams["gamma"]
        self.log(f"{split}/redundancy_loss", redundancy_loss)

        loss = (invariance_loss + redundancy_loss) / N
        self.log(f"{split}/loss", loss, prog_bar=True)

        return loss

    def training_step(self, batch, batch_idx):
        return self.model_step(batch, split="train")

    def validation_step(self, batch, batch_idx):
        return self.model_step(batch, split="val")

    def test_step(self, batch, batch_idx):
        return self.model_step(batch, split="test")

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(
            [
                {"params": self.backbone.parameters(), "lr": self.lr_backbone},
                {"params": self.head.parameters(), "lr": self.lr_head},
            ]
        )
        if self.hparams["scheduler"] is not None:
            scheduler = self.hparams["scheduler"](optimizer=optimizer)
            return {
                "optimizer": optimizer,
                "lr_scheduler": {"scheduler": scheduler, "monitor": "train/loss"},
            }
        return {"optimizer": optimizer}

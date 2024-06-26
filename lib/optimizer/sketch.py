import torch

from lib.optimizer.latent import LatentOptimizer


class SketchOptimizer(LatentOptimizer):
    def __init__(
        self,
        loss_mode: str = "l1",  # none, l1
        loss_weight: float = 1.0,
        silhouette_loss: str = "silhouette",  # none, silhouette
        silhouette_weight: float = 1.0,
        verbose: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)

    def training_step(self, batch, batch_idx):
        self.loss.eval()

        # get the gt image and normals
        sketch = batch["sketch"]  # dim (1, 3, H, W) and values are (-1, 1)
        sketch_emb = self.loss.embedding(sketch, mode="sketch")  # (1, D)

        ############################################################
        # Global Loss Dataset
        ############################################################

        # calculate the points on the surface
        with torch.no_grad():
            points, surface_mask = self.deepsdf.sphere_tracing(
                latent=self.latent,
                points=batch["points"].squeeze(),
                rays=batch["rays"].squeeze(),
                mask=batch["mask"].squeeze(),
            )
        # render the grayscale image and get the embedding from the grayscale image
        rendered_grayscale = self.deepsdf.render_grayscale(
            latent=self.latent,
            points=points,
            mask=surface_mask,
        )  # (H, W, 3)
        grayscale = self.deepsdf.image_to_siamese(rendered_grayscale)  # (1, 3, H, W)
        grayscale_emb = self.loss.embedding(grayscale, mode="grayscale")  # (1, D)
        # calculate the loss between the sketch and the grayscale image
        if (loss_mode := self.hparams["loss_mode"]) == "none":
            loss = torch.tensor(0.0).to(self.device)
        else:
            loss = self.loss.compute(sketch_emb, grayscale_emb, loss_mode).clone()
            loss *= self.hparams["loss_weight"]
        if self.hparams["verbose"]:
            self.log("optimize/loss", loss)

        ############################################################
        # Silhouette
        ############################################################

        # calculate the silhouette loss to fix missing parts and grow in empty space
        silhouette_loss = torch.tensor(0.0).to(self.device)
        if self.hparams["silhouette_loss"] != "none":
            if "silhouette" in batch:
                silhouette = batch["silhouette"].squeeze()
            else:
                with torch.no_grad():
                    silhouette_points, surface_mask = self.deepsdf.sphere_tracing(
                        latent=sketch_emb.squeeze(),
                        points=batch["points"].squeeze(),
                        rays=batch["rays"].squeeze(),
                        mask=batch["mask"].squeeze(),
                    )
                    # render grayscale image and get embedding from the grayscale image
                    rendered_normals = self.deepsdf.render_normals(
                        latent=sketch_emb.squeeze(),
                        points=silhouette_points,
                        mask=surface_mask,
                    )  # (H, W, 3)
                    # render the normals image
                    normal = self.deepsdf.image_to_siamese(rendered_normals)
                    out = self.deepsdf.render_silhouette(
                        normals=rendered_normals,
                        points=silhouette_points,
                        latent=self.latent,
                        world_to_camera=batch["world_to_camera"].squeeze(),
                        camera_width=batch["camera_width"].squeeze(),
                        camera_height=batch["camera_height"].squeeze(),
                        camera_focal=batch["camera_focal"].squeeze(),
                        return_full=True,
                    )
                    silhouette = out["final_silhouette"]  # dim (H, W) values in (0, 1)
            H, W = silhouette.shape
            # insert the min points from self.latent
            min_sdf = torch.abs(self.forward(points)).reshape(H, W)
            soft_silhouette = min_sdf - self.deepsdf.hparams["surface_eps"]
            silhouette_loss = silhouette * torch.relu(soft_silhouette)
            silhouette_loss += (1.0 - silhouette) * torch.relu(-soft_silhouette)
            silhouette_error_map = silhouette_loss.clone()  # (H, W)
            silhouette_loss = silhouette_loss[batch["mask"].reshape(H, W)]
            silhouette_loss *= self.hparams["silhouette_weight"]
            if self.hparams["verbose"]:
                self.log("optimize/silhouette_loss", silhouette_loss.mean())

        ############################################################
        # Regularization
        ############################################################

        reg_loss = torch.tensor(0.0).to(self.device)
        if self.hparams["reg_loss"] != "none":
            if self.hparams["reg_loss"] == "retrieval":
                std = self.shape_latents.std(0)
                mean = self.shape_latents.mean(0)
                reg_loss = ((self.latent.clone() - mean) / std).pow(2)
            elif self.hparams["reg_loss"] == "prior":
                std = self.deepsdf.lat_vecs.weight.std(0)
                mean = self.deepsdf.lat_vecs.weight.mean(0)
                reg_loss = ((self.latent.clone() - mean) / std).pow(2)
            elif self.hparams["reg_loss"] == "latent":
                reg_loss = self.loss.compute(sketch_emb, self.latent[None, ...])
            reg_loss *= self.hparams["reg_weight"]
            if self.hparams["verbose"]:
                self.log("optimize/reg_loss", reg_loss.mean())

        ############################################################
        # Total Loss
        ############################################################

        total_loss = reg_loss.mean() + loss.mean() + silhouette_loss.mean()
        if self.hparams["verbose"]:
            self.log("optimize/total_loss", total_loss, prog_bar=True)

        latent_norm = torch.norm(self.latent, dim=-1)
        if self.hparams["verbose"]:
            self.log("optimize/latent_norm", latent_norm)

        ############################################################
        # Visualize the different images
        ############################################################
        if self.hparams["verbose"]:
            self.log_image("handdrawn", self.deepsdf.loss_input_to_image(sketch))
            self.log_image("grayscale", self.deepsdf.loss_input_to_image(grayscale))
            if self.hparams["silhouette_loss"] != "none":
                if "silhouette" in batch:
                    self.log_image("silhouette", silhouette)
                else:
                    self.log_image("normal", self.deepsdf.loss_input_to_image(normal))
                    self.log_silhouette(out, "final_silhouette")
        return total_loss

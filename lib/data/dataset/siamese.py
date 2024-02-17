from typing import Callable, Optional

from torch.utils.data import Dataset

from lib.data.metainfo import MetaInfo


class SiameseDataset(Dataset):
    def __init__(
        self,
        data_dir: str = "data/",
        split: Optional[str] = None,
        sketch_transform: Optional[Callable] = None,
        normal_transform: Optional[Callable] = None,
    ):
        self.sketch_transform = sketch_transform
        self.normal_transform = normal_transform
        self.metainfo = MetaInfo(data_dir=data_dir, split=split)
        self.metainfo.load_snn()

    def __len__(self):
        return self.metainfo.snn_count

    def __getitem__(self, index):
        info = self.metainfo.get_snn(index)
        obj_id = info["obj_id"]
        image_id = info["image_id"]
        label = info["label"]
        image_type = info["image_type"]

        if image_type == "sketch":
            image = self.metainfo.load_sketch(obj_id, image_id)
            if self.sketch_transform is not None:
                image = self.sketch_transform(image)

        if image_type == "normal":
            image = self.metainfo.load_normal(obj_id, image_id)
            if self.normal_transform is not None:
                image = self.normal_transform(image)

        return {
            "image": image,
            "image_id": int(image_id),
            "type_idx": self.metainfo.image_type_2_type_idx[image_type],
            "label": label,
        }
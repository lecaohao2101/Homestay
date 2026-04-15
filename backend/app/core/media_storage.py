from pathlib import Path
from uuid import uuid4

from app.core.config import settings

CONTENT_TYPE_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class LocalMediaStorage:
    def __init__(self, root_dir: str):
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, *, content_type: str, data: bytes) -> str:
        ext = CONTENT_TYPE_EXTENSION.get(content_type, "")
        key = f"{uuid4().hex}{ext}"
        target = (self.root / key).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid media path")
        target.write_bytes(data)
        return key

    def delete(self, key: str) -> None:
        target = (self.root / key).resolve()
        if target.parent != self.root:
            return
        if target.exists():
            target.unlink()

    def get_path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if target.parent != self.root:
            raise FileNotFoundError("Invalid media path")
        return target


def get_media_storage() -> LocalMediaStorage:
    if settings.MEDIA_STORAGE_PROVIDER != "local":
        raise ValueError("Only local media storage is currently supported")
    return LocalMediaStorage(settings.MEDIA_LOCAL_DIR)

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

from .const import TRANSLATION_CACHE_VERSION
from ..utils import log


# --------------------------------------------------------------------------- #
#  Bộ nhớ đệm theo lô: chạy lại sau khi lỗi thì không phải dịch lại từ đầu
# --------------------------------------------------------------------------- #
class ChunkCache:
    """Lưu kết quả từng lô ra file JSON. Khoá gắn với NỘI DUNG lô nên khi
    phụ đề gốc thay đổi thì cache tự vô hiệu, không dùng nhầm bản cũ."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self.data: Dict[str, List[str]] = {}
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                if self.data:
                    log(f"Tìm thấy {len(self.data)} lô đã dịch trước đó - dùng lại.", "ok")
            except Exception:
                self.data = {}

    @staticmethod
    def key(index: int, texts: List[str]) -> str:
        h = hashlib.md5("\n".join(texts).encode("utf-8")).hexdigest()[:12]
        return f"{TRANSLATION_CACHE_VERSION}-{index}-{len(texts)}-{h}"

    def get(self, key: str) -> Optional[List[str]]:
        v = self.data.get(key)
        return v if isinstance(v, list) else None

    def put(self, key: str, values: List[str]) -> None:
        self.data[key] = values
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass          # cache hỏng không được phép làm chết chương trình

    def discard(self, key: str) -> None:
        if key not in self.data:
            return
        self.data.pop(key, None)
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def remove(self) -> None:
        self.data = {}
        if self.path and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass


class TranslationIncomplete(RuntimeError):
    """Dịch thiếu quá nhiều lô - không nên đem đi lồng tiếng luôn."""

    def __init__(self, failed: List[int], total: int):
        self.failed, self.total = failed, total
        super().__init__(f"thiếu {len(failed)}/{total} lô")

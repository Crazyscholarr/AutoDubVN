"""Vá tương thích môi trường - PHẢI gọi TRƯỚC khi import funasr/modelscope.

LỖI ĐƯỢC VÁ Ở ĐÂY
-----------------
    Download: iic/speech_seaco_paraformer_large_... failed!:
        'HubConfig' object has no attribute '_logged_out'
    -> model 'paraformer-zh' is not registered.

NGUYÊN NHÂN THẬT SỰ (không phải lỗi của funasr, cũng không phải lỗi mạng):
    Python **3.10.0** có một lỗi trong module `dataclasses`: khi khai báo
    `@dataclass(slots=True)` cùng với `field(init=False, default=...)` thì giá
    trị mặc định bị MẤT - vì trình tạo `__slots__` xoá thuộc tính mặc định ở
    cấp class, còn `__init__` thì không gán (do `init=False`). Kết quả: đọc
    thuộc tính đó ném AttributeError.

    Gói `modelscope_hub` (modelscope >= 1.38 tách hub ra gói này) khai báo đúng
    kiểu đó:

        @dataclass(slots=True)
        class HubConfig:
            _logged_out: bool = field(default=False, init=False, repr=False)
            _endpoint_overridden: bool = field(default=False, init=False, ...)

    nên `HubConfig()` vỡ ngay trong `__post_init__` -> modelscope không tải
    được model -> FunASR coi 'paraformer-zh' là tên class chưa đăng ký.

    Lỗi này đã được Python sửa từ **3.10.1**. Máy đang chạy 3.10.0.

CÁCH XỬ LÝ
    - Cách gốc rễ (khuyên dùng): nâng Python lên 3.10.11 (hoặc 3.11/3.12) rồi
      tạo lại venv.
    - Cách tại chỗ (file này): gán sẵn giá trị mặc định vào slot TRƯỚC khi
      `__post_init__` chạy. Không sửa file của thư viện, không ảnh hưởng máy
      đã chạy Python mới (tự phát hiện, không dính lỗi thì không vá).
"""
from __future__ import annotations

import dataclasses
import sys

_done = False


def _patch_slots_dataclass(cls) -> bool:
    """Gán sẵn các field `init=False, default=...` bị Python 3.10.0 làm mất."""
    defaults = {f.name: f.default for f in dataclasses.fields(cls)
                if not f.init and f.default is not dataclasses.MISSING}
    if not defaults:
        return False
    orig_init = cls.__init__

    def __init__(self, *args, **kwargs):
        for name, value in defaults.items():
            object.__setattr__(self, name, value)
        orig_init(self, *args, **kwargs)

    cls.__init__ = __init__
    cls.__autodub_patched__ = True
    return True


def patch_modelscope_hubconfig(verbose: bool = True) -> bool:
    """Trả về True nếu đã vá. An toàn tuyệt đối: mọi lỗi đều nuốt im lặng."""
    global _done
    if _done:
        return False
    _done = True

    # Lỗi chỉ tồn tại ở Python 3.10.0; bản khác bỏ qua cho nhanh.
    if sys.version_info[:3] > (3, 10, 0):
        return False

    try:
        from modelscope_hub import config as _cfg
    except Exception:
        return False

    cls = getattr(_cfg, "HubConfig", None)
    if cls is None or getattr(cls, "__autodub_patched__", False):
        return False

    # Thử tạo thật một object: chỉ vá khi ĐÚNG là dính lỗi, tránh vá bừa.
    try:
        cls()
        return False                       # môi trường bình thường
    except AttributeError as e:
        if "has no attribute" not in str(e):
            return False
    except Exception:
        return False                       # lỗi khác -> không phải ca này

    try:
        if not _patch_slots_dataclass(cls):
            return False
        cls()                              # kiểm chứng đã hết lỗi
    except Exception:
        return False

    if verbose:
        try:
            from .utils import log
            log("Đã vá lỗi dataclass của Python 3.10.0 cho modelscope_hub "
                "(nên nâng Python lên 3.10.11+ để khỏi cần vá).", "ok")
        except Exception:
            pass
    return True

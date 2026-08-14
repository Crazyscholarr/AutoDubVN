"""Trạng thái dùng chung của backend GUI: hàng đợi, tiến độ, khoá luồng.

Tách riêng để mọi module khác (pipeline, render, HTTP handler...) cùng nhìn
vào MỘT bộ trạng thái duy nhất mà không import vòng lẫn nhau. Các dict ở đây
được sửa TẠI CHỖ (mutate), không bao giờ gán lại, nên `from .state import
STATE` ở đâu cũng trỏ về đúng một đối tượng.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from ..utils import log, set_cancel_event

# autodub/server/state.py -> autodub/server -> autodub -> thư mục gốc dự án
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_DIR = os.path.join(HERE, "ui")
CONFIG_PATH = os.path.join(HERE, "config.yaml")

_LOCK = threading.RLock()
_NEXT_ID = [1]
_CANCEL_EVENT = threading.Event()
set_cancel_event(_CANCEL_EVENT)

STATE: Dict = {
    "queue": [],          # [{id,name,path,status,progress,note}]
    "selected": None,
    "running": False,
    "cancel": False,
    "busy": "",           # việc nền đang chạy (vd "Đang dò vùng sub cứng…")
    "progress": {"pct": 0, "step": "", "detail": "", "sub": 0, "total": 6},
    "log": [],
    "nvenc": None,
    "manual": {
        "rev": 0, "working": False, "status": "Sẵn sàng",
        "audio_path": "", "audio_duration": 0.0,
        "output_path": "", "error": "",
        "script_path": "", "script_title": "", "script_words": 0,
        "recommended_voice": "", "voice_analysis": {},
        "voice_recommendations": [],
    },
}

PROJECTS: Dict[int, Dict] = {}
# Số hiệu phiên bản của từng dự án. Giao diện chỉ tải lại DỰ ÁN (có thể nặng vài
# MB với phim dài) khi số này đổi, thay vì kéo về mỗi 1.2 giây -> hết treo.
REV: Dict[int, int] = {}


def bump_rev(job_id: int):
    with _LOCK:
        REV[job_id] = REV.get(job_id, 0) + 1


def _log(msg: str, kind: str = "info"):
    with _LOCK:
        STATE["log"].append({"t": time.time(), "kind": kind, "msg": str(msg)})
        del STATE["log"][:-300]
    log(msg, kind)


def _progress(pct: float = None, step: str = None, detail: str = None,
              sub: int = None):
    with _LOCK:
        p = STATE["progress"]
        if pct is not None:
            p["pct"] = max(0, min(100, round(float(pct), 1)))
        if step is not None:
            p["step"] = step
        if detail is not None:
            p["detail"] = detail
        if sub is not None:
            p["sub"] = sub


def _find(job_id: int) -> Optional[Dict]:
    for j in STATE["queue"]:
        if j["id"] == job_id:
            return j
    return None

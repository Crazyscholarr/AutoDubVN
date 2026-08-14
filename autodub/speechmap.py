"""BẢN ĐỒ THOẠI - mốc thời gian THẬT của từng ký tự mà ASR nghe được.

VÌ SAO CẦN MODULE NÀY
=====================
Trước đây mọi chỗ phải chia lại một dòng phụ đề thành nhiều dòng đều chia thời
gian theo TỈ LỆ SỐ KÝ TỰ trên cả ô thời gian:

    dòng gốc  [00:03,6 -> 00:17,8]  "A B C D"      (14,2 giây)
    chia 2    [00:03,6 -> 00:10,7]  "A B"
              [00:10,7 -> 00:17,8]  "C D"

Cách chia đó coi ô thời gian như một băng nói LIÊN TỤC. Thực tế bên trong ô có
những quãng lặng (nhân vật nghỉ, nhạc nền, tiếng động), nên mốc 10,7 giây rơi
vào chỗ KHÔNG có ai nói. Giọng đọc tiếng Việt của phần "C D" vì thế phát sớm
hoặc muộn vài giây so với miệng nhân vật. Lỗi này cộng dồn qua ba bước liên tiếp
(gom câu nguồn -> chia lại bản dịch -> tách theo dấu câu) và chính là cảm giác
"voice trượt khỏi hình, sửa mãi không hết".

FunASR/Paraformer trả về timestamp theo TỪNG KÝ TỰ (faster-whisper trả theo
từng từ). Đó là mốc thời gian thật. Module này gom toàn bộ mốc đó thành một
"bản đồ thoại" dùng chung cho cả lần chạy, để mọi bước chia lại phụ đề đặt ranh
giới ĐÚNG vào chỗ ký tự tương ứng thay vì nội suy thẳng qua cả quãng lặng.

CÁCH DÙNG
=========
    from . import speechmap
    speechmap.set_active(speechmap.SpeechMap(marks))   # sau khi ASR xong
    ...
    bounds = speechmap.get_active().slice_window(start, end, weights)

Không có bản đồ (dùng lại SRT cũ, ASR không trả timestamp) thì mọi hàm trả về
None và phần gọi tự lùi về cách chia theo tỉ lệ như trước - không vỡ luồng nào.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# Hai ký tự cách nhau quá mức này thì coi như đã sang một cụm nói khác.
DEFAULT_BURST_GAP = 0.30


def _clean_marks(marks) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for item in marks or []:
        try:
            if isinstance(item, dict):
                a = float(item.get("start", item.get("begin")))
                b = float(item.get("end", item.get("stop")))
            else:
                a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if b < a:
            a, b = b, a
        if b <= a:
            b = a + 0.01
        out.append((a, b))
    out.sort()
    # Bỏ mốc trùng lặp (vá lỗ hổng có thể nhận lại cùng một đoạn nhiều lần).
    dedup: List[Tuple[float, float]] = []
    for a, b in out:
        if dedup and abs(dedup[-1][0] - a) < 1e-4 and abs(dedup[-1][1] - b) < 1e-4:
            continue
        dedup.append((a, b))
    return dedup


@dataclass
class SpeechMap:
    """Danh sách mốc (start, end) của từng ký tự/từ ASR nghe được, đơn vị GIÂY."""

    marks: List[Tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.marks = _clean_marks(self.marks)

    # ---------------------------------------------------------------- cơ bản #
    def __len__(self) -> int:
        return len(self.marks)

    @property
    def empty(self) -> bool:
        return not self.marks

    def merge(self, other: "SpeechMap") -> "SpeechMap":
        return SpeechMap(list(self.marks) + list(other.marks))

    def shift(self, seconds: float) -> "SpeechMap":
        """Dịch toàn bộ mốc (dùng khi chỉ xử lý một đoạn đã cắt của video)."""
        d = float(seconds or 0.0)
        if abs(d) < 1e-9:
            return SpeechMap(list(self.marks))
        return SpeechMap([(a + d, b + d) for a, b in self.marks])

    # -------------------------------------------------------------- tra cứu #
    def _bisect(self, t: float) -> int:
        lo, hi = 0, len(self.marks)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.marks[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def window(self, start: float, end: float) -> List[Tuple[float, float]]:
        """Các mốc có TÂM nằm trong [start, end]."""
        if self.empty or end <= start:
            return []
        i = max(0, self._bisect(start) - 2)
        out: List[Tuple[float, float]] = []
        for a, b in self.marks[i:]:
            if a > end:
                break
            mid = (a + b) / 2.0
            if start <= mid <= end:
                out.append((a, b))
        return out

    def speech_seconds(self, start: float, end: float) -> float:
        """Tổng thời lượng THẬT có tiếng nói trong khoảng, tính theo mốc ký tự."""
        total = 0.0
        for a, b in self.bursts_in(start, end):
            total += max(0.0, b - a)
        return total

    def bursts_in(self, start: float, end: float,
                  gap: float = DEFAULT_BURST_GAP) -> List[Tuple[float, float]]:
        """Gom mốc ký tự thành cụm nói liền mạch trong khoảng cho trước."""
        marks = self.window(start, end)
        if not marks:
            return []
        gap = max(0.0, float(gap))
        out: List[Tuple[float, float]] = [list(marks[0])]  # type: ignore[list-item]
        for a, b in marks[1:]:
            if a - out[-1][1] > gap:
                out.append([a, b])  # type: ignore[arg-type]
            else:
                out[-1][1] = max(out[-1][1], b)
        return [(float(a), float(b)) for a, b in out]

    def onset(self, t: float, tol: float = 0.35,
              gap: float = DEFAULT_BURST_GAP) -> Optional[float]:
        """Mốc BẮT ĐẦU cụm nói gần `t` nhất, nếu cách không quá `tol` giây.

        Dùng để hút mốc phát giọng về đúng lúc nhân vật mở miệng: lệch 0,2-0,3
        giây nghe đã thấy "hơi sai môi" dù không ai đo được bằng mắt.
        """
        if self.empty or tol <= 0:
            return None
        lo, hi = t - tol, t + tol
        cands = self.bursts_in(lo - gap, hi + gap, gap=gap)
        best = None
        for a, _ in cands:
            if abs(a - t) <= tol and (best is None or abs(a - t) < abs(best - t)):
                best = a
        return best

    # ------------------------------------------------------- chia lại phụ đề #
    @staticmethod
    def _snap_to_pause(marks: List[Tuple[float, float]], idx: int,
                       lo: int, hi: int, radius: int = 1,
                       min_gap: float = 0.22) -> int:
        """Kéo chỗ cắt về KHOẢNG NGHỈ gần nhất trong bán kính vài ký tự.

        Trọng số nội dung (số ký tự bản dịch) chỉ ước lượng được vị trí, sai một
        ký tự là bình thường. Nhưng nếu chỗ cắt đứng ngay cạnh một quãng nghỉ
        dài, sai một ký tự đổi thành sai vài GIÂY - cắt sang bên kia quãng nghỉ.
        Người ngắt câu ở chỗ nghỉ, nên ta cũng ngắt ở đó.

        `radius` phải nhỏ hơn nửa khoảng cách tới chỗ cắt lân cận, nếu không một
        chỗ cắt sẽ "giành" mất quãng nghỉ của chỗ cắt kế bên rồi đẩy cả hai đi
        sai (đo trên video thật: một chỗ lệch 5,4 giây vì lý do này).
        """
        n = len(marks)
        if n < 2 or hi <= lo or radius < 1:
            return idx

        def gap_at(j: int) -> float:
            if j <= 0 or j >= n:
                return 0.0
            return max(0.0, marks[j][0] - marks[j - 1][1])

        best_j, best_gap = idx, gap_at(idx)
        for j in range(max(lo, idx - radius), min(hi, idx + radius) + 1):
            g = gap_at(j)
            if g > best_gap + 1e-6:
                best_gap, best_j = g, j
        return best_j if best_gap >= min_gap else idx

    def slice_window(self, start: float, end: float,
                     weights: Sequence[float],
                     min_part: float = 0.12,
                     ) -> Optional[List[Tuple[float, float]]]:
        """Chia [start, end] thành len(weights) phần THEO MỐC KÝ TỰ THẬT.

        `weights` là trọng số nội dung của từng phần (thường là số ký tự). Hàm
        đổi trọng số thành vị trí ký tự trong bản đồ rồi lấy đúng thời điểm của
        ký tự đó, nên ranh giới luôn rơi vào khe giữa hai ký tự có thật - không
        bao giờ rơi giữa một quãng lặng dài như cách chia theo tỉ lệ.

        Trả về None nếu không đủ dữ liệu (phần gọi tự lùi về chia theo tỉ lệ).
        """
        n_parts = len(weights)
        if n_parts <= 0 or end <= start:
            return None
        if n_parts == 1:
            return [(float(start), float(end))]

        marks = self.window(start, end)
        # Cần ít nhất 2 mốc cho mỗi phần. Mốc quá thưa so với số phần nghĩa là
        # bản đồ thiếu dữ liệu cho vùng này (dòng nhạc/hiệu ứng, hoặc ASR chỉ
        # nghe được vài tiếng): lúc đó vị trí ký tự suy ra từ mốc còn lệch hơn
        # cả chia theo tỉ lệ, nên trả None để phần gọi dùng cách cũ.
        if len(marks) < 2 * n_parts:
            return None

        total_w = float(sum(max(0.0, float(w)) for w in weights)) or 1.0
        n = len(marks)

        # Vị trí ký tự ước lượng theo trọng số nội dung...
        raw: List[int] = []
        acc = 0.0
        for k, w in enumerate(weights[:-1]):
            acc += max(0.0, float(w))
            idx = int(round(acc / total_w * n))
            raw.append(max(k + 1, min(n - (n_parts - 1 - k), idx)))

        # ...rồi hút về quãng nghỉ gần nhất, không vượt nửa đường tới chỗ cắt bên cạnh.
        cuts: List[int] = []
        for k, idx in enumerate(raw):
            lo = (cuts[-1] + 1) if cuts else 1
            hi = n - (n_parts - 1 - k)
            truoc = raw[k - 1] if k else 0
            sau = raw[k + 1] if k + 1 < len(raw) else n
            radius = max(1, min(3, (min(idx - truoc, sau - idx)) // 2))
            idx = max(lo, min(hi, idx))
            cuts.append(self._snap_to_pause(marks, idx, lo, hi, radius=radius))

        bounds: List[Tuple[float, float]] = []
        lo_idx = 0
        for k in range(n_parts):
            hi_idx = cuts[k] if k < len(cuts) else n
            part_start = float(start) if k == 0 else marks[lo_idx][0]
            part_end = float(end) if k == n_parts - 1 else marks[hi_idx - 1][1]
            if part_end - part_start < min_part:
                part_end = part_start + min_part
            bounds.append((part_start, part_end))
            lo_idx = hi_idx

        # Bảo đảm tăng dần và không tràn khỏi ô gốc.
        fixed: List[Tuple[float, float]] = []
        cursor = float(start)
        for i, (a, b) in enumerate(bounds):
            a = max(a, cursor)
            b = max(b, a + min_part)
            if i == n_parts - 1:
                b = max(float(end), a + min_part)
            fixed.append((a, b))
            cursor = a + min_part / 2.0 if b <= a else b
        return fixed

    # ------------------------------------------------------------- lưu / đọc #
    def to_dict(self) -> dict:
        return {"phien_ban": 1,
                "moc": [[round(a, 3), round(b, 3)] for a, b in self.marks]}

    def save(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False)
            return True
        except OSError:
            return False

    @classmethod
    def from_dict(cls, data) -> "SpeechMap":
        if isinstance(data, dict):
            marks = data.get("moc") or data.get("marks") or []
        else:
            marks = data or []
        return cls(marks)

    @classmethod
    def load(cls, path: str) -> Optional["SpeechMap"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                m = cls.from_dict(json.load(f))
        except (OSError, ValueError):
            return None
        return m if not m.empty else None


# --------------------------------------------------------------------------- #
#  Bản đồ ĐANG DÙNG cho lần chạy hiện tại
#
#  Các hàm chia lại phụ đề nằm rải rác ở srt_utils/asr/tts và được gọi từ 4 chỗ
#  khác nhau (CLI, GUI, bước TTS, bước render). Truyền tay bản đồ qua từng chữ
#  ký hàm sẽ phải sửa hàng chục call site và rất dễ bỏ sót một chỗ - mà bỏ sót
#  một chỗ là lại trôi. Vì mỗi lần chạy chỉ xử lý MỘT video (GUI có lock
#  STATE["running"]), một bản đồ dùng chung là đủ và an toàn.
# --------------------------------------------------------------------------- #
_ACTIVE: Optional[SpeechMap] = None

# Công tắc tắt bản đồ để so sánh trước/sau (dùng khi đo kiểm thử):
#   AUTODUB_TAT_BAN_DO_THOAI=1 -> quay về cách chia theo tỉ lệ ký tự.
_ENV_OFF = "AUTODUB_TAT_BAN_DO_THOAI"


def disabled() -> bool:
    return str(os.environ.get(_ENV_OFF, "")).strip().lower() in ("1", "true", "yes")


def set_active(m: Optional[SpeechMap]) -> Optional[SpeechMap]:
    global _ACTIVE
    _ACTIVE = m if (m is not None and not m.empty) else None
    return _ACTIVE


def get_active() -> Optional[SpeechMap]:
    return None if disabled() else _ACTIVE


def clear_active() -> None:
    global _ACTIVE
    _ACTIVE = None


def slice_window(start: float, end: float, weights: Sequence[float],
                 min_part: float = 0.12
                 ) -> Optional[List[Tuple[float, float]]]:
    """Chia ô thời gian theo bản đồ đang dùng. None = chưa có bản đồ."""
    m = get_active()
    if m is None or m.empty:
        return None
    return m.slice_window(start, end, weights, min_part=min_part)


def onset(t: float, tol: float = 0.35) -> Optional[float]:
    m = get_active()
    if m is None or m.empty:
        return None
    return m.onset(t, tol=tol)


def default_path(out_dir: str, stem: str) -> str:
    return os.path.join(out_dir, f"{stem}.ban_do_thoai.json")


def load_active(path: str) -> Optional[SpeechMap]:
    """Đọc bản đồ từ file rồi đặt làm bản đồ đang dùng."""
    m = SpeechMap.load(path) if path and os.path.exists(path) else None
    return set_active(m)

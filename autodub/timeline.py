"""Thuật toán CHỐNG ĐÈ THOẠI (anti-overlap) - phần "chuyên nghiệp" nhất.

Vấn đề: khi lồng tiếng, câu tiếng Việt thường DÀI hơn câu gốc, và timestamp SRT
của video dài rất dễ sát/trùng nhau. Nếu cứ phát đúng mốc thời gian thì giọng
nhân vật A chưa dứt đã bị giọng nhân vật B đè lên -> nghe rối.

Giải pháp (fit-to-slot + cascade + phục hồi trôi):
  1. Duyệt các câu theo thứ tự thời gian, giữ một con trỏ `cursor` = thời điểm
     sớm nhất mà câu kế tiếp được phép bắt đầu (đuôi câu trước + khoảng nghỉ).
  2. Mỗi câu đặt tại max(mốc gốc, cursor) -> không bao giờ bắt đầu sớm hơn dự kiến
     và không bao giờ chồng lên câu trước.
  3. "Ô trống" (slot) = khoảng từ chỗ đặt đến khi câu SAU bắt đầu (theo mốc gốc).
     Nếu audio TTS dài hơn ô trống, TĂNG TỐC nói (atempo) vừa đủ để lọt, nhưng
     không vượt `max_speed` (giữ tự nhiên). Nếu vẫn dài -> chấp nhận trôi nhẹ và
     đẩy các câu sau (cascade) thay vì để đè nhau.
  4. PHỤC HỒI TRÔI (recover_drift): nếu đang trễ so với video (từ bước 3 của
     các câu trước) VÀ câu hiện tại còn "dư hơi" (nat < slot), tăng tốc THÊM
     một chút (vẫn không vượt max_speed) để RÚT NGẮN độ trễ ngay, thay vì giữ
     nguyên mức trễ tới tận khi gặp một khoảng nghỉ dài tự nhiên. Đây là phần
     xử lý cho phản hồi "thoại tụt lại phía sau, một số đoạn ngắn thì khớp" -
     trước đây thuật toán chỉ tránh KHÔNG trễ THÊM chứ không chủ động bù lại.

Module này thuần logic (không phụ thuộc thư viện audio) để dễ test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass
class Placement:
    index: int
    placed_start: float     # thời điểm bắt đầu thực tế (giây)
    speed: float            # hệ số tăng tốc áp lên audio (>=1.0)
    natural_dur: float      # độ dài audio TTS gốc (giây)
    final_dur: float        # độ dài sau khi tăng tốc = natural_dur / speed
    drift: float            # placed_start - start_gốc (đo độ lệch so với video)
    trimmed: bool = False   # True nếu clip phải cắt để không kéo câu sau lệch hình

    @property
    def placed_end(self) -> float:
        return self.placed_start + self.final_dur


def fit_segments(
    starts: Sequence[float],
    natural_durations: Sequence[float],
    max_speed: float = 1.6,
    min_gap: float = 0.08,
    total_duration: Optional[float] = None,
    recover_drift: bool = True,
    base_speed: float = 1.0,
) -> List[Placement]:
    """Sắp lịch phát cho từng câu sao cho KHÔNG câu nào chồng lên câu nào.

    starts             : mốc bắt đầu gốc của từng câu (giây), đã sort tăng dần.
    natural_durations  : độ dài audio TTS tự nhiên tương ứng (giây).
    max_speed          : trần tăng tốc (1.6 = nhanh tối đa 60%). Không vượt để giữ nghe rõ.
    min_gap            : khoảng nghỉ tối thiểu giữa 2 câu (giây).
    total_duration     : độ dài video (để tính ô trống cho câu cuối). Có thể None.
    recover_drift      : True = khi đang trễ VÀ câu hiện tại còn dư hơi, tăng tốc
                         thêm (vẫn trong max_speed) để rút ngắn độ trễ ngay, thay
                         vì chỉ tránh trễ thêm và chờ khoảng nghỉ tự nhiên mới hết trễ.
    """
    n = len(starts)
    if n != len(natural_durations):
        raise ValueError("starts và natural_durations phải cùng độ dài")
    max_speed = max(1.0, float(max_speed))
    base_speed = min(max_speed, max(1.0, float(base_speed)))

    placements: List[Placement] = []
    cursor = 0.0
    for i in range(n):
        start = float(starts[i])
        nat = max(0.0, float(natural_durations[i]))

        drift_before = max(0.0, cursor - start)  # đang trễ bao nhiêu TRƯỚC câu này
        placed_start = max(start, cursor)

        # Mốc bắt đầu của câu kế tiếp (theo thời gian GỐC) để xác định ô trống.
        if i + 1 < n:
            next_start = float(starts[i + 1])
        elif total_duration is not None:
            next_start = float(total_duration)
        else:
            # Không có ràng buộc -> để NGUYÊN tốc độ. Phải cộng cả min_gap để
            # slot = nat (nếu chỉ +nat thì slot = nat - min_gap < nat, khiến câu
            # cuối bị coi là "chật" rồi tăng tốc vô cớ, trái với chú thích).
            next_start = placed_start + nat + min_gap

        slot = next_start - placed_start - min_gap

        speed = base_speed
        if nat > slot and slot > 1e-3:
            speed = min(max_speed, max(base_speed, nat / slot))
        elif slot <= 1e-3:
            # Không còn chỗ (câu sau tới quá sát) -> nói nhanh tối đa, đành trôi.
            speed = max_speed

        if recover_drift and drift_before > 0.05 and nat > 1e-3:
            # Đang trễ + câu này còn dư hơi -> tăng thêm tốc độ để BÙ LẠI độ trễ
            # (không chỉ tránh trễ thêm như trước), miễn không vượt max_speed.
            target_final = max(nat / max_speed, nat - drift_before)
            speed_recover = nat / target_final if target_final > 1e-3 else max_speed
            speed = min(max_speed, max(speed, speed_recover))

        final_dur = nat / speed if speed > 0 else nat
        placements.append(
            Placement(
                index=i,
                placed_start=placed_start,
                speed=round(speed, 4),
                natural_dur=nat,
                final_dur=final_dur,
                drift=placed_start - start,
            )
        )
        cursor = placed_start + final_dur + min_gap

    return placements


def fit_segments_strict(
    starts: Sequence[float],
    natural_durations: Sequence[float],
    max_speed: float = 1.6,
    min_gap: float = 0.04,
    total_duration: Optional[float] = None,
    trim_overflow: bool = True,
    base_speed: float = 1.0,
    ends: Optional[Sequence[float]] = None,
    max_overhang: float = 0.75,
) -> List[Placement]:
    """Xếp lịch bám mốc gốc từng câu, không cascade drift sang câu sau.

    Chế độ này ưu tiên đồng bộ hình/sub: mỗi câu bắt đầu đúng timestamp gốc
    (cộng offset nếu có). Khi có `ends`, cửa sổ của mỗi clip luôn dài ít nhất
    đến `end` của câu gốc và vẫn được mượn khoảng im lặng trước câu kế tiếp.
    Vì vậy hai câu vốn chồng nhau (thường là hai nhân vật) được phép chồng như
    bản gốc, thay vì ép câu trước chạy nhanh/cắt đuôi chỉ vì nhân vật kế tiếp
    bắt đầu nói. Nếu không có `ends`, hàm giữ hành vi cũ.

    `max_overhang` GIỚI HẠN phần im lặng được mượn: clip chỉ được chạy quá `end`
    gốc tối đa bấy nhiêu giây. Trước đây phần mượn là VÔ HẠN - slot kéo tới tận
    mốc bắt đầu của câu kế tiếp, nên một câu sub dài 3s mà sau nó là 50s im lặng
    sẽ nhận slot 50s: TTS đọc 25s vẫn "vừa slot" nên không tăng tốc, không cắt,
    và giọng đọc đè lên hình suốt 22s sau khi phụ đề đã tắt. Đặt 0 = bám chặt
    `end` gốc (không mượn gì).
    """
    n = len(starts)
    if n != len(natural_durations):
        raise ValueError("starts và natural_durations phải cùng độ dài")
    if ends is not None and n != len(ends):
        raise ValueError("starts và ends phải cùng độ dài")
    max_speed = max(1.0, float(max_speed))
    base_speed = min(max_speed, max(1.0, float(base_speed)))
    min_gap = max(0.0, float(min_gap or 0.0))
    try:
        max_overhang = max(0.0, float(max_overhang))
    except (TypeError, ValueError):
        max_overhang = 0.0

    placements: List[Placement] = []
    for i in range(n):
        start = float(starts[i])
        nat = max(0.0, float(natural_durations[i]))
        if ends is not None:
            own_end = float(ends[i])
            # Trần cứng: không bao giờ đọc quá `end` gốc quá `max_overhang` giây,
            # dù phía sau có bao nhiêu im lặng đi nữa.
            hard_cap = own_end + max_overhang
            available_end = own_end
            if i + 1 < n:
                available_end = max(
                    available_end,
                    min(float(starts[i + 1]) - min_gap, hard_cap))
            elif total_duration is not None:
                available_end = max(
                    available_end,
                    min(float(total_duration) - min_gap, hard_cap))
            slot = max(0.01, available_end - start)
        elif i + 1 < n:
            next_start = float(starts[i + 1])
            slot = max(0.01, next_start - start - min_gap)
        elif total_duration is not None:
            next_start = float(total_duration)
            slot = max(0.01, next_start - start - min_gap)
        else:
            next_start = start + nat / base_speed + min_gap
            slot = max(0.01, next_start - start - min_gap)
        speed = base_speed
        if nat > slot and slot > 1e-3:
            speed = min(max_speed, max(base_speed, nat / slot))
        elif slot <= 1e-3:
            speed = max_speed

        final_dur = nat / speed if speed > 0 else nat
        trimmed = False
        # Ignore floating-point dust when speed was calculated exactly to the
        # slot; otherwise an equal-length clip is incorrectly marked/processed
        # as trimmed by a few femtoseconds.
        if trim_overflow and final_dur > slot + 1e-6:
            final_dur = slot
            trimmed = True

        placements.append(
            Placement(
                index=i,
                placed_start=start,
                speed=round(speed, 4),
                natural_dur=nat,
                final_dur=final_dur,
                drift=0.0,
                trimmed=trimmed,
            )
        )

    return placements


def auto_fit(
    starts: Sequence[float],
    natural_durations: Sequence[float],
    max_speed: float = 1.6,
    min_gap: float = 0.08,
    total_duration: Optional[float] = None,
    recover_drift: bool = True,
    target_drift: float = 0.6,
) -> Tuple[List[Placement], float]:
    """Tìm TỐC ĐỘ NỀN nhỏ nhất đủ để cả đoạn không bị trôi, rồi xếp lịch.

    VÌ SAO CẦN: câu tiếng Việt dịch từ tiếng Trung dài hơn hẳn bản gốc. Nếu chỉ
    tăng tốc những câu bị chật, ta được một bản lồng tiếng "giật cục" - câu thì
    thong thả, câu thì nhanh 1.6x - MÀ VẪN trôi, vì tổng thời lượng giọng nói
    lớn hơn thời lượng video (đo được: trôi 79 giây trên video 5.6 phút).

    Cách đúng: chia đều phần dôi ra cho TẤT CẢ các câu. Nói nhanh hơn 12% đều
    đặn thì gần như không nhận ra; nói 1.0x rồi đột ngột 1.6x thì nghe rất rõ.
    Tìm hệ số nền bằng chia đôi khoảng - fit_segments rất nhẹ nên vô tư.
    """
    def _try(base: float):
        pl = fit_segments(starts, natural_durations, max_speed=max_speed,
                          min_gap=min_gap, total_duration=total_duration,
                          recover_drift=recover_drift, base_speed=base)
        worst = max((p.drift for p in pl), default=0.0)
        return pl, worst

    pl, worst = _try(1.0)
    if worst <= target_drift:
        return pl, 1.0

    lo, hi = 1.0, max_speed
    pl_hi, worst_hi = _try(hi)
    # Nếu ngay cả tốc độ trần cũng không hết trôi thì đừng ép mọi câu lên trần:
    # nhắm mức trôi tốt nhất có thể, rồi tìm tốc độ NHỎ NHẤT vẫn đạt mức đó -
    # nói nhanh thêm nữa chỉ làm chói tai chứ không cứu được gì.
    goal = target_drift if worst_hi <= target_drift else worst_hi + 0.05
    best_pl, best = pl_hi, hi
    for _ in range(20):                       # chia đôi 20 lần là quá đủ
        mid = (lo + hi) / 2.0
        pl_mid, worst_mid = _try(mid)
        if worst_mid <= goal:
            best_pl, best, hi = pl_mid, mid, mid
        else:
            lo = mid
    return best_pl, best


def summarize(placements: List[Placement]) -> dict:
    """Vài chỉ số để log ra cho người dùng biết mức độ ép/tăng tốc."""
    if not placements:
        return {"count": 0}
    sped = [p for p in placements if p.speed > 1.001]
    max_drift = max((p.drift for p in placements), default=0.0)
    return {
        "count": len(placements),
        "sped_up": len(sped),
        "trimmed": sum(1 for p in placements if getattr(p, "trimmed", False)),
        "max_speed": round(max((p.speed for p in placements), default=1.0), 3),
        "max_drift_s": round(max_drift, 2),
        "total_end_s": round(placements[-1].placed_end, 2),
    }

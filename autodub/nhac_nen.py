"""Nhạc nền cho video kể chuyện.

Lo ba việc: tìm và tải nhạc CC0/Public Domain về máy, chuẩn hoá mức âm để bài
nào cũng nhỏ đều nhau, rồi trộn xuống dưới giọng đọc kèm ducking (nhạc tự lùi
lại mỗi khi có lời).

Vì sao phải chuẩn hoá: mốc "-38 dB" chỉ có nghĩa khi biết nhạc gốc to bao
nhiêu. Cùng một lệnh giảm 38 dB, bài thu to sẽ vẫn nghe rõ còn bài thu nhỏ thì
mất hút. Nên ở đây đo mức trung bình của từng bài trước rồi mới bù đúng lượng
cần thiết, để bài nào cũng nằm đúng mốc người dùng đặt.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .utils import log, run, ffprobe_duration

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AutoDubVN/1.0 (nhac nen CC0; lien he qua kho ma nguon)"

# Chỉ nhận giấy phép cho phép dùng thoải mái, không cần xin phép. Vẫn ghi lại
# nguồn và tên tác giả trong nguon.json để người dùng ghi công nếu muốn.
_LICENSE_OK = ("cc0", "public domain", "publicdomain", "pd-", "cc-zero")

# Nhạc khí, không lời, hợp làm nền cho giọng kể.
DEFAULT_CATEGORIES = (
    "Ambient music",
    "Musopen",
    "Piano music",
    "Classical music",
)

# File quá nhỏ thường là mẫu vài giây, quá lớn thường là bản thu WAV cả trăm MB.
MIN_SIZE_MB = 0.4
MAX_SIZE_MB = 40.0
MIN_DURATION = 25.0

TARGET_DB = -38.0
DB_FLOOR = -60.0
DB_CEIL = -10.0


def thu_muc_nhac(root: Optional[str] = None) -> str:
    """Thư mục chứa nhạc nền. Người dùng bỏ file của mình vào đây cũng được."""
    base = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", "nhac_nen")
    os.makedirs(path, exist_ok=True)
    return path


_AUDIO_EXT = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4a", ".aac"}


def liet_ke_nhac_co_san(folder: Optional[str] = None) -> List[Dict]:
    """Danh sách nhạc đã có trong máy, kèm nguồn nếu biết."""
    folder = folder or thu_muc_nhac()
    sources = _doc_nguon(folder)
    out: List[Dict] = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in _AUDIO_EXT:
            continue
        info = dict(sources.get(name) or {})
        info.update({"ten": name, "duong_dan": path,
                     "dung_luong_mb": round(os.path.getsize(path) / 1048576, 1)})
        out.append(info)
    return out


def _duong_dan_nguon(folder: str) -> str:
    return os.path.join(folder, "nguon.json")


def _doc_nguon(folder: str) -> Dict[str, Dict]:
    try:
        with open(_duong_dan_nguon(folder), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _ghi_nguon(folder: str, name: str, info: Dict) -> None:
    data = _doc_nguon(folder)
    data[name] = info
    try:
        with open(_duong_dan_nguon(folder), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"Không ghi được nguon.json: {e}", "warn")


def _giay_phep_hop_le(text: str) -> bool:
    low = (text or "").strip().lower()
    return bool(low) and any(k in low for k in _LICENSE_OK)


# Kho nhạc cổ điển hay kèm thư viện mẫu nhạc cụ rời ("1st violin B 03.wav"):
# đúng giấy phép, đúng định dạng, nhưng chỉ là vài nốt đàn nên không làm nền được.
_MAU_NHAC_CU_RE = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)\s+)?"
    r"(?:violin|viola|cello|contrabass|flute|oboe|clarinet|bassoon|trumpet|"
    r"trombone|horn|tuba|harp|timpani|piano)\s+[A-G]#?b?\s*\d+\b",
    re.IGNORECASE)


def _la_mau_nhac_cu(title: str) -> bool:
    return bool(_MAU_NHAC_CU_RE.search(title or ""))


def _goi_api(params: Dict, timeout: float = 20.0) -> Dict:
    url = WIKIMEDIA_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _danh_muc_con(cat: str, limit: int = 8, timeout: float = 20.0) -> List[str]:
    """Danh mục con của một danh mục. Nhiều danh mục lớn chỉ chứa danh mục con
    chứ không chứa file nào trực tiếp, nên phải bước xuống một cấp."""
    try:
        data = _goi_api({
            "action": "query", "format": "json",
            "list": "categorymembers",
            "cmtitle": f"Category:{cat}",
            "cmtype": "subcat", "cmlimit": limit,
        }, timeout=timeout)
    except Exception:
        return []
    return [str(m.get("title") or "").removeprefix("Category:")
            for m in ((data.get("query") or {}).get("categorymembers") or [])]


def tim_nhac_online(categories: Optional[List[str]] = None,
                    limit: int = 12,
                    timeout: float = 20.0,
                    dao_sau: bool = True) -> List[Dict]:
    """Hỏi Wikimedia Commons xem có bài nhạc CC0/PD nào dùng được.

    Không ghim sẵn đường dẫn bài hát nào cả: đường dẫn ghim cứng sẽ chết dần
    theo thời gian, còn hỏi thẳng danh mục thì luôn ra bản đang có thật.
    """
    cats = list(categories or DEFAULT_CATEGORIES)
    found: List[Dict] = []
    seen = set()
    da_xet = set()
    while cats and len(found) < limit:
        cat = cats.pop(0)
        if cat in da_xet:
            continue
        da_xet.add(cat)
        try:
            data = _goi_api({
                "action": "query", "format": "json",
                "generator": "categorymembers",
                "gcmtitle": f"Category:{cat}",
                "gcmtype": "file", "gcmlimit": 40,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
            }, timeout=timeout)
        except Exception as e:
            log(f"Không hỏi được danh mục nhạc \"{cat}\": {e}", "warn")
            continue

        truoc = len(found)
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime") or "")
            url = str(info.get("url") or "")
            if not mime.startswith("audio") or not url or url in seen:
                continue
            size_mb = float(info.get("size") or 0) / 1048576
            if not (MIN_SIZE_MB <= size_mb <= MAX_SIZE_MB):
                continue
            meta = info.get("extmetadata") or {}
            lic = str((meta.get("LicenseShortName") or {}).get("value") or "")
            if not _giay_phep_hop_le(lic):
                continue
            if _la_mau_nhac_cu(str(page.get("title") or "")):
                continue
            seen.add(url)
            artist = re.sub(r"<[^>]+>", "",
                            str((meta.get("Artist") or {}).get("value") or "")).strip()
            found.append({
                "ten": str(page.get("title") or "").removeprefix("File:"),
                "url": url,
                "giay_phep": lic,
                "tac_gia": artist or "không rõ",
                "danh_muc": cat,
                "dung_luong_mb": round(size_mb, 1),
                "trang": "https://commons.wikimedia.org/wiki/"
                         + urllib.parse.quote(str(page.get("title") or "")),
            })
            if len(found) >= limit:
                break
        if len(found) == truoc and dao_sau:
            cats.extend(_danh_muc_con(cat, timeout=timeout))
    return found


def _ten_file_an_toan(name: str) -> str:
    safe = re.sub(r"[^\w\s.\-]", "", name, flags=re.UNICODE).strip()
    safe = re.sub(r"\s+", "_", safe)
    return safe[:80] or f"nhac_{int(time.time())}"


def tai_nhac(item: Dict, folder: Optional[str] = None,
             timeout: float = 120.0) -> Optional[str]:
    """Tải một bài về thư mục nhạc. Trả về đường dẫn, hoặc None nếu hỏng."""
    folder = folder or thu_muc_nhac()
    name = _ten_file_an_toan(item.get("ten") or "")
    dest = os.path.join(folder, name)
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return dest

    tmp = dest + ".part"
    try:
        req = urllib.request.Request(item["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(tmp, "wb") as f:
            while True:
                buf = resp.read(262144)
                if not buf:
                    break
                f.write(buf)
        os.replace(tmp, dest)
    except Exception as e:
        log(f"Tải nhạc \"{item.get('ten')}\" lỗi: {e}", "warn")
        try:
            os.path.exists(tmp) and os.remove(tmp)
        except OSError:
            pass
        return None

    if ffprobe_duration(dest) < MIN_DURATION:
        log(f"Bỏ \"{name}\": ngắn hơn {MIN_DURATION:.0f} giây, không đủ làm nền.", "info")
        try:
            os.remove(dest)
        except OSError:
            pass
        return None

    _ghi_nguon(folder, name, {k: item.get(k) for k in
                              ("giay_phep", "tac_gia", "danh_muc", "trang", "url")})
    log(f"Đã tải nhạc nền: {name} ({item.get('giay_phep')})", "ok")
    return dest


def dam_bao_co_nhac(so_bai: int = 3, folder: Optional[str] = None,
                    categories: Optional[List[str]] = None,
                    cho_phep_tai: bool = True) -> List[Dict]:
    """Bảo đảm trong máy có ít nhất `so_bai` bài để chọn.

    Có sẵn thì dùng luôn, không có mới tải. Mạng hỏng cũng không làm gãy việc
    dựng video: chỉ trả về những gì đang có.
    """
    folder = folder or thu_muc_nhac()
    có = liet_ke_nhac_co_san(folder)
    if len(có) >= so_bai or not cho_phep_tai:
        return có

    log(f"Đang tìm nhạc nền CC0/Public Domain (cần thêm {so_bai - len(có)} bài)…", "step")
    for item in tim_nhac_online(categories, limit=so_bai * 3):
        if len(liet_ke_nhac_co_san(folder)) >= so_bai:
            break
        tai_nhac(item, folder)
    return liet_ke_nhac_co_san(folder)


def do_muc_am(path: str) -> Optional[float]:
    """Mức âm trung bình của file, tính bằng dBFS. None nếu không đo được."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", path,
             "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
            errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Không đo được âm lượng nhạc nền: {e}", "warn")
        return None
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB",
                  (proc.stderr or "") + (proc.stdout or ""))
    return float(m.group(1)) if m else None


def _gain_can_bu(music_path: str, muc_dich_db: float) -> float:
    """Cần cộng thêm bao nhiêu dB để bài này về đúng mốc mong muốn."""
    mean = do_muc_am(music_path)
    if mean is None:
        # Không đo được thì coi như bài đã ở mức thu thông thường (-18 dBFS).
        mean = -18.0
    return muc_dich_db - mean


def chon_nhac(folder: Optional[str] = None,
              uu_tien: str = "",
              seed: Optional[int] = None) -> Optional[str]:
    """Chọn một bài. `uu_tien` là tên file hoặc đường dẫn người dùng chỉ định."""
    folder = folder or thu_muc_nhac()
    uu_tien = (uu_tien or "").strip().strip('"')
    if uu_tien:
        if os.path.isfile(uu_tien):
            return os.path.abspath(uu_tien)
        cand = os.path.join(folder, uu_tien)
        if os.path.isfile(cand):
            return cand
        log(f"Không thấy nhạc \"{uu_tien}\", sẽ chọn bài khác.", "warn")
    có = liet_ke_nhac_co_san(folder)
    if not có:
        return None
    rng = random.Random(seed)
    return rng.choice(có)["duong_dan"]


def _lam_tron_db(value: float) -> float:
    return max(DB_FLOOR, min(DB_CEIL, float(value)))


def build_filter(gain_db: float, duration: float, duck: bool,
                 fade: float = 2.0,
                 duck_ratio: float = 8.0,
                 nguong_duck: float = 0.02,
                 attack: float = 20.0,
                 release: float = 500.0) -> str:
    """Dựng chuỗi filter ffmpeg trộn giọng đọc với nhạc nền.

    Tách riêng khỏi hàm chạy để test được mà không cần gọi ffmpeg thật.
    """
    fade = max(0.0, float(fade or 0.0))
    fade_out_at = max(0.0, float(duration) - fade)
    music = [f"volume={gain_db:.2f}dB"]
    if fade > 0:
        music.append(f"afade=t=in:st=0:d={fade:.2f}")
        music.append(f"afade=t=out:st={fade_out_at:.2f}:d={fade:.2f}")
    music.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    music_chain = ",".join(music)

    parts = [f"[1:a]{music_chain}[m]",
             "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:"
             "channel_layouts=stereo[v]"]
    if duck:
        # Giọng đọc vừa là tiếng chính vừa là tín hiệu điều khiển, nên phải nhân
        # đôi luồng: một bản đem trộn, một bản đẩy vào sidechain.
        parts.append("[v]asplit=2[vmix][vkey]")
        # Mức nhạc lùi được bao nhiêu dB = (giọng vượt ngưỡng bao nhiêu dB)
        # × (1 − 1/ratio). Nên ratio 8 với giọng thường sẽ hạ nhạc chừng 10-12 dB.
        ratio = max(1.0, min(20.0, float(duck_ratio)))
        parts.append(
            f"[m][vkey]sidechaincompress=threshold={nguong_duck}:"
            f"ratio={ratio:.1f}:attack={attack:.0f}:release={release:.0f}"
            f":makeup=1[mduck]")
        voice_label, music_label = "[vmix]", "[mduck]"
    else:
        voice_label, music_label = "[v]", "[m]"
    # normalize=0 rất quan trọng: mặc định amix chia đều biên độ, giọng đọc sẽ
    # bị tụt đúng một nửa chỉ vì có thêm nhạc.
    parts.append(f"{voice_label}{music_label}amix=inputs=2:duration=first:"
                 "dropout_transition=0:normalize=0[out]")
    return ";".join(parts)


def tron_nhac_nen(voice_path: str, out_path: str,
                  music_path: Optional[str] = None,
                  muc_db: float = TARGET_DB,
                  duck: bool = True,
                  duck_ratio: float = 8.0,
                  fade: float = 2.0,
                  folder: Optional[str] = None,
                  seed: Optional[int] = None) -> Dict:
    """Trộn nhạc nền xuống dưới giọng đọc.

    `muc_db` là mức nhạc muốn nghe thấy, tính theo dBFS trung bình (mặc định
    -38, tức rất nhỏ, chỉ đủ lấp khoảng lặng). Trả về mô tả việc đã làm; nếu
    không có nhạc thì chép nguyên giọng đọc và nói rõ lý do.
    """
    if not voice_path or not os.path.isfile(voice_path):
        raise ValueError("Chưa có file giọng đọc để trộn nhạc nền.")

    music_path = chon_nhac(folder, music_path or "", seed=seed)
    duration = ffprobe_duration(voice_path)
    if duration <= 0:
        raise ValueError("Không đọc được độ dài giọng đọc.")

    if not music_path:
        log("Không có nhạc nền nào trong máy, giữ nguyên giọng đọc.", "warn")
        return {"path": voice_path, "music": "", "duration": duration,
                "ly_do": "chưa có nhạc nền"}

    muc_db = _lam_tron_db(muc_db)
    gain = _gain_can_bu(music_path, muc_db)
    filt = build_filter(gain, duration, duck, fade=fade,
                        duck_ratio=duck_ratio)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin",
        "-i", voice_path,
        # Lặp vô hạn rồi cắt theo độ dài giọng đọc, khỏi phải tính bài dài bao nhiêu.
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex", filt,
        "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        out_path,
    ]
    run(cmd, check=True, quiet=True)
    if not os.path.exists(out_path) or ffprobe_duration(out_path) <= 0:
        raise RuntimeError("Trộn nhạc nền xong nhưng file kết quả rỗng.")

    log(f"Đã trộn nhạc nền \"{os.path.basename(music_path)}\" ở mức {muc_db:.0f} dB"
        + (" (có ducking)" if duck else ""), "ok")
    return {"path": out_path, "music": music_path, "duration": duration,
            "muc_db": muc_db, "gain_db": round(gain, 2), "duck": bool(duck)}

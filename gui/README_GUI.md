# AutoDubVN — App desktop

Giao diện desktop cho AutoDubVN: **nhấn là mở một cửa sổ app riêng** (dùng
WebView2 của Windows), **không phải trình duyệt** (không tab, không thanh địa
chỉ). Bên trong vẫn chạy đúng pipeline `python main.py <video>` như cũ, nhưng có
nút bấm, ô cấu hình, và log chạy hiện trực tiếp.

## Chạy nhanh (khuyến nghị)

Double-click **`run_gui.bat`** ở thư mục gốc AutoDubVN.

Lần đầu nó tự cài `pywebview` (+ `pyyaml`) vào venv rồi mở app. Các lần sau mở
thẳng. Muốn tạo shortcut ngoài Desktop: chuột phải `run_gui.bat` → *Send to* →
*Desktop (create shortcut)*, đổi tên/icon tùy thích.

Yêu cầu: Windows 10/11 (đã có sẵn **WebView2 Runtime**; nếu máy quá cũ thiếu nó,
tải "Evergreen WebView2 Runtime" miễn phí của Microsoft).

## Đóng gói thành .exe (tùy chọn)

Double-click **`gui\build_exe.bat`** (hoặc `venv\Scripts\pyinstaller gui\autodubvn_gui.spec`).
Kết quả: `dist\AutoDubVN\AutoDubVN.exe`.

`.exe` này là **lớp vỏ giao diện gọn nhẹ**: phần lồng tiếng nặng (torch/funasr/
edge-tts…) vẫn chạy bằng Python trong **venv** của project — nên `.exe` nhỏ, build
nhanh, không phải nhồi vài GB thư viện + model vào một file. Vì vậy hãy đặt
`AutoDubVN.exe` (hoặc cả thư mục `dist\AutoDubVN`) **cạnh `main.py` và thư mục
`venv`** thì nó mới tìm thấy pipeline.

## Cấu trúc (để thay giao diện theo mẫu)

```
gui/
├─ app.py            ← khung cửa sổ + cầu nối Python↔JS (KHÔNG cần sửa khi đổi skin)
├─ web/
│  ├─ index.html     ← bố cục giao diện   ┐  thay 3 file này là đổi toàn bộ
│  ├─ style.css      ← diện mạo (skin)     ├─ diện mạo theo bản thiết kế mẫu
│  └─ app.js         ← logic nút bấm/log   ┘
├─ autodubvn_gui.spec, build_exe.bat, requirements-gui.txt
```

Cầu nối JS→Python (giữ nguyên tên khi ráp thiết kế mẫu vào):

| Gọi từ JS | Việc |
|---|---|
| `pywebview.api.get_config()` | đọc `config.yaml` → đổ lên form |
| `pywebview.api.save_config({ "asr.backend": "...", ... })` | lưu (giữ chú thích) |
| `pywebview.api.browse_video()` | hộp thoại chọn file native |
| `pywebview.api.start(pathOrLink)` | chạy lồng tiếng |
| `pywebview.api.stop()` | dừng |
| `pywebview.api.open_file(path)` / `open_folder(path)` | mở kết quả |

Python→JS (app tự gọi khi có sự kiện): `window.onLog({line})`,
`window.onState({running})`, `window.onResult({path})`, `window.onDone({code})`.

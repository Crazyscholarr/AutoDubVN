# AutoDubVN

Lồng tiếng Việt cho video, dựng video kể chuyện và quản lý ý tưởng nội dung trên máy local. / Local Vietnamese dubbing, narrated videos and content planning.

[Tiếng Việt](#tieng-viet) | [English](#english)

**2026-09-14:** [Dịch theo câu, glossary và TTS theo nhóm / Sentence-first translation, glossary and grouped TTS](docs/SEMANTIC_PIPELINE.md). Có thay đổi mặc định và giới hạn kiểm chứng live; xem tài liệu trước khi dịch lại video dài. / Defaults have changed; read the live-validation limits before retranslating a long video.

<a id="tieng-viet"></a>
## Tiếng Việt

### Phạm vi và tính năng

Ứng dụng dùng các package trong `autodub/` và giao diện `ui/`. Bộ kiểm thử gồm unit, HTTP/UI và E2E CLI/GUI với media thật; phản hồi ASR/dịch/TTS được mock. Chất lượng dịch thực tế phụ thuộc nguồn và provider.

- **Lồng tiếng:** file/URL, hàng đợi, ASR, sửa phụ đề, dịch theo lô, reflow/beautify, TTS, đồng bộ và render.
- **Download:** yt-dlp, Bilibili DASH; xử lý link/cookie cho YouTube, Douyin, TikTok, Kuaishou, Ixigua. Live phụ thuộc nguồn/quyền truy cập.
- **ASR:** `paraformer`, `sensevoice`, `faster-whisper`, `whisperx`; cứu vùng thoại thiếu, lọc hallucination, sửa từ, screen cues, speech map; diarization pyannote tùy chọn.
- **Dịch:** Gemini browser/API, TokenRouter, TokenRouter Gemini, InferX, NVIDIA, ZenMux, TokenHarbor; context, gợi ý nhân vật, cache lô và retry/fallback.
- **TTS/media:** Edge, CapCut, VieNeu; narrator/alternate/per-speaker, rate/pitch, trim/fitting; trộn âm gốc, nhạc/ducking kể chuyện, blur/logo/ASS hardsub; NVENC/CPU, stream copy và GUI render theo mảnh.
- **Video kể chuyện:** TXT/MD, công cụ kịch bản ngoài, ảnh/video nguồn, ảnh AI, slideshow, giọng nhân vật, metadata/thumbnail/mô tả YouTube. Chưa xác minh upload YouTube tự động.
- **Kho ý tưởng:** tìm/nhập JSON/SQLite, AI hoặc heuristic offline, lưu tiến độ, xuất Excel/JSON, chuyển sang kể chuyện.
- **Công cụ Video:** tìm/tải/cắt hàng loạt; progress/cancel/lịch sử job/cấu hình/project sidecar. Resume tùy stage/cache, không khôi phục mọi phép tính dang dở.

```text
File/URL → download nếu cần → probe/extract → ASR/subtitle có sẵn
→ cue nguồn → nhóm ý/context/glossary/cache → câu Việt hoàn chỉnh
→ căn cue → quality gate vùng lỗi → validator/fallback → .vi.srt/dòng GUI
→ TTS theo nhóm riêng → fitting/timeline → mix/duration lock/sync check
→ video + .vi.srt giữ cue hiển thị + sidecar tùy chọn
```

GUI dùng dòng `{start,end,src,vi,speaker,...}`; TTS nhận `vi` đã cập nhật. CLI truyền cùng danh sách `Segment` sau làm đẹp sang `build_voice_track`. E2E kiểm tra cả hai đường đi.

### Yêu cầu và cài đặt Windows

Windows 10/11, Python 64-bit; đã thử **3.11.9**, Windows build **26200**. Launcher đề cập 3.10/3.11; chưa kiểm thử nền tảng khác. Cần FFmpeg **và FFprobe** trên PATH hoặc `ffmpeg_dir`; đã thử **9.0**, CPU H.264/NVENC. Desktop cần pywebview/pythonnet và WebView2; browser translation cần Playwright, Edge/Chrome và phiên đăng nhập. Model ASR phải tải đủ; GPU tùy chọn. CPU faster-whisper dùng `device: cpu`, `compute_type: int8`; GPU cần CUDA/PyTorch phù hợp. CapCut cần SDK/dịch vụ trong `tools/capcut-tts-api`; VieNeu/WhisperX/pyannote tùy chọn. Kịch bản ngoài dùng `tao_kich_ban.tool_dir`. Chưa có RAM/disk tối thiểu đã đo; video/model/PCM có thể lớn. Node chỉ cần cho test JavaScript, đã thử **24.16.0**.

PowerShell tại root local, giữ venv/config hiện có:

```powershell
$env:PYTHONUTF8 = '1'
if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) { python -m venv venv }
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r gui\requirements-gui.txt
if (-not (Test-Path -LiteralPath 'config.yaml')) { Copy-Item -LiteralPath config.example.yaml -Destination config.yaml }
.\venv\Scripts\python.exe -m pip check
ffmpeg -version
ffprobe -version
```

Đã kiểm tra requirements/entry point; pip check và dry-run offline hai requirements pass trong venv hiện có. Không tái tạo môi trường sạch/tải lại toàn bộ model; chưa có lockfile. `install.bat` cũ có bước CUDA riêng và thiếu vài dependency hiện tại; dùng requirements trên. Nếu thiếu FFmpeg, lấy Windows static từ [nguồn launcher chỉ dẫn](https://www.gyan.dev/ffmpeg/builds/), thêm `bin` vào PATH hoặc YAML:

```yaml
ffmpeg_dir: 'C:\Tools\ffmpeg\bin'
```

### Cấu hình

Dựa trên [config.example.yaml](config.example.yaml), không ghi đè credential. Key rút gọn cùng hàng thuộc section đầu hàng.

| Key YAML | Mục đích/yêu cầu | Ví dụ |
| --- | --- | --- |
| `ffmpeg_dir` | Tùy chọn nếu có PATH | `'C:\Tools\ffmpeg\bin'` |
| `asr.backend`, `device`, `compute_type`, `model_size` | ASR | `faster-whisper`, `cpu`, `int8`, `large-v3` |
| `translation.provider` | Đường dịch | `browser` hoặc `nvidia` |
| `translation.<provider>_api_key`, `<provider>_model` | Key bắt buộc cho API/model | `YOUR_API_KEY`; xem dưới |
| `translation.chunk_size`, `context_lines` | Lô/context API CLI | `25`, `3` |
| `translation.keep_source_timing`, `reuse_existing` | Giữ cue/tái sử dụng bản dịch phù hợp | `true`, `true` |
| `translation.vi_reflow`, `vi_beautify` | Rule/AI chia chữ | `true`, `false` |
| `translation.vi_beautify_window`, `vi_beautify_overlap` | Cửa sổ/context AI | `10`, `2` |
| `translation.browser_profile`, `browser_channel`, `wait_reply` | Phiên/chờ browser | `browser_profile`, `msedge`, `120` |
| `tts.engine`, `narrator_voice`, `narrator_pitch`, `base_rate` | Giọng/SSML Edge | `edge`, `vi-VN-NamMinhNeural`, `+0Hz`, `+0%` |
| `tts.max_speed`, `sync_mode`, `lock_av` | Fitting/khóa start | `1.6`, `strict`, `true` |
| `tts.max_retries`, `retry_delay`, `concurrency` | Retry/số luồng | `2`, `1.2`, `4` |
| `video.hardsub_vietnamese`, `use_gpu`, `keep_original_muted` | Render/âm gốc | `false`, `true`, `false` |
| `output.dir`, `suffix`, `keep_temp` | Output CLI/file tạm | `output`, `.vietsub_dub`, `false` |
| `tao_kich_ban.tool_dir` | Công cụ kịch bản ngoài | Đường dẫn cài đặt của bạn |
| `content_pipeline.database` | Kho SQLite | `data/content_ideas.sqlite` |

Provider → model mặc định trong `autodub/providers.py`: `gemini` → `gemini-3.6-flash`; `tokenrouter` → `moonshotai/kimi-k3-free`; `tokenrouter_gemini` → `google/gemini-3.6-flash`; `inferx` → `deepseek-v4-flash`; `nvidia` → `google/gemma-4-31b-it`; `zenmux` → `z-ai/glm-4.7-flash-free`; `tokenharbor` → `deepseek-v4-flash:free`. Đây là giá trị code, chỉ model NVIDIA trên được smoke live. Base URL/timeout tùy provider ánh xạ qua `api_params_for_provider`. Edge còn có `vi-VN-HoaiMyNeural`; CapCut/VieNeu dùng catalog riêng.

CLI đọc `.env` và điền YAML key rỗng từ `GEMINI_API_KEY`, `TOKENROUTER_API_KEY`, `TOKENROUTER_GEMINI_API_KEY`, `INFERX_API_KEY`, `NVIDIA_API_KEY`. `.env` ghi đè biến process; YAML không rỗng ưu tiên hơn. GUI chỉ đọc YAML, không có cùng loader `.env`; ZenMux/TokenHarbor dùng YAML. Option project GUI có thể ưu tiên hơn config chung.

### Quick Start và thao tác

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe gui\app.py
```

Hoặc `run_gui.bat`. Desktop đã mở với config QA riêng. Chọn **Lồng tiếng video**, thêm file/link và chọn video; xem **Nhận dạng → Dịch → Giọng đọc → Xuất file**. **Làm mờ/Phụ đề/Logo/Cắt video** chỉnh lớp hình/khoảng xử lý. Duyệt bản dịch trước TTS và nghe câu vượt slot.

CLI chỉ nhận một file/URL, không có flag provider hay `--help`:

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe main.py 'C:\Media\video mẫu.mp4'
```

Thay placeholder; entry point đã chạy E2E, video riêng cần model/provider cấu hình. `run.bat` gọi cùng CLI, hỏi input nếu thiếu đối số. **Video kể chuyện** nhận TXT/MD hoặc gọi công cụ kịch bản rồi dựng giọng/ảnh/video. **Kho ý tưởng** tìm/nhập/phân tích/xuất; **Công cụ Video** tìm/tải/cắt hàng loạt.

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe scripts\content_pipeline.py --help
```

Help đã chạy. `import`, `analyze`, `export`, `run`, `sample` dùng database cấu hình và có thể thay đổi kho; `--output-dir` chỉ đổi nơi xuất. Xem [tài liệu kho nội dung](docs/CONTENT_PIPELINE.md).

### Dịch, làm đẹp và output

Mặc định mới: dịch thành câu hoàn chỉnh cùng `source_ids`, rồi căn nguyên văn vào cue và chỉ gọi quality gate cho vùng ngắt xấu. Mỗi batch có context hai phía, summary và glossary xuyên video. `keep_source_timing: true` giữ cue/timestamp. Bản đã qua semantic validator không chạy lại cleanup cũ. Đặt `semantic_translation: false` để dùng luồng legacy mô tả dưới đây; xem [SEMANTIC_PIPELINE](docs/SEMANTIC_PIPELINE.md) về config, cache, retry và giới hạn.

Ở đường legacy, validator giữ số cue/index/start/end; text không rỗng/markdown/whitespace thừa. Chuỗi từ Unicode và số phải giữ nguyên thứ tự, cho phép đổi hoa/thường/dấu câu. Nghỉ trên 1,4 giây hoặc người nói khác đã xác định là ranh cứng. Overlap giữ cue đã áp dụng; merge mất/lặp chữ hoàn tác cửa sổ. Timeout/rate limit/JSON lỗi giữ bản trước AI; tín hiệu hủy job được truyền lên pipeline. Rule là heuristic; glossary “vòng → điểm” cần dấu hiệu bắn đích gần cue, vẫn cần duyệt thuật ngữ.

TTS semantic đọc theo nhóm bằng Segment âm thanh riêng; sau khi đo clip, `.vi.srt` và ASS được gắn lại theo đúng khoảng giọng (chia theo độ dài chữ Việt), không giữ khoảng nghỉ pack Trung trong cụm. Với TTS legacy (`semantic_groups: false`), `use_placed=True` đổi end theo độ dài giọng từng cue. Fitting có thể tăng tốc/cắt đuôi. Sync check có thể báo `fail` nhưng vẫn render: đọc báo cáo/nghe lại.

CLI ghi `<output.dir>/<stem>/`; GUI ghi `output/<stem chuẩn hóa>/`, không dùng `output.dir` như CLI; khoảng cắt có stem riêng. Artifact tùy stage:

```text
<stem>.asr.srt                  ASR trước chuẩn bị dịch
<stem>.src.srt                  cue nguồn trong pipeline
<stem>.vi.srt                   bản Việt; sau TTS bám giọng đọc
<stem>.dich_cache.json          cache CLI
<stem>.translate_cache.json     cache GUI
<stem>.quy_trinh.log            log lượt chạy
<stem>.tts_loi.txt              nếu còn lỗi TTS
<stem>.project.json             trạng thái GUI
<stem>.vietsub_dub.mp4          video; suffix CLI tùy chỉnh
_tmp/                          audio trích, clip, mix, render
```

Có sidecar speech-map/media-clock/sync-check, thumbnail tùy chọn. CLI chỉ xóa `_tmp` do lượt chạy tạo khi `keep_temp` tắt; thư mục có sẵn được giữ. Chạy lại cùng stem có thể thay artifact: sao lưu/dùng QA output riêng. Job history ở `data/background_jobs.json`, nội dung ở SQLite cấu hình.

### Testing và phát triển

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_beautify_safety.py -v
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_dub_e2e.py -v
.\venv\Scripts\python.exe -m compileall -q autodub gui scripts main.py
.\venv\Scripts\python.exe -m pip check
node --test tests\test_story_ui_state.js
```

Suite mặc định không gọi dịch/TTS live. E2E dùng Windows SAPI tạo lời thoại, mock ASR/phản hồi mạng, chạy media thật. Browser tests dùng Playwright với Edge local (`channel="msedge"`), cần Edge đã cài, không đăng nhập AI. Media tests cần FFmpeg/FFprobe; Node không cần cho app.

```powershell
.\venv\Scripts\python.exe -m pip install coverage ruff
$env:COVERAGE_FILE = Join-Path $env:TEMP ('autodub-' + [guid]::NewGuid().ToString('N') + '.coverage')
.\venv\Scripts\python.exe -m coverage run --source=autodub,main -m unittest discover -s tests
.\venv\Scripts\python.exe -m coverage report
.\venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 autodub gui scripts tests main.py

```

Coverage/Ruff là dependency kiểm thử bổ sung. Mã test được giữ trong `tests/`; cache và kết quả kiểm thử không đưa lên Git. Chưa cấu hình formatter/type checker; Ruff trên chỉ kiểm tra lỗi nghiêm trọng.

### Troubleshooting, giới hạn và riêng tư

- **FFmpeg/path:** kiểm tra cả binary/PATH/`ffmpeg_dir`; Unicode/dấu cách đã test, dùng nháy đơn YAML. Giới hạn path Windows vẫn áp dụng.
- **UnicodeEncodeError khi redirect help/log:** đặt `$env:PYTHONUTF8 = '1'`; batch launcher đã đặt UTF-8.
- **Key/timeout/rate limit:** kiểm tra provider/key/model, giảm cỡ lô/concurrency, xem cache/log. Fallback không chứng minh provider khác chạy live.
- **ASR thiếu model.bin:** cache chưa đủ; chọn model đủ hoặc tải lại bằng công cụ model/ASR. QA gặp ở `small`, còn `large-v3` đã nhận dạng thật CPU.
- **Render:** xem log, thử `use_gpu: false`, kiểm tra đủ hình/tiếng. MP4Box chỉ cho thay audio phù hợp; blur/hardsub cần encode.
- **TTS lệch/quá nhanh:** xem sync/`.tts_loi.txt`, lock/offset, độ dài câu/max speed. Lỗi một phần có thể để cue câm; lỗi toàn bộ dừng trước render.
- **SRT:** dùng reflow/beautify/audit rồi duyệt; metric không chứng minh mọi câu tự nhiên/tên/quan hệ dịch đúng. Parser chính SRT, ASS cho render; chưa xác minh nhập VTT/ASS hay tải subtitle tự động.
- **Chưa kiểm chứng đầy đủ:** Gemini browser, CapCut, VieNeu, Paraformer/SenseVoice/WhisperX, pyannote, tải nguồn live, công cụ kịch bản ngoài; chưa stress nhiều giờ/hết disk/quyền ghi mọi stage.

Giữ `config.yaml`, `.env`, cookie/profile ngoài Git. Dịch/beautify gửi subtitle ra provider; Edge/CapCut nhận text giọng; ảnh/nguồn có thể dùng dịch vụ ngoài. ASR/VieNeu có đường local nhưng model ban đầu có thể cần mạng. HTTP UI bind localhost, không phải dịch vụ đa người dùng có xác thực. Kiểm tra log/output trước chia sẻ.

Khi đóng góp: giữ tương thích config/project/cache, dùng fixture tạm, thêm regression, chạy suite và `git diff --check`; không commit dữ liệu/media QA. **Không có LICENSE ở root: chưa khai báo license.** Code dùng FFmpeg, yt-dlp, Playwright, pywebview, FunASR, faster-whisper, Edge TTS và dependency trong requirements; license upstream không tự cấp quyền cho project này.

<a id="english"></a>
## English

### Scope and features

The application uses the `autodub/` packages and `ui/` frontend. Tests cover units, HTTP/UI and CLI/GUI E2E with real media and mocked ASR/translation/TTS responses. Live translation quality depends on the source and provider.

- **Dubbing:** files/URLs, queue, ASR, subtitle editing, batch translation, reflow/beautification, TTS, synchronization and rendering.
- **Downloads:** yt-dlp, Bilibili DASH; URL/cookie handling for YouTube, Douyin, TikTok, Kuaishou and Ixigua. Live access depends on the source/permissions.
- **ASR:** `paraformer`, `sensevoice`, `faster-whisper`, `whisperx`; missing-speech recovery, hallucination filtering, corrections, screen cues, speech maps; optional pyannote diarization.
- **Translation:** Gemini browser/API, TokenRouter, TokenRouter Gemini, InferX, NVIDIA, ZenMux, TokenHarbor; context, character hints, batch caches and retry/fallback.
- **TTS/media:** Edge, CapCut, VieNeu; narrator/alternate/per-speaker, rate/pitch, trim/fitting; original-audio mixing, story music/ducking, blur/logos/ASS hardsubs; NVENC/CPU, stream copy and GUI chunked rendering.
- **Narrated videos:** TXT/MD, external script tool, source images/videos, AI images, slideshows, character voices, YouTube metadata/thumbnails/descriptions. Automatic YouTube upload is unverified.
- **Content planning:** search/import JSON/SQLite, AI or offline heuristic analysis, incremental storage, Excel/JSON export, story handoff.
- **Video tools:** batch search/download/cut; progress/cancellation/job history/settings/project sidecars. Resume depends on stage/cache, not restoration of every interrupted computation.

```text
File/URL → optional download → probe/extract → ASR/existing subtitles
→ source cues → semantic groups/context/glossary/cache → complete Vietnamese sentences
→ cue allocation → selective quality gate → validator/fallback → .vi.srt/GUI rows
→ separate grouped TTS → fitting/timeline → mix/duration lock/sync check
→ video + .vi.srt preserving display cues + optional sidecars
```

GUI rows use `{start,end,src,vi,speaker,...}`; TTS receives updated `vi`. The CLI passes its beautified `Segment` list to `build_voice_track`. E2E verifies both paths.

### Requirements and Windows installation

Windows 10/11, 64-bit Python; tested with **3.11.9**, Windows build **26200**. The launcher mentions 3.10/3.11; other platforms were not tested. Both **FFmpeg and FFprobe** are required on PATH or through `ffmpeg_dir`; tested with **9.0**, CPU H.264/NVENC. Desktop needs pywebview/pythonnet and WebView2; browser translation needs Playwright, Edge/Chrome and a signed-in session. ASR model files must be complete; GPU is optional. CPU faster-whisper uses `device: cpu`, `compute_type: int8`; GPU needs compatible CUDA/PyTorch. CapCut requires its SDK/service in `tools/capcut-tts-api`; VieNeu/WhisperX/pyannote are optional. External scripts use `tao_kich_ban.tool_dir`. No minimum RAM/disk figure was measured; media/models/PCM can be large. Node is only for JavaScript tests, tested with **24.16.0**.

PowerShell at the local root; preserve an existing venv/config:

```powershell
$env:PYTHONUTF8 = '1'
if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) { python -m venv venv }
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r gui\requirements-gui.txt
if (-not (Test-Path -LiteralPath 'config.yaml')) { Copy-Item -LiteralPath config.example.yaml -Destination config.yaml }
.\venv\Scripts\python.exe -m pip check
ffmpeg -version
ffprobe -version
```

Requirements/entry points were inspected; pip check and offline dry-runs of both requirement files passed in the existing venv. QA did not rebuild a clean environment/download all models; no lockfile exists. The older `install.bat` has a separate CUDA step and omits some current dependencies; use the requirements above. For missing FFmpeg, obtain Windows static binaries from the [source referenced by the launcher](https://www.gyan.dev/ffmpeg/builds/), add `bin` to PATH or use YAML:

```yaml
ffmpeg_dir: 'C:\Tools\ffmpeg\bin'
```

### Configuration

Use [config.example.yaml](config.example.yaml), preserving credentials. Abbreviated keys in a row belong to its first section.

| YAML key | Purpose/requirement | Example |
| --- | --- | --- |
| `ffmpeg_dir` | Optional when on PATH | `'C:\Tools\ffmpeg\bin'` |
| `asr.backend`, `device`, `compute_type`, `model_size` | ASR | `faster-whisper`, `cpu`, `int8`, `large-v3` |
| `translation.provider` | Translation route | `browser` or `nvidia` |
| `translation.<provider>_api_key`, `<provider>_model` | Required API key/model | `YOUR_API_KEY`; see below |
| `translation.chunk_size`, `context_lines` | Batch/CLI API context | `25`, `3` |
| `translation.keep_source_timing`, `reuse_existing` | Preserve cues/reuse suitable translation | `true`, `true` |
| `translation.vi_reflow`, `vi_beautify` | Rule/AI redistribution | `true`, `false` |
| `translation.vi_beautify_window`, `vi_beautify_overlap` | AI window/context | `10`, `2` |
| `translation.browser_profile`, `browser_channel`, `wait_reply` | Browser session/timeout | `browser_profile`, `msedge`, `120` |
| `tts.engine`, `narrator_voice`, `narrator_pitch`, `base_rate` | Voice/Edge SSML | `edge`, `vi-VN-NamMinhNeural`, `+0Hz`, `+0%` |
| `tts.max_speed`, `sync_mode`, `lock_av` | Fitting/start locking | `1.6`, `strict`, `true` |
| `tts.max_retries`, `retry_delay`, `concurrency` | Retry/concurrency | `2`, `1.2`, `4` |
| `video.hardsub_vietnamese`, `use_gpu`, `keep_original_muted` | Render/original audio | `false`, `true`, `false` |
| `output.dir`, `suffix`, `keep_temp` | CLI output/temp files | `output`, `.vietsub_dub`, `false` |
| `tao_kich_ban.tool_dir` | External script tool | Your installation path |
| `content_pipeline.database` | SQLite library | `data/content_ideas.sqlite` |

Provider → default model in `autodub/providers.py`: `gemini` → `gemini-3.6-flash`; `tokenrouter` → `moonshotai/kimi-k3-free`; `tokenrouter_gemini` → `google/gemini-3.6-flash`; `inferx` → `deepseek-v4-flash`; `nvidia` → `google/gemma-4-31b-it`; `zenmux` → `z-ai/glm-4.7-flash-free`; `tokenharbor` → `deepseek-v4-flash:free`. These are code values; only that NVIDIA model received a live smoke test. Provider-specific base URL/timeouts are mapped by `api_params_for_provider`. Edge also has `vi-VN-HoaiMyNeural`; CapCut/VieNeu use separate catalogs.

CLI reads `.env`, filling empty YAML keys from `GEMINI_API_KEY`, `TOKENROUTER_API_KEY`, `TOKENROUTER_GEMINI_API_KEY`, `INFERX_API_KEY`, `NVIDIA_API_KEY`. `.env` overrides process variables; nonempty YAML wins. GUI reads YAML without the same `.env` loader; ZenMux/TokenHarbor use YAML. GUI project options may override global configuration.

### Quick Start and workflow

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe gui\app.py
```

Or use `run_gui.bat`. Desktop startup passed with isolated QA settings. Select **Lồng tiếng video**, add a file/link and select the video; review **Nhận dạng → Dịch → Giọng đọc → Xuất file**. **Làm mờ/Phụ đề/Logo/Cắt video** control image layers/processing intervals. Review translations before TTS and listen to lines exceeding their slots.

CLI accepts one file/URL, without provider flags or `--help`:

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe main.py 'C:\Media\video mẫu.mp4'
```

Replace the placeholder; the entry point ran through E2E, while your media requires configured models/providers. `run.bat` calls the same CLI, prompting if no argument is supplied. **Video kể chuyện** accepts TXT/MD or invokes the script tool before voices/images/video. **Kho ý tưởng** searches/imports/analyzes/exports; **Công cụ Video** performs batch search/download/cut.

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe scripts\content_pipeline.py --help
```

Help was executed. `import`, `analyze`, `export`, `run`, `sample` use the configured database and can modify it; `--output-dir` changes only export location. See the [content workflow document](docs/CONTENT_PIPELINE.md).

### Translation, beautification and outputs

New default: translate complete sentences with `source_ids`, redistribute their unchanged words into cues, then quality-check flagged windows. Batches carry context on both sides, a running summary and glossary. `keep_source_timing: true` preserves display cues/clocks. Validated semantic text skips legacy cleanup. Set `semantic_translation: false` for the legacy path described below; see [SEMANTIC_PIPELINE](docs/SEMANTIC_PIPELINE.md) for configuration, caches, retries and limits.

Validation preserves cue count/index/start/end; text must be nonempty without markdown/excess whitespace. Ordered Unicode words and numbers must match exactly, allowing case/punctuation changes. Pauses over 1.4 seconds or known speaker changes are hard boundaries. Overlap preserves applied cues; word loss/repetition rolls back the window. Timeout/rate limit/bad JSON keeps pre-AI text; job cancellation propagates to the pipeline. Rules are heuristic; the “vòng → điểm” glossary needs nearby target-shooting evidence and terminology still needs review.

Semantic TTS reads groups through separate audio Segments, then retimes on-screen cues onto the spoken interval so `.vi.srt`/ASS follow the voice. Legacy TTS (`semantic_groups: false`) can change cue ends through `use_placed=True`. Fitting may accelerate/trim speech. Sync checks can report `fail` and still render: inspect reports/listen again.

CLI uses `<output.dir>/<stem>/`; GUI uses `output/<normalized stem>/`, without honoring `output.dir` like CLI; trimmed intervals get separate stems. Artifacts depend on stage:

```text
<stem>.asr.srt                  ASR before translation preparation
<stem>.src.srt                  source cues in pipeline
<stem>.vi.srt                   Vietnamese; after TTS follows spoken audio
<stem>.dich_cache.json          CLI batch cache
<stem>.translate_cache.json     GUI batch cache
<stem>.quy_trinh.log            run log
<stem>.tts_loi.txt              when TTS failures remain
<stem>.project.json             GUI state
<stem>.vietsub_dub.mp4          video; CLI suffix configurable
_tmp/                          extracted audio, clips, mix, render
```

Optional speech-map/media-clock/sync-check sidecars and thumbnails are produced. CLI deletes `_tmp` only if this invocation created it and `keep_temp` is false; existing directories are preserved. Re-running a stem may replace artifacts: back up/use separate QA output. Job history is in `data/background_jobs.json`; content uses configured SQLite storage.

### Testing and development

```powershell
$env:PYTHONUTF8 = '1'
.\venv\Scripts\python.exe -m unittest discover -s tests -v
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_beautify_safety.py -v
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_dub_e2e.py -v
.\venv\Scripts\python.exe -m compileall -q autodub gui scripts main.py
.\venv\Scripts\python.exe -m pip check
node --test tests\test_story_ui_state.js
```

The default suite makes no live translation/TTS requests. E2E uses Windows SAPI spoken fixtures, mocked ASR/network responses and real media operations. Browser tests use Playwright with local Edge (`channel="msedge"`), require installed Edge and do not log in to AI services. Media tests need FFmpeg/FFprobe; Node is not required by the app.

```powershell
.\venv\Scripts\python.exe -m pip install coverage ruff
$env:COVERAGE_FILE = Join-Path $env:TEMP ('autodub-' + [guid]::NewGuid().ToString('N') + '.coverage')
.\venv\Scripts\python.exe -m coverage run --source=autodub,main -m unittest discover -s tests
.\venv\Scripts\python.exe -m coverage report
.\venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 autodub gui scripts tests main.py

```

Coverage/Ruff are additional test dependencies. Test sources remain in `tests/`; caches and results are excluded from Git. No formatter/type checker is configured; these Ruff rules check serious errors only.

### Troubleshooting, limitations and privacy

- **FFmpeg/paths:** check both binaries/PATH/`ffmpeg_dir`; Unicode/spaces were tested, use single-quoted YAML. Windows path-length limits still apply.
- **UnicodeEncodeError with redirected help/logs:** set `$env:PYTHONUTF8 = '1'`; batch launchers already enable UTF-8.
- **Key/timeout/rate limit:** verify provider/key/model, reduce batch size/concurrency, inspect cache/logs. Fallback does not prove another provider ran live.
- **Missing ASR model.bin:** incomplete cache; select a complete model or download through model/ASR tooling. QA encountered this with `small`, while `large-v3` performed real CPU recognition.
- **Rendering:** inspect logs, try `use_gpu: false`, verify video/audio. MP4Box is for suitable audio replacement; blur/hardsubs need encoding.
- **TTS drift/excessive speed:** inspect sync/`.tts_loi.txt`, locking/offset, text length/speed limit. Partial failure may leave silent cues; total failure stops before render.
- **SRT:** use reflow/beautify/audit then review; metrics do not prove every sentence natural or names/relationships correctly translated. Primary parser is SRT, ASS is for rendering; VTT/ASS import and automatic subtitle downloads are unverified.
- **Not fully verified:** Gemini browser, CapCut, VieNeu, Paraformer/SenseVoice/WhisperX, pyannote, live downloads, external script tool; no multi-hour/disk-exhaustion/every-stage permission stress.

Keep `config.yaml`, `.env`, cookies/profiles out of Git. Translation/beautification sends subtitles to providers; Edge/CapCut receive speech text; images/sources may use external services. ASR/VieNeu have local paths but initial models may require network access. HTTP UI binds localhost and is not an authenticated multi-user service. Inspect logs/outputs before sharing.

Contributions should preserve config/project/cache compatibility, use temporary fixtures, add regressions, run the suite and `git diff --check`; do not commit user data/QA media. **No root LICENSE exists: no project license has been declared.** Code uses FFmpeg, yt-dlp, Playwright, pywebview, FunASR, faster-whisper, Edge TTS and requirements-file dependencies; upstream licenses do not grant rights to this project itself.

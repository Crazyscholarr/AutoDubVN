# Semantic subtitle translation / Dịch phụ đề theo câu

## Tiếng Việt

Luồng dịch của CLI và GUI, cả API lẫn Gemini browser, mặc định dùng semantic pipeline khi truyền `translation_cfg`. Hàm thư viện gọi không có cấu hình vẫn giữ hợp đồng cũ. Luồng sửa nguồn đã là tiếng Việt trong browser vẫn dùng proofreading cũ.

1. Đọc cue nguồn và chia nhóm theo khoảng nghỉ (`max_group_gap_ms`, mặc định 1400), speaker đã biết, `hard_boundary` hoặc scene/chapter đã có metadata. Dấu câu ASR Trung đơn lẻ không đủ để kết luận hết ý: dữ liệu thực có `整。 / 晚`. Không tự đoán người nói/cảnh từ SRT.
2. Gom batch khoảng 10 cue/45 giây (tối đa 30 cue/60 giây), tránh chẻ nhóm. Cấp 2 cue trước/sau chỉ đọc, summary và glossary. Nhóm mặc định tối đa 6 cue/20 giây; cue đơn dài hơn giới hạn được giữ nguyên. Batch cuối/ranh giới có thể ngắn hơn. Lô nhỏ và prompt gọn giúp xkiro lẫn Gemini web (dán vừa, ít SCHEMA_FAILURE).
3. Prompt A trả câu hoàn chỉnh với `source_ids`. Validator yêu cầu phủ đúng target, đúng thứ tự, không lặp/ngoài context, không vượt nhóm; kiểm tra chữ số, tên đã khóa và chữ Trung ngoài glossary.
4. Prompt B phân phối câu vào cue. Validator kiểm tra id/start/end/text và chuỗi từ Unicode/số **theo từng câu**, không chỉ toàn batch. Tên glossary nhiều từ phải nằm trọn trong một cue. Lỗi được retry một lần với lỗi cụ thể và phản hồi trước. Nếu vẫn lỗi, dùng bộ chia theo thời lượng/ngữ pháp từ câu Việt đã đạt. Không chèn/bỏ từ để lấp cue rỗng.
5. Prompt C (vi_beautify auto/true) chỉ nhận câu còn ngắt đáng ngờ; mặc định tắt để khỏi thêm lượt API. Mỗi cue chỉ có một chủ sở hữu. Có source Trung và glossary. C chỉ được đổi dấu câu/hoa thường hoặc chuyển từ; lỗi nghĩa/tên/xưng hô được ghi warning, không tự viết lại. Kết quả lỗi giữ bản B.
6. TTS tạo Segment âm thanh riêng theo câu/nhóm. Audio dùng strict fitting, không tràn ra ngoài end nhóm. Sau khi đo clip thật, mốc phụ đề hiển thị được gắn lại theo `[placed_start, placed_start+voice_duration]` của cụm, chia theo độ dài chữ Việt — không giữ khoảng nghỉ pack Trung trong cụm. Voice/speaker khác hoặc ranh cứng tách nhóm. Với SRT cũ không có metadata, nhóm được suy ra bảo thủ từ dấu câu/khoảng nghỉ hiện có. ASS burn-in dùng đúng cửa sổ giọng, không cắt theo `read_cps`.

Giới hạn CPS/số ký tự là mục tiêu mềm: không bỏ nghĩa để ép vừa. Còn câu dài được ghi `reading_speed`; fitting vẫn có thể tăng tốc/cắt audio nếu câu đọc vượt slot. Giữ timeline không chứng minh từng từ khớp môi; chưa có forced alignment trong nhóm.

### Cấu hình và cache

Các khóa có chú thích trong [config.example.yaml](../config.example.yaml). Không cần thay key/provider để bật đường đi mới. Các cài đặt quan trọng:

```yaml
translation:
  semantic_translation: true
  keep_source_timing: true
  semantic_batch_cues: 10
  semantic_batch_seconds: 45
  semantic_context_cues: 2
  semantic_group_max_cues: 6
  semantic_group_max_seconds: 20
  max_group_gap_ms: 1400
  name_policy: han_viet  # han_viet | pinyin | keep_source
  glossary:
    "七月": {vi: "Thất Nguyệt", type: person, locked: true}
  glossary_path: ""     # JSON glossary series chỉ đọc
  browser_model: "selected-in-browser"
  vi_beautify: false
tts:
  semantic_groups: true
```

`tts.semantic_groups` mặc định theo `translation.semantic_translation`; đặt false để đọc từng cue. Đặt cả hai false để dùng lại cách dịch/đọc cũ. `keep_source_timing` giữ cue sau bước chuẩn bị nguồn; tránh các lựa chọn gộp/chia nguồn nếu cần giữ nguyên cue SRT đầu vào.

`translation.reuse_existing: true` vẫn dùng SRT Việt có sẵn; nó không tự dịch lại file cũ bằng luồng mới. Muốn thử bản mới, dùng bản sao/output riêng và đặt `reuse_existing: false`. Không cần xóa các SRT gốc.

Tên mới confidence dưới 0,9 hoặc `needs_review=true` phải giữ chữ nguồn, không khóa. Tên đã khóa có ưu tiên; cấu hình glossary inline ghi đè glossary file. Chương trình không tự sửa file glossary series. Một tên giữ chữ Trung có thể cần người duyệt cách phát âm trước TTS.

Với cache gốc `C` (`<stem>.dich_cache.json` của CLI hoặc `<stem>.translate_cache.json` của GUI):

- `C.semantic.json`: kết quả A/B từng batch; hash SHA-256 gồm nguồn, context, prompt version, provider/model, glossary/summary và cấu hình. Cache cũ không dùng chung với semantic.
- `C.semantic.json.review.json`: trạng thái từng batch, warning, câu nguồn được phủ và glossary cuối.
- `C.semantic.json.glossary.json`: glossary thuần JSON, có thể dùng làm `glossary_path` cho video sau cùng series.
- `C.semantic-cues.json`: metadata quyền sở hữu câu. Chỉ phục hồi khi toàn bộ id/text/start/end còn khớp.

API semantic không tự chuyển Gemini/NVIDIA sang model khác, để cache phản ánh model đã yêu cầu. Browser cần đổi `browser_model` khi người dùng đổi model trên web; app không tự xác minh tên model đang chọn. Browser tạo chat mới sau số request `reset_every`, mỗi request đã có context/glossary cần thiết.

Nếu A không đạt sau một retry, giữ các batch đạt trong cache và báo `TranslationIncomplete` trước TTS. Nếu B và fallback cùng không thể chia đủ cue an toàn, cũng báo chưa hoàn tất. Không có cách giữ “bản Việt trước A” cho cue chưa từng dịch. Không tự biến nguồn Trung thành bản Việt thành công.

### Kiểm chứng

Nhịp hiển thị Trung dùng packer theo từ; thuật toán, giới hạn và lệnh replay ở
[CAPCUT_CAPTIONS.md](CAPCUT_CAPTIONS.md). Nếu chữ/mốc mâu thuẫn khiến pack chưa
đầy đủ, CLI/GUI lưu bằng chứng và dừng trước dịch/TTS để duyệt nguồn.

Validator token bảo đảm bảo toàn nội dung sau bước dịch Việt, không chứng minh đúng nghĩa Trung–Việt. Các thử nghiệm API đã gặp lỗi schema, chữ Trung sót và timeout; chưa xác nhận chất lượng ngang CapCut. Đọc/nghe so nguồn trước khi render toàn bộ video. Packer và công cụ đối chiếu file vàng được cải tiến riêng; không sửa nghĩa nguồn bằng cách đổi điểm ngắt.

## English

Configured CLI/GUI API and Gemini browser translation now use a sentence-first pipeline by default. Calls without `translation_cfg` retain the legacy contract; Vietnamese browser proofreading is unchanged. Source groups respect known speakers, pauses, explicit boundaries and supplied scene/chapter metadata. Chinese ASR punctuation alone is soft because it can occur inside a word. No speaker/scene inference is performed.

Batches target 10 cues/45 seconds with two read-only cues on each side, a running summary and glossary. Defaults cap groups at 6 cues/20 seconds and batches at 30 cues/60 seconds, except indivisible long cues. Compact prompts and payloads keep the same contract for xkiro and Gemini web. Prompt A translates complete sentences with exhaustive, ordered, exclusive target ownership. B assigns their unchanged words to display cues. C (off by default) only checks flagged sentence windows against Chinese source/glossary. Validators protect IDs, clocks, nonempty clean text, ordered Unicode words/numbers per sentence and unsplit glossary names. Each gate retries once; B can fall back to deterministic allocation, C to B. A failure has no previous Vietnamese translation to fall back to: valid batches remain cached and `TranslationIncomplete` stops TTS.

TTS builds separate sentence/group audio segments, strictly fitted to group intervals. After the fitted clip is measured, display cues are retimed onto that spoken interval by Vietnamese character share so on-screen captions follow the voice instead of leftover Chinese screen-pack gaps. Voice changes and hard boundaries split groups. Old SRT files without metadata use conservative punctuation/pause grouping. Fitting can still accelerate/trim overlong speech; intra-group word alignment remains a character-share estimate, not a forced-alignment model. CPS/line-length targets are soft, never permission to drop words.

The YAML example above lists the controls. Set `semantic_translation` and `tts.semantic_groups` false for legacy translation/speech. Name policies are `han_viet`, `pinyin`, `keep_source`. Uncertain entities retain source spelling with review flags; confident suggestions can lock, and locked entries take priority. Shared glossary files are read-only; inline entries override them. Retained Chinese names may require pronunciation review before TTS.

`translation.reuse_existing: true` still reuses existing Vietnamese SRT files. Test a new translation in a separate copy/output with `reuse_existing: false`; deleting original subtitles is unnecessary.

For legacy cache path `C`, new data live in `C.semantic.json`, diagnostics in `C.semantic.json.review.json`, reusable glossary in `C.semantic.json.glossary.json`, and exact-match cue metadata in `C.semantic-cues.json`. Hashes include prompt version, model/provider, source/context, glossary/summary and configuration. Semantic Gemini/NVIDIA requests do not silently switch models. Set `browser_model` when changing the web model; the selected web model is not automatically verified. Browser sessions reset every `reset_every` requests.

Token equivalence proves preservation only between Vietnamese translation and redistribution. It cannot prove cross-language fidelity; ID/digit/schema checks are necessary but insufficient, and selective quality windows do not cover every semantic error. Live API probes have encountered schema errors, untranslated Chinese and timeouts; CapCut-level quality has not been verified.

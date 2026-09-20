"""Align two Chinese SRT tracks by text, then inspect their Vietnamese versions.

Writes a new QA directory; never edits any input. Scores are detector signals,
not an automatic verdict about translation fidelity.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from difflib import SequenceMatcher
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autodub.srt_utils import load_srt_file
from autodub.vi_reflow import bad_break_score, hard_boundary


def normalized(text):
    return "".join(re.findall(r"[^\W_]", text.casefold()))


def track(segs):
    text, owners = "", []
    for i, s in enumerate(segs):
        part = normalized(s.text)
        text += part
        owners.extend([i] * len(part))
    return text, owners


def align_source(a, b):
    left, li = track(a)
    right, ri = track(b)
    hits = {}
    for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks():
        if block.size < 3:
            continue
        for k in range(block.size):
            hits.setdefault(li[block.a+k], []).append(ri[block.b+k])
    return hits


def metrics(segs):
    return dict(cues=len(segs), end_seconds=segs[-1].end if segs else 0,
                invalid_clocks=sum(not 0 <= s.start < s.end for s in segs),
                empty=sum(not s.text.strip() for s in segs),
                over_22_cps=sum(len(s.text)/max(.001, s.duration)>22 for s in segs),
                suspected_bad_breaks=sum(not hard_boundary(a,b) and bad_break_score(a.text,b.text)>=8
                                         for a,b in zip(segs,segs[1:])))


def compare(paths, sample=240, parent=None):
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    cap_src, cap_vi, auto_src, auto_vi = [load_srt_file(str(p)) for p in paths]
    if len(cap_src)!=len(cap_vi) or len(auto_src)!=len(auto_vi):
        raise ValueError("Mỗi cặp source/translation cần cùng số cue để ánh xạ vị trí")
    hits = align_source(cap_src, auto_src)
    count = min(max(1, sample), len(cap_src))
    selected = sorted({round(i*(len(cap_src)-1)/max(1,count-1)) for i in range(count)})
    rows = []
    for i in selected:
        matches = hits.get(i, [])
        confidence = len(matches)/max(1,len(normalized(cap_src[i].text)))
        if matches and confidence >= .35:
            lo, hi = min(matches), max(matches)+1
            method = "text_anchor"
        else:
            lo = max(0, bisect_right([s.start for s in auto_src],cap_src[i].start)-1)
            hi = min(len(auto_src),lo+2)
            method = "time_fallback_needs_review"
        rows.append(dict(capcut_id=cap_src[i].index, start=cap_src[i].start,
                         source_capcut=cap_src[i].text, capcut_vi=cap_vi[i].text,
                         autodub_ids=[s.index for s in auto_src[lo:hi]],
                         source_autodub=" | ".join(s.text for s in auto_src[lo:hi]),
                         autodub_vi=" | ".join(s.text for s in auto_vi[lo:hi]),
                         source_start_delta_s=round(auto_src[lo].start-cap_src[i].start,3),
                         alignment=method, anchor_fraction=round(confidence,3),
                         review="Chưa chấm nghĩa; xem cue lân cận trước khi kết luận thiếu/lặp ý"))
    root = Path(tempfile.mkdtemp(prefix="semantic-qa-", dir=parent))
    report = dict(inputs=[dict(path=str(p.resolve()), sha256=h, **metrics(s)) for p,h,s in
                          zip(paths,hashes,[cap_src,cap_vi,auto_src,auto_vi])],
                  sampled_capcut_cues=len(rows), rows=rows,
                  limitations=["Hai nguồn ASR khác nhau: dùng neo text Trung trước, không so cùng index.",
                               "Các số bad-break/CPS chỉ là tín hiệu, không phải điểm dịch nghĩa.",
                               "Chưa nghe video; timestamp nguồn khác nhau không tự xác định bản nào đúng."])
    (root/"comparison.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    esc = html.escape
    rendered = "".join(f'<tr><td>{r["capcut_id"]}<br>{r["start"]:.2f}s</td>'
                       f'<td>{esc(r["source_capcut"])}</td><td>{esc(r["capcut_vi"])}</td>'
                       f'<td>{esc(r["source_autodub"])}</td><td>{esc(r["autodub_vi"])}</td>'
                       f'<td>{r["autodub_ids"]}<br>{r["source_start_delta_s"]:+.3f}s<br>'
                       f'{r["alignment"]}<br>{r["anchor_fraction"]:.0%}</td></tr>' for r in rows)
    page = '''<!doctype html><html lang="vi"><meta charset="utf-8"><title>Đối chiếu phụ đề</title>
<style>body{font:15px system-ui;margin:28px;color:#20272b;background:#fafafa}h1{font-size:24px}
table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #ddd;vertical-align:top;text-align:left}
th{position:sticky;top:0;background:#eef2f4}input{padding:10px;width:50%;margin:16px 0}small{color:#555}</style>
<h1>CapCut và AutoDubVN — đối chiếu theo nội dung nguồn</h1>
<p>SAMPLE cue CapCut trải đều đầu, giữa, cuối; “ | ” là ranh giới cue AutoDubVN.
Neo chữ Trung giúp đối chiếu khi hai bản ASR khác thời gian. Cue ghép có thể chứa thêm ngữ cảnh.
Đây là phiếu duyệt, chưa phải kết luận chất lượng ngang CapCut.</p>
<input id="q" placeholder="Lọc theo tên, câu thoại, ID…" aria-label="Lọc câu thoại">
<table><thead><tr><th>CapCut cue / giờ</th><th>Trung CapCut</th><th>Việt CapCut</th><th>ASR AutoDubVN</th><th>Việt AutoDubVN cũ</th><th>Neo cue / lệch start / độ phủ</th></tr></thead><tbody>ROWS</tbody></table>
<script>document.querySelector('#q').oninput=e=>{let q=e.target.value.toLocaleLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q))}</script></html>'''
    (root/"comparison.html").write_text(page.replace("SAMPLE",str(len(rows))).replace("ROWS",rendered),encoding="utf-8")
    assert hashes==[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    return root, report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("tracks",type=Path,nargs='+',help="gold source, or gold-zh gold-vi source-zh source-vi")
    p.add_argument("--sample",type=int,default=240)
    p.add_argument("--output-parent",type=Path)
    p.add_argument("--speech-map",type=Path)
    p.add_argument("--repack",action="store_true")
    p.add_argument("--thresholds",type=Path,help="JSON overrides for caption QA thresholds")
    a=p.parse_args()
    if len(a.tracks)==2:
        from scripts.caption_metrics import evaluate
        limits=json.loads(a.thresholds.read_text(encoding='utf-8')) if a.thresholds else None
        root,report=evaluate(*a.tracks,speech_map_path=a.speech_map,repack=a.repack,
                             parent=a.output_parent,thresholds=limits)
        print(root)
        print(f'{"Metric":24} {"Before":>12} {"After":>12}')
        for key in ('cues','avg_duration','avg_chars','max_duration','overlaps',
                    'torn_word_pct','abut_pct','pause_f1','han_cer','jieba_wer'):
            print(f'{key:24} {report["before"][key]:12.4f} {report["after"][key]:12.4f}')
        print(json.dumps(dict(passed=report['passed'],checks=report['checks'],
                              golden_checks=report['golden_checks'],
                              withheld_han_chars=report['withheld_han_chars']),ensure_ascii=False))
        return 0 if report['passed'] else 1
    if len(a.tracks)!=4:
        p.error('Provide two Chinese SRTs, or four source/translation SRTs')
    if a.repack or a.speech_map or a.thresholds:
        p.error('Caption options require two Chinese SRTs')
    root,report=compare(a.tracks,a.sample,a.output_parent)
    print(root)
    print(json.dumps({"sampled":report["sampled_capcut_cues"],"inputs":report["inputs"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    sys.exit(main())

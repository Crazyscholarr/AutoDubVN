"""Chinese caption evaluation: content errors are independent of cue boundaries."""

from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile

from autodub.asr.screen_pack import word_spans
from autodub.srt_utils import load_srt_file, save_srt_file

DEFAULT_THRESHOLDS = dict(
    min_avg_duration=1.4,
    max_avg_duration=2.6,
    min_avg_chars=7.0,
    max_avg_chars=14.0,
    min_cue_ratio=0.75,
    max_cue_ratio=1.25,
    max_duration=5.0,
    max_overlaps=0,
    max_torn_word_pct=5.0,
    min_pause_f1=0.0,
    max_withheld_chars=0,
)


def han(text):
    return "".join(re.findall(r"[\u3400-\u9fff]", text))


def track(segs):
    text, owners, edges = "", [], []
    for i, seg in enumerate(segs):
        part = han(seg.text)
        text += part
        owners.extend([i] * len(part))
        edges.append(len(text))
    return text, owners, edges


def edit_distance(a, b):
    """Exact Levenshtein distance using a bit vector; works on chars or words."""
    if not a:
        return len(b)
    eq = {}
    for i, ch in enumerate(a):
        eq[ch] = eq.get(ch, 0) | (1 << i)
    positive, negative, score, high = ~0, 0, len(a), 1 << (len(a) - 1)
    for ch in b:
        match = eq.get(ch, 0)
        xv = match | negative
        xh = (((match & positive) + positive) ^ positive) | match
        ph = negative | ~(xh | positive)
        mh = positive & xh
        score += bool(ph & high) - bool(mh & high)
        ph, mh = (ph << 1) | 1, mh << 1
        positive, negative = mh | ~(xv | ph), ph & xv
    return score


def metrics(segs):
    text, owners, edges = track(segs)
    internal = set(edges[:-1])
    torn = set()
    for a, b in word_spans(text):
        for edge in internal.intersection(range(a + 1, b)):
            if edge:
                torn.add(owners[edge - 1])
    n = len(segs)
    return dict(
        cues=n,
        avg_chars=len(text) / max(1, n),
        avg_duration=sum(s.duration for s in segs) / max(1, n),
        max_duration=max((s.duration for s in segs), default=0),
        overlaps=sum(a.end > b.start + 1e-6 for a, b in zip(segs, segs[1:])),
        invalid_clocks=sum(not 0 <= s.start < s.end for s in segs),
        torn_word_pct=100 * len(torn) / max(1, n),
        torn_cue_ids=[segs[i].index for i in sorted(torn)],
        abut_pct=100
        * sum(abs(a.end - b.start) <= 0.0011 for a, b in zip(segs, segs[1:]))
        / max(1, n - 1),
    )


def comparison(gold, actual, pause_seconds=0.18):
    gt, go, ge = track(gold)
    at, ao, ae = track(actual)
    anchors = {}
    hits = {}
    for block in SequenceMatcher(None, gt, at, autojunk=False).get_matching_blocks():
        if block.size < 3:
            continue
        for k in range(block.size):
            gi, ai = block.a + k, block.b + k
            anchors[ai] = gi
            hits.setdefault(go[gi], []).append(ao[ai])
    gold_pauses = {
        ge[i]
        for i in range(len(gold) - 1)
        if gold[i + 1].start - gold[i].end >= pause_seconds - 1e-6
    }
    actual_pauses = [
        ae[i]
        for i in range(len(actual) - 1)
        if actual[i + 1].start - actual[i].end >= pause_seconds - 1e-6
    ]
    matched = set()
    for edge in actual_pauses:
        anchor = anchors.get(edge - 1)
        if anchor is None:
            continue
        candidates = gold_pauses - matched
        nearest = min(candidates, key=lambda x: abs(x - anchor - 1), default=None)
        if nearest is not None and abs(nearest - anchor - 1) <= 1:
            matched.add(nearest)
    tp = len(matched)
    precision = tp / len(actual_pauses) if actual_pauses else float(not gold_pauses)
    recall = tp / len(gold_pauses) if gold_pauses else float(not actual_pauses)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    rows = []
    gold_edges = set(ge[:-1])
    actual_edge_map = {anchors[x - 1] + 1 for x in ae[:-1] if x - 1 in anchors}
    for i, g in enumerate(gold):
        indices = sorted(set(hits.get(i, [])))
        different = ge[i] in gold_edges and ge[i] not in actual_edge_map
        low = ge[i - 1] if i else 0
        different |= any(low < e < ge[i] for e in actual_edge_map)
        rows.append(
            dict(
                gold_id=g.index,
                start=g.start,
                end=g.end,
                gold=g.text,
                actual_ids=[actual[k].index for k in indices],
                actual=" | ".join(actual[k].text for k in indices),
                actual_start=actual[indices[0]].start if indices else None,
                actual_end=actual[indices[-1]].end if indices else None,
                different_cut=bool(different),
                anchor_fraction=len(hits.get(i, [])) / max(1, len(han(g.text))),
                needs_review=not indices,
            )
        )
    gw = [gt[a:b] for a, b in word_spans(gt)]
    aw = [at[a:b] for a, b in word_spans(at)]
    return dict(
        pause_precision=precision,
        pause_recall=recall,
        pause_f1=f1,
        gold_pauses=len(gold_pauses),
        actual_pauses=len(actual_pauses),
        anchored_char_fraction=len(anchors) / max(1, len(at)),
        han_cer=edit_distance(gt, at) / max(1, len(gt)),
        jieba_wer=edit_distance(gw, aw) / max(1, len(gw)),
        rows=rows,
    )


def check_thresholds(stats, gold_count, withheld, thresholds):
    tests = {
        "avg_duration": thresholds["min_avg_duration"]
        <= stats["avg_duration"]
        <= thresholds["max_avg_duration"],
        "avg_chars": thresholds["min_avg_chars"]
        <= stats["avg_chars"]
        <= thresholds["max_avg_chars"],
        "cue_ratio": thresholds["min_cue_ratio"]
        <= stats["cues"] / max(1, gold_count)
        <= thresholds["max_cue_ratio"],
        "max_duration": stats["max_duration"] < thresholds["max_duration"],
        "overlaps": stats["overlaps"] <= thresholds["max_overlaps"],
        "valid_clocks": stats["invalid_clocks"] == 0,
        "torn_words": stats["torn_word_pct"] <= thresholds["max_torn_word_pct"],
        "pause_f1": stats["pause_f1"] >= thresholds["min_pause_f1"],
        "withheld_text": withheld <= thresholds["max_withheld_chars"],
    }
    return tests


def evaluate(
    gold_path,
    source_path,
    *,
    speech_map_path=None,
    repack=False,
    parent=None,
    thresholds=None,
):
    from autodub.speechmap import SpeechMap
    from autodub.asr.merge import pack_screen_cues

    paths = [Path(gold_path), Path(source_path)]
    if speech_map_path:
        paths.append(Path(speech_map_path))
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    gold, source = [load_srt_file(str(p)) for p in paths[:2]]
    if not gold or not source:
        raise ValueError("Both subtitle inputs must contain cues")
    review = []
    sm = None
    if speech_map_path:
        data = json.loads(Path(speech_map_path).read_text(encoding="utf-8"))
        sm = SpeechMap(data.get("moc", data.get("marks", [])))
        if sm.empty:
            raise ValueError("Speech map contains no marks")
    actual = (
        pack_screen_cues(source, speech_map=sm, review=review) if repack else source
    )
    before = dict(**metrics(source), **comparison(gold, source))
    after = dict(**metrics(actual), **comparison(gold, actual))
    withheld = sum(len(han(r["text"])) for r in review if r.get("withheld"))
    limits = dict(DEFAULT_THRESHOLDS)
    unknown = set(thresholds or {}) - limits.keys()
    if unknown:
        raise ValueError(f"Unknown thresholds: {sorted(unknown)}")
    limits.update(thresholds or {})
    checks = check_thresholds(after, len(gold), withheld, limits)
    # Never report that the known damaged replay words were repaired by deleting them.
    golden_checks = {}
    original = "".join(s.text for s in source)
    for word in ("活下去", "哥们", "来一根"):
        if word in han(original) or word in han("".join(s.text for s in gold)):
            reference = next((s for s in gold if word in han(s.text)), None)
            golden_checks[word] = any(
                word in han(s.text)
                and (reference is None or abs(s.start - reference.start) < 3)
                for s in actual
            )
    if "他们不再只是伪装" in original:
        golden_checks["separate_clauses"] = any(
            han(s.text) == "他们不再只是伪装" for s in actual
        ) and any(han(s.text) == "他们在成长" for s in actual)
    if "好运" in original and "松姜" in original:
        golden_checks["scene_not_glued"] = not any(
            "好运" in s.text and "松姜" in s.text for s in actual
        )
    if any(han(s.text) in ("蔓", "延嘿", "盐") for s in source):
        golden_checks["weak_fragments_reviewed"] = not any(
            han(s.text) in ("蔓", "延嘿", "盐") for s in actual
        )
    report = dict(
        inputs=[dict(path=str(p.resolve()), sha256=h) for p, h in zip(paths, hashes)],
        gold=metrics(gold),
        before=before,
        after=after,
        review=review,
        withheld_han_chars=withheld,
        thresholds=limits,
        checks=checks,
        golden_checks=golden_checks,
        passed=all(checks.values()) and all(golden_checks.values()),
        notes=[
            "Han text anchors; no index alignment or gold text fed into packing.",
            "CER is exact Han Levenshtein; WER uses dictionary tokenization, not semantic fidelity.",
            "Pause >=180ms; an anchored boundary matches within one Han character. Unanchored pauses count as misses.",
            "Torn-word percentage is a dictionary signal. Withheld text and CER must be reviewed separately.",
            "Source homophones require title/glossary evidence; this comparison does not correct them.",
        ],
    )
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="caption-qa-", dir=parent))
    (root / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if repack:
        save_srt_file(
            str(
                root / ("packed.srt" if report["passed"] else "packed.needs-review.srt")
            ),
            actual,
        )
    esc = html.escape
    columns = (
        "cues",
        "avg_duration",
        "avg_chars",
        "max_duration",
        "overlaps",
        "torn_word_pct",
        "abut_pct",
        "pause_f1",
        "han_cer",
        "jieba_wer",
    )
    numbers = "".join(
        f"<tr><th>{k}</th><td>{before[k]:.4f}</td><td>{after[k]:.4f}</td></tr>"
        for k in columns
    )
    rendered = "".join(
        f'<tr data-diff="{int(r["different_cut"])}"><td>{r["gold_id"]}<br>{r["start"]:.3f}s</td>'
        f"<td>{esc(r['gold'])}</td><td>{esc(r['actual'])}</td>"
        f"<td>{r['actual_ids']}<br>{r['anchor_fraction']:.0%}</td></tr>"
        for r in after["rows"]
    )
    warnings = "".join(
        f"<li>{r['start']:.3f}–{r['end']:.3f}s: {esc(r['text'])} — {esc(r['reason'])}"
        f"{' (withheld)' if r.get('withheld') else ''}</li>"
        for r in review
    )
    page = f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>Caption QA</title>
<style>body{{font:16px system-ui;background:#f6f8fa;color:#172536;margin:28px}}table{{border-collapse:collapse;background:white;width:100%}}
th,td{{border:1px solid #ccd5df;padding:10px;text-align:left;vertical-align:top}}thead{{position:sticky;top:0;background:#e6edf4}}
input,select{{padding:10px;margin:14px 12px 14px 0}}summary{{cursor:pointer}}.status{{font-weight:700}}</style>
<h1>Nhịp phụ đề Trung — đối chiếu CapCut</h1><p class="status">{"PASS" if report["passed"] else "NEEDS REVIEW / FAIL"}</p>
<p>Chữ Hán được dùng làm neo. Không sửa lời nguồn bằng file vàng. Với {withheld} chữ tạm giữ để duyệt, bản packed chưa được xem là đầy đủ.</p>
<table><tr><th>Chỉ số</th><th>ASR cũ</th><th>Packed</th></tr>{numbers}</table>
<details><summary>Ngưỡng và kiểm tra vàng</summary><pre>{esc(json.dumps(dict(checks=checks, golden=golden_checks), ensure_ascii=False, indent=2))}</pre></details>
<details><summary>{len(review)} vùng cần duyệt</summary><ul>{warnings}</ul></details>
<input id="q" placeholder="Lọc câu thoại, ID…" aria-label="Tìm câu"><select id="mode" aria-label="Loại hàng"><option value="all">Tất cả</option><option value="diff">Cắt khác chỗ</option></select>
<table id="cues"><thead><tr><th>Gold / thời gian</th><th>CapCut</th><th>Packed (| = ranh cue)</th><th>Cue / độ phủ neo</th></tr></thead><tbody>{rendered}</tbody></table>
<script>function filter(){{const q=document.querySelector('#q').value.toLowerCase(),diff=document.querySelector('#mode').value==='diff';document.querySelectorAll('#cues tbody tr').forEach(r=>r.hidden=(!r.textContent.toLowerCase().includes(q)||(diff&&r.dataset.diff!=='1')))}}document.querySelector('#q').oninput=filter;document.querySelector('#mode').onchange=filter;</script></html>"""
    (root / "comparison.html").write_text(page, encoding="utf-8")
    if hashes != [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]:
        raise RuntimeError("An input changed while evaluation was running")
    return root, report

# -*- coding: utf-8 -*-
from __future__ import annotations
import copy, json, re, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import yaml
from autodub.srt_utils import load_srt_file
from autodub.translate.api import _api_call
from autodub.translate.cache import TranslationIncomplete
from autodub.translate.semantic import translate_semantic
OUT = ROOT / "_tmp" / "speed_probe"
OUT.mkdir(parents=True, exist_ok=True)

def redact(text):
    text = str(text or "")
    text = re.sub(r"sk-[A-Za-z0-9._-]{8,}", "[REDACTED]", text)
    text = re.sub(r"thk_live_[A-Za-z0-9._-]{8,}", "[REDACTED]", text)
    text = re.sub(r"nvapi-[A-Za-z0-9._-]{8,}", "[REDACTED]", text)
    text = re.sub(r"[0-9a-f]{8,}\.[A-Za-z0-9._-]{8,}", "[REDACTED]", text)
    return text[:800]

def find_source_srt():
    hits = sorted(ROOT.joinpath("output").rglob("*.src.srt"))
    for path in hits:
        if "BV1Za886oEAm" in str(path):
            return path
    if hits:
        return hits[-1]
    raise SystemExit("No .src.srt under output/")

def load_cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

def provider_params(tr, provider):
    p = provider.strip().lower()
    if p == "xkiro":
        return (tr.get("xkiro_api_key", ""), tr.get("xkiro_model") or "qwen/qwen3.5-flash:free",
                tr.get("xkiro_base_url") or "https://api.xkiro.com/v1", int(tr.get("xkiro_timeout", 120) or 120))
    if p == "zai":
        return (tr.get("zai_api_key", ""), tr.get("zai_model") or "glm-4.7-flash",
                tr.get("zai_base_url") or "https://api.z.ai/api/paas/v4", int(tr.get("zai_timeout", 120) or 120))
    raise ValueError("unsupported provider " + p)

def run_arm(name, segs, tr, provider, semantic_cfg, film_hint):
    key, model, base, timeout = provider_params(tr, provider)
    if not key:
        return dict(name=name, ok=False, error="missing_key", provider=provider, model=model)
    calls = []
    def ask(prompt):
        t0 = time.monotonic()
        rec = dict(seconds=None, chars=0, error=None)
        try:
            raw = _api_call(prompt, key, model, 0.2, provider, base, timeout, allow_model_fallback=False)
            rec["seconds"] = round(time.monotonic() - t0, 2)
            rec["chars"] = len(raw or "")
            calls.append(rec)
            return raw
        except Exception as exc:
            rec["seconds"] = round(time.monotonic() - t0, 2)
            rec["error"] = redact(exc)
            calls.append(rec)
            raise
    work = copy.deepcopy(segs)
    cache = OUT / (name.replace(" ", "_") + ".cache")
    t0 = time.monotonic()
    record = dict(name=name, provider=provider, model=model, cues=len(work),
                  cue_ids=[work[0].index, work[-1].index], cfg=semantic_cfg)
    try:
        translate_semantic(work, ask, semantic_cfg, cache_path=str(cache),
                           identity=[provider, model, name], film_hint=film_hint)
        record.update(ok=True, error=None)
    except TranslationIncomplete as exc:
        record.update(ok=False, error=redact(exc))
    except Exception as exc:
        record.update(ok=False, error=redact(exc))
    record["wall_s"] = round(time.monotonic() - t0, 2)
    record["api_calls"] = len(calls)
    record["api_seconds"] = round(sum(c["seconds"] or 0 for c in calls), 2)
    record["api_errors"] = sum(1 for c in calls if c.get("error"))
    record["calls"] = calls
    record["cues_per_min"] = (round(len(work) / record["wall_s"] * 60, 1) if record["wall_s"] else None)
    return record

def main():
    src = find_source_srt()
    all_segs = load_srt_file(str(src))
    start = 1800 if len(all_segs) > 1824 else 0
    sample = all_segs[start:start + 24]
    if len(sample) < 12:
        sample = all_segs[:24]
    cfg = load_cfg()
    tr = cfg.get("translation") or {}
    film_hint = "xiaoqian compilation"
    base_semantic = {"name_policy": "han_viet", "max_cps": 22, "max_chars_per_line": 42,
                     "max_lines_per_cue": 2, "vi_beautify_threshold": 8.0}
    arms = [
        ("A_xkiro_hien_tai", "xkiro", {**base_semantic, "vi_beautify": "auto", "semantic_group_max_cues": 12, "semantic_batch_cues": 20}),
        ("B_xkiro_tat_quality", "xkiro", {**base_semantic, "vi_beautify": False, "semantic_group_max_cues": 12, "semantic_batch_cues": 20}),
        ("C_xkiro_lo_6", "xkiro", {**base_semantic, "vi_beautify": False, "semantic_group_max_cues": 6, "semantic_batch_cues": 10}),
        ("D_zai_glm47_tat_quality", "zai", {**base_semantic, "vi_beautify": False, "semantic_group_max_cues": 12, "semantic_batch_cues": 20}),
    ]
    results = []
    meta = dict(source=str(src), sample_cues=len(sample),
                sample_index=[sample[0].index, sample[-1].index],
                sample_seconds=round(sample[-1].end - sample[0].start, 1),
                active_config_provider=tr.get("provider"),
                active_xkiro_model=tr.get("xkiro_model"),
                active_zai_model=tr.get("zai_model"),
                vi_beautify=tr.get("vi_beautify"))
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    for name, provider, semantic_cfg in arms:
        print("START", name, flush=True)
        rec = run_arm(name, sample, tr, provider, semantic_cfg, film_hint)
        results.append(rec)
        slim = {k: rec[k] for k in ("name","provider","model","ok","wall_s","api_calls","api_seconds","api_errors","cues_per_min","error") if k in rec}
        print(json.dumps(slim, ensure_ascii=False), flush=True)
        (OUT / "results.json").write_text(json.dumps(dict(meta=meta, results=results), ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", flush=True)

if __name__ == "__main__":
    main()

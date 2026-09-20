# Screen captions: packing and evaluation

`autodub.asr.merge.pack_screen_cues` delegates to `asr/screen_pack.py`. It accepts
the existing arguments plus keyword-only `speech_map`, `review` (a list filled
with diagnostics), and `protected_words`. Without an explicit map it uses the
thread's active SpeechMap. `caption_style: sentence` retains its previous path.

## Algorithm

- FunASR adapters keep raw sentence text until the complete character map is
  installed. Punctuation does not consume a speech mark.
- Apply the existing punctuation repair unchanged, then tokenize lexical text
  with **jieba >=0.42.1** (`HMM=False`). Restore punctuation at word edges;
  misplaced punctuation inside dictionary words is removed. Decimal values,
  signed numbers, Latin grades, explicit names and the protected phrases remain
  intact. Without jieba, a bounded maximum-match dictionary is available.
- Reconcile adjacent old cue windows only when a short run has exactly as many
  letters as observed marks; missing-mark fragments are not used to balance it.
  Other count mismatches are marked approximate. Fractional allocation stays
  within observed marks; a token spanning a real pause is withheld for review.
- Hard pauses (> `screen_gap`, default .32s), strong punctuation, known source
  boundaries and repeated-subject clause boundaries split bursts. Soft punctuation
  ends a phrase once it has four letters. A shortest-path score over **word**
  edges balances length, duration and smaller pauses (>=.18s).
- Defaults: soft ceiling 16 letters / 2.8s; hard ceiling 22 letters / 4.3s;
  minimum four letters, preferred short phrase around eight. Tiny fragments have
  a cost encouraging coalescing within the same burst. No coalescing across hard
  pauses or sentence stops. Abut gaps <=.10s; retain longer holes.
- Unsupported tiny fragments, word/pause conflicts and unusable clocks enter
  `review`. Diagnostics preserve original text and time ranges. A missing word
  is **not** counted as a repaired word.

CLI/GUI call `ensure_complete` before saving successful source subtitles or
starting translation. If text is withheld, it creates a new `caption-review-*`
directory containing `source.srt`, `packed.needs-review.srt`, `review.json` and
the active map, then raises `CaptionReviewRequired`. Rescue receives the raw
source, so it can resolve a clock problem without losing words first.
`asr.reuse_existing: false` now permits fresh ASR in both CLI and GUI even when
an old `.asr.srt` exists. This change does not edit local configuration.

The packer never substitutes `伟人` with `伪人` or `松姜` with `松江`.
Contextual corrections and subtitle import belong to P1. Display cues remain
separate from semantic translation/TTS groups; see [SEMANTIC_PIPELINE.md](SEMANTIC_PIPELINE.md).

## Compare without live ASR

From the project directory in PowerShell:

```powershell
$env:PYTHONUTF8='1'
$src = Get-ChildItem -LiteralPath 'output' -Recurse -Filter '*.asr.srt' |
  Where-Object Name -Like '*BV1Ewtd64EcR*' | Select-Object -First 1
.\venv\Scripts\python.exe scripts\compare_subtitles.py output\trung.srt $src.FullName `
  --speech-map $src.FullName.Replace('.asr.srt','.ban_do_thoai.json') `
  --repack --output-parent output
```

Two SRT arguments select Chinese segmentation QA. The previous four-argument
Chinese/Vietnamese comparison remains available. Each run creates a new
`caption-qa-*` directory with JSON, searchable/filterable HTML and (when packing)
an SRT. Inputs are hashed before/after; the tool never writes to a film's files.
An incomplete result is named `packed.needs-review.srt`.

Metrics: Han characters/cue, duration, overlaps, dictionary word tears, abut,
pause precision/recall/F1, exact Han Levenshtein CER and dictionary-token WER.
Chinese text anchors replace index matching. Pause >=180ms; an anchored boundary
matches within one Han character, with unmatched pauses retained in denominators.
Dictionary word tears are detector signals; punctuation and ASR words can differ.
CER/WER measure source differences, not translation fidelity or lip sync.

Exit **0** requires all configured thresholds and applicable golden checks to
pass; **1** means review/failure; invalid arguments return an error. Defaults:
average duration 1.4–2.6s, Han chars 7–14, cue ratio .75–1.25 of gold,
max duration <5s, no overlaps/invalid clocks, torn words <=5%, no withheld Han
characters. Pause F1 is reported but has no enforced minimum by default.
`--thresholds limits.json` overrides named values, for example:

```json
{"max_torn_word_pct": 1.0, "min_pause_f1": 0.5, "max_withheld_chars": 0}
```

## Recorded replay, 2026-09-14

The 672-cue CapCut source was used only for evaluation. Old ASR: 719 cues;
packed draft: 686. Average duration 1.475 → 1.489s; Han chars 8.200 → 8.290;
maximum duration 5.165 → 3.345s; overlaps 0 → 0; dictionary tears
33.797% → .292%; abut 18.942% → 28.175%; pause F1 .0637 → .0729.

**P0 acceptance is not complete.** The draft withholds 209 Han characters;
CER rises .0589 → .0888 and WER .0890 → .1064. The lower tear rate must not be
presented as overall quality improvement. The two repeated-subject clauses are
separate, good luck is separated from the next fragment, and weak fragments
enter review. The first `活下去`, `哥们`, `来一根` remain unresolved because old
text and clocks conflict. The source contains `来一来根`, and its total 5,953
letters/digits does not match 5,946 marks. A label-free time map cannot establish
which words were actually spoken. Gold itself also contains `伟人` at the start.
No gold substitution, live ASR, translation or TTS was used for this replay.

Tests cover all protected phrases at former character cuts, punctuation inside
words, numbers/grades, speaker/boundary metadata, silence, the first 30 original
ASR cues as a portable fixture, review gating, rescue, text anchors and CLI exit
codes. The existing screen/gop/asr-merge tests remain in place.

Implementation files: `autodub/asr/{screen_pack,merge,funasr,pipeline,common}.py`,
`main.py`, `autodub/server/pipeline.py`, `scripts/{compare_subtitles,caption_metrics}.py`
and `requirements.txt`. Tests: `test_screen_captions.py`, `test_caption_metrics.py`,
`test_caption_review_gate.py`, plus `tests/fixtures/caption_replay.json`.
Validation: 566 Python tests and one JavaScript test passed; selected Ruff fatal
checks, `pip check` and `git diff --check` passed. No model/network translation or
TTS service is called by these new tests.

"""Offline scheduler benchmark: 10,000 cues, four workers, no provider calls."""
import asyncio
import json
import os
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autodub.srt_utils import Segment
from autodub.tts import edge


async def fake_synth(*args, **kwargs):
    await asyncio.sleep(0)
    return True


async def legacy(segments, workdir, rate, concurrency):
    sem = asyncio.Semaphore(concurrency)
    paths = [None] * len(segments)

    async def attempt(i, seg):
        voice, pitch = edge._edge_voice_and_pitch(seg.voice)
        out = os.path.join(workdir, f'line_{i:05d}.mp3')
        async with sem:
            ok = await fake_synth(seg.text, voice, pitch, rate, out)
        if ok:
            paths[i] = out

    await asyncio.gather(*(attempt(i, seg) for i, seg in enumerate(segments)))
    return paths


segments = [Segment(i, i, i + 1, 'Xin chào') for i in range(10000)]
results = {}
with patch.object(edge, '_synth_one', new=fake_synth):
    for name, fn in [('legacy_gather', legacy), ('bounded_workers', edge._synth_all)]:
        samples = []
        for _ in range(3):
            tracemalloc.start()
            start = time.perf_counter()
            paths = asyncio.run(fn(segments, '.', '+0%', 4))
            elapsed = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert len(paths) == 10000 and all(paths)
            samples.append({'seconds': elapsed, 'peak_mib': peak / 1024**2})
        results[name] = {
            'median_seconds': statistics.median(s['seconds'] for s in samples),
            'median_peak_mib': statistics.median(s['peak_mib'] for s in samples),
            'samples': samples,
        }
print(json.dumps(results, indent=2))

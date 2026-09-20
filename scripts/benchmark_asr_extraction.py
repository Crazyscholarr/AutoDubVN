"""Measured FFmpeg stages; lossless candidates verified by decoded PCM hash.

Run: python scripts/benchmark_asr_extraction.py INPUT --seconds 60 --output report.json
Stages are end-to-end wall times, not falsely additive internal CPU timings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time


def benchmark(source, seconds=60, repeats=3):
    def timed(args):
        started = time.perf_counter()
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *args],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return time.perf_counter()-started

    result = {'source': str(source), 'seconds': seconds, 'repeats': repeats, 'stages_s': {}}
    with tempfile.TemporaryDirectory(prefix='asr-extraction-bench-') as td:
        base = ['-y', '-t', str(seconds), '-i', str(source), '-vn']
        stages = {
            'startup': ['-version'],
            'decode_null': [*base, '-f', 'null', '-'],
            'decode_resample_null': [*base, '-ac', '1', '-ar', '16000', '-f', 'null', '-'],
            'decode_loudnorm_resample_null': [*base, '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
                                             '-ac', '1', '-ar', '16000', '-f', 'null', '-'],
        }
        for name,args in stages.items():
            values = [timed(args) for _ in range(repeats)]
            result['stages_s'][name] = {'samples': values, 'median': statistics.median(values)}
        pcm = Path(td)/'normalized.wav'
        timed([*base,'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-ac','1','-ar','16000',
               '-c:a','pcm_s16le',str(pcm)])
        result['candidates'] = {}
        for codec,level in [('flac',5),('flac',0),('pcm_s16le',None)]:
            name = f'{codec}_{level}'
            dest=Path(td)/(name+('.flac' if codec=='flac' else '.wav'))
            encoding=['-c:a',codec]+(['-compression_level',str(level)] if level is not None else [])
            samples=[timed([*base,'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-ac','1','-ar','16000',
                            *encoding,str(dest)]) for _ in range(repeats)]
            encode_only=[timed(['-y','-i',str(pcm),*encoding,str(dest)]) for _ in range(repeats)]
            decoded=subprocess.run(['ffmpeg','-v','error','-i',str(dest),'-f','s16le','-'],
                                   check=True,capture_output=True).stdout
            started=time.perf_counter()
            with open(Path(td)/'copy.bin','wb') as stream:
                stream.write(dest.read_bytes())
                stream.flush()
                import os
                os.fsync(stream.fileno())
            result['candidates'][name]=dict(total_samples=samples,total_median=statistics.median(samples),
                encode_from_pcm_samples=encode_only,bytes=dest.stat().st_size,
                disk_copy_flush_s=time.perf_counter()-started,pcm_sha256=hashlib.sha256(decoded).hexdigest())
        hashes={v['pcm_sha256'] for v in result['candidates'].values()}
        result['lossless_identical_pcm']=len(hashes)==1
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--output',default='asr-extraction-benchmark.json')
    args=parser.parse_args()
    Path(args.output).write_text(json.dumps(benchmark(args.source,args.seconds),indent=2),encoding='utf-8')

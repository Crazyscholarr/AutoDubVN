"""Offline defect observations. Does not repair code or touch production data.

Run with the project's Python. Every probe uses temporary files or mock transports.
These are observations of known defects, not passing product acceptance tests.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autodub import utils
from autodub.srt_utils import Segment
from autodub.tts import capcut, vieneu, timeline, concat
from autodub.video import process
from autodub.translate.cache import ChunkCache
from autodub.server import config_api, helpers
from autodub.content_pipeline.store import ContentStore
import main as cli
import yaml

PROBES = []


def probe(key):
    def register(fn):
        PROBES.append((key, fn))
        return fn
    return register


@probe('A01')
def capcut_default_narrator():
    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(timeline, 'assign_voices'), \
         mock.patch.object(timeline, '_synth_all_capcut') as synth:
        try:
            timeline.build_voice_track([Segment(1, 0, 2, 'Xin chào')], td, 3, engine='capcut')
        except AttributeError as exc:
            assert not synth.called
            return {'error': str(exc), 'network_calls': 0}
        raise AssertionError('No longer reproduced')


@probe('A02')
def missing_cancel_token_other_engines():
    seen = {}
    event = threading.Event()
    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(timeline, 'active_cancel_event', return_value=event), \
         mock.patch.object(timeline, 'assign_voices'):
        for engine, fn in [('capcut', '_synth_all_capcut'), ('vieneu', '_synth_all_vieneu')]:
            def observe(*args, **kwargs):
                seen[engine] = kwargs.get('cancel_event') is event
                raise InterruptedError('stop probe')
            with mock.patch.object(timeline, fn, side_effect=observe):
                try:
                    timeline.build_voice_track([Segment(1, 0, 2, 'Xin chào')], td, 3,
                                               engine=engine, narrator={'voice': 'voice'})
                except InterruptedError:
                    pass
    assert seen == {'capcut': False, 'vieneu': False}
    return {'received_job_cancel_event': seen}


@probe('A03')
def capcut_corrupt_cache():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'cached.mp3'
        path.write_bytes(b'not audio' * 100)
        with mock.patch.object(capcut, '_record_capcut_voice_status'), \
             mock.patch.object(capcut, '_load_capcut_client') as load:
            accepted = capcut._synth_one_capcut('Xin chào', 'voice', '1.0', str(path))
        assert accepted and not load.called
        return {'invalid_bytes': path.stat().st_size, 'accepted_as_success': accepted}


@probe('A04')
def vieneu_cancel_deletes_old_file():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'old.wav'
        path.write_bytes(b'previous completed audio')
        event = threading.Event()
        event.set()
        try:
            vieneu._synth_one_vieneu('Xin chào', None, str(path), cancel_event=event)
        except InterruptedError:
            pass
        assert not path.exists()
        return {'previous_output_deleted_before_inference': True}


@probe('A05')
def retry_wait_cancel_latency():
    event = threading.Event()
    timer = threading.Timer(.03, event.set)
    with mock.patch.object(utils, 'active_cancel_event', return_value=event):
        started = time.monotonic()
        timer.start()
        try:
            utils.wait_or_cancel(.5)
        except InterruptedError:
            elapsed = time.monotonic() - started
        finally:
            timer.join()
    assert elapsed >= .45
    return {'cancel_at_seconds': .03, 'wait_returned_seconds': round(elapsed, 3)}


@probe('A06')
def nested_yaml_keys_overwritten():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'config.yaml'
        path.write_text('translation:\n  chunk_size: 25\n  nested:\n    chunk_size: 3\n', encoding='utf-8')
        with mock.patch.object(config_api, 'CONFIG_PATH', str(path)):
            config_api._patch_yaml_section('translation', {'chunk_size': 80}, ('chunk_size',))
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
        assert value['translation']['nested']['chunk_size'] == 80
        return value


@probe('A07')
def inline_yaml_corrupted_by_save():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'config.yaml'
        path.write_text('translation: {provider: nvidia}\ntts:\n  engine: edge\n', encoding='utf-8')
        with mock.patch.object(config_api, 'CONFIG_PATH', str(path)):
            config_api._patch_yaml_section('translation', {'chunk_size': 80}, ('chunk_size',))
        try:
            yaml.safe_load(path.read_text(encoding='utf-8'))
        except yaml.YAMLError as exc:
            return {'saved_file_is_invalid_yaml': True, 'error': type(exc).__name__}
        raise AssertionError('No longer reproduced')


@probe('A08')
def cli_yaml_root_types():
    failures = {}
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'config.yaml'
        for name, content in [('empty', ''), ('null_section', 'translation: null'), ('list_root', '[]')]:
            path.write_text(content, encoding='utf-8')
            try:
                cfg = cli.load_config(str(path))
                cfg.get('ffmpeg_dir')
            except (TypeError, AttributeError) as exc:
                failures[name] = type(exc).__name__
    assert len(failures) == 3
    return failures


@probe('A09')
def malformed_cache_root():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'cache.json'
        path.write_text('[]', encoding='utf-8')
        cache = ChunkCache(str(path))
        try:
            cache.get('key')
        except AttributeError as exc:
            return {'error': str(exc)}
        raise AssertionError('No longer reproduced')


@probe('A10')
def content_search_applies_limit_before_filter():
    with tempfile.TemporaryDirectory() as td:
        store = ContentStore(str(Path(td) / 'ideas.db'))
        store.upsert([{'id': 'old', 'title_original': 'needle'}, {'id': 'new', 'title_original': 'unrelated'}])
        conn = store.connect()
        with conn:
            conn.execute("UPDATE content_ideas SET updated_at='2000' WHERE id='old'")
            conn.execute("UPDATE content_ideas SET updated_at='2026' WHERE id='new'")
        conn.close()
        result = store.list(search='needle', limit=1)
        assert result == [] and store.get('old')
        return {'matches_exist': 1, 'matches_returned': 0}


@probe('A11')
def picture_lock_swallows_cancel():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'source.wav'
        src.write_bytes(b'a' * 1000)
        with mock.patch.object(process, 'ffprobe_duration', return_value=2), \
             mock.patch.object(process, 'run', side_effect=InterruptedError('cancel')):
            result = process.lock_audio_to_picture_duration(str(src), 10)
        assert result == str(src)
        return {'cancel_propagated': False, 'returned_original': True}


@probe('A12')
def picture_lock_trusts_damaged_output():
    with tempfile.TemporaryDirectory() as td:
        src, dest = Path(td) / 'source.wav', Path(td) / 'source.picture.wav'
        src.write_bytes(b'a' * 1000)
        dest.write_bytes(b'corrupt' * 100)
        process._write_lock_fingerprint(str(dest), process._lock_fingerprint(str(src), 10, 48000))
        with mock.patch.object(process, 'ffprobe_duration', return_value=2), \
             mock.patch.object(process, 'run') as run:
            result = process.lock_audio_to_picture_duration(str(src), 10)
        assert result == str(dest) and not run.called
        return {'corrupt_output_reused': True, 'output_probed': False}


@probe('A13')
def narration_drops_unpaired_text():
    result = timeline.build_narration_timeline(['First', 'Second'], [1.0])
    assert len(result) == 1
    return {'texts': 2, 'timeline_items': len(result)}


@probe('A14')
def narration_metadata_overrides_clock():
    result = timeline.build_narration_timeline(['First'], [1.0], [{'start': 10, 'end': -2}])
    assert result[0]['end'] < result[0]['start']
    return result[0]


@probe('A15')
def manual_tts_missing_file_omitted():
    with tempfile.TemporaryDirectory() as td:
        src, missing, out = Path(td) / 'valid.wav', Path(td) / 'missing.wav', Path(td) / 'out.wav'
        src.write_bytes(b'valid')
        def join(paths, destination, **kwargs):
            Path(destination).write_bytes(b'joined')
        with mock.patch.object(concat, '_synth_all', new=mock.AsyncMock(return_value=[str(src), str(missing)])), \
             mock.patch.object(concat, 'ffprobe_duration', return_value=1), \
             mock.patch.object(concat, 'concat_audio_clips', side_effect=join):
            result = concat.synthesize_text_audio('First. Second.', td, str(out),
                utterances=[{'text': 'First.'}, {'text': 'Second.'}])
        assert len(result['segments']) == 1 and result['chunks'] == 2
        return {'reported_chunks': result['chunks'], 'actual_segments': len(result['segments'])}


@probe('A16')
def same_basename_output_collision():
    left = helpers._output_dir_for_video('C:/source-one/episode.mp4')
    right = helpers._output_dir_for_video('D:/source-two/episode.mp4')
    assert left == right
    return {'distinct_source_paths_share_output': True, 'folder_name': Path(left).name}


@probe('A17')
def lock_failure_overwrites_old_output():
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / 'source.wav', Path(td) / 'locked.wav'
        src.write_bytes(b'source')
        out.write_bytes(b'good')
        def fail(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'partial')
            raise RuntimeError('encoder failed')
        with mock.patch.object(process, 'ffprobe_duration', return_value=2), \
             mock.patch.object(process, 'run', side_effect=fail):
            process.lock_audio_to_picture_duration(str(src), 10, out_path=str(out))
        assert out.read_bytes() == b'partial'
        return {'previous_output_overwritten': True}


@probe('A18')
def asr_accepts_other_audio_fingerprint():
    from autodub.asr.long_audio import compatible_checkpoint
    old = dict(version=2, audio_sha256='audio-one', duration=60,
               model='same-model', language='zh', options={}, chunk_seconds=60, overlap=1)
    new = dict(old, audio_sha256='different-audio', duration=61)
    accepted = compatible_checkpoint(old, new)
    assert accepted
    return {'different_audio_sha256': True, 'checkpoint_compatible': accepted}


@probe('A19')
def legacy_translation_ignores_changed_model():
    from autodub.translate import pipeline
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / 'cache.json')
        with mock.patch.object(pipeline, '_api_call', return_value='["Xin chào bạn."]') as call:
            for model in ['model-one', 'different-model']:
                pipeline.translate_segments([Segment(1, 0, 3, '你好')], 'fake-key',
                    model=model, cache_path=path, translation_cfg={'semantic_translation': False},
                    shorten_long_lines_enabled=False)
        assert call.call_count == 1
        return {'models_requested': 2, 'provider_calls': call.call_count}


@probe('A20')
def render_concat_failure_overwrites_video():
    from autodub.server import render
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'good.mp4'
        out.write_bytes(b'good-video')
        def fail(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b'partial-video')
            raise RuntimeError('disk full')
        with mock.patch.object(render, 'run', side_effect=fail):
            try:
                render._concat_mp4_parts(['one.mp4', 'two.mp4'], str(out))
            except RuntimeError:
                pass
        assert out.read_bytes() == b'partial-video'
        return {'previous_video_overwritten': True}


@probe('A21')
def legacy_source_history_without_fingerprint():
    with tempfile.TemporaryDirectory() as td:
        store = ContentStore(str(Path(td) / 'ideas.db'))
        url = 'https://example.org/story/one'
        store.upsert([{'id': 'old', 'source_url': url, 'status': 'used', 'usage_count': 1}])
        result = store.source_history([url])
        assert result == {}
        return {'used_source_exists': True, 'returned_history': result}


@probe('A22')
def queued_asr_cancel_keeps_global_busy_flag():
    from autodub.server import state
    sample = {'running': True, 'busy': '', 'queue': []}
    with mock.patch.object(state, 'STATE', sample), \
         mock.patch.object(state, '_CANCEL_EVENT', threading.Event()), \
         mock.patch.object(state, 'JOB_MANAGER') as manager:
        manager.submit.return_value = 'fake-job'
        state.submit_job(lambda: None, name='ASR retry',
                         metadata={'kind': 'asr_rerecognize', 'queue_id': 1})
        manager.submit.call_args.kwargs['on_cancel']()
        assert sample['running'] is True
    return {'after_cancel_running': True, 'new_pipeline_blocked': True}


@probe('A23')
def content_analysis_swallows_cancellation():
    from autodub.content_pipeline import analyze
    from autodub import translate
    with mock.patch.object(analyze, '_provider_params', return_value=(
            'nvidia', 'fake-key', 'model', 'https://example.invalid', 1)), \
         mock.patch.object(translate, '_api_call', side_effect=InterruptedError('cancelled')):
        result = analyze.analyze_record({'id': 'x', 'content': 'Test story'}, {})
    assert result['analysis_provider'] == 'heuristic-fallback'
    return {'cancel_propagated': False, 'provider': result['analysis_provider']}


@probe('A24')
def importing_audio_overwrites_busy_manual_state():
    from autodub.server.story import audio
    state = {'running': True, 'busy': 'TTS running',
             'manual': {'working': True, 'audio_path': 'active.wav', 'rev': 1}}
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'other.wav'
        path.write_bytes(b'fake-media')
        with mock.patch.object(audio, 'STATE', state), \
             mock.patch.object(audio, 'ffprobe_duration', return_value=2):
            result, status = audio.api_manual_use_audio({'path': str(path)})
        assert status == 200 and not state['manual']['working']
        assert state['running'] and state['manual']['audio_path'] == str(path.resolve())
    return {'status': status, 'running': True, 'manual_working': False,
            'active_audio_replaced': True}


@probe('A25')
def concurrent_content_patches_lose_unrelated_edit():
    with tempfile.TemporaryDirectory() as td:
        store = ContentStore(str(Path(td) / 'ideas.db'))
        store.upsert([{'id': 'x', 'title_localized': 'old', 'notes': 'old'}])
        original_get = store.get
        both_read = threading.Barrier(2)
        first_done = threading.Event()
        seen = set()
        failures = []

        def interleaved_get(record_id):
            name = threading.current_thread().name
            item = original_get(record_id)
            if name not in seen:
                seen.add(name)
                both_read.wait(timeout=5)
                if name == 'notes-editor':
                    assert first_done.wait(5)
            return item

        def edit(values, first=False):
            try:
                store.patch('x', values)
            except Exception as exc:
                failures.append(str(exc))
            finally:
                if first:
                    first_done.set()

        with mock.patch.object(store, 'get', side_effect=interleaved_get):
            workers = [threading.Thread(name='title-editor', target=edit,
                        args=({'title_localized': 'new'}, True)),
                       threading.Thread(name='notes-editor', target=edit,
                        args=({'notes': 'new'},))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(12)
            assert not any(worker.is_alive() for worker in workers)
        assert not failures, failures
        result = original_get('x')
        assert result['title_localized'] == 'old' and result['notes'] == 'new'
        return {'title_edit_lost': True, 'final_title': result['title_localized'],
                'final_notes': result['notes']}


def main():
    results = []
    for key, fn in PROBES:
        try:
            evidence = fn()
            results.append({'id': key, 'probe': fn.__name__, 'reproduced': True, 'evidence': evidence})
        except Exception as exc:
            results.append({'id': key, 'probe': fn.__name__, 'reproduced': False,
                            'probe_error': f'{type(exc).__name__}: {exc}'})
    target = ROOT / '_tmp' / 'audit_reproductions_20260920.json'
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any(not row['reproduced'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())

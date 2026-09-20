import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autodub.video import extract


class AudioReuse(unittest.TestCase):
    def test_existing_flac_is_reused_even_when_requested_wav(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            with patch.object(extract,'ffprobe_duration',return_value=2), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio') as make:
                result=extract.ensure_audio(str(source),str(flac.with_suffix('.wav')),trim_duration=2)
                self.assertEqual(result,str(flac))
                make.assert_not_called()

    def test_changed_trim_or_filter_prevents_stale_reuse(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            wav=Path(td)/'audio16k.wav'
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            with patch.object(extract,'extract_audio',side_effect=make) as generate, \
                 patch.object(extract,'ffprobe_duration',return_value=2), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[{'sample_rate':'16000','channels':1}]}))):
                extract.ensure_audio(str(source),str(wav),trim_start=1,trim_duration=2)
                extract.ensure_audio(str(source),str(wav),trim_start=1,trim_duration=2)
                self.assertEqual(generate.call_count,1)
                extract.ensure_audio(str(source),str(wav),trim_start=3,trim_duration=2)
                self.assertEqual(generate.call_count,2)
                extract.ensure_audio(str(source),str(wav),trim_start=3,trim_duration=2,loudnorm=False)
                self.assertEqual(generate.call_count,3)

    def test_trusted_sidecar_reuses_loudnorm_length_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            stat=source.stat()
            sidecar=Path(str(flac)+'.source.json')
            sidecar.write_text(json.dumps(dict(
                source=os.path.normcase(os.path.abspath(source)),
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, sr=16000,
                loudnorm=True, trim_start=0.0, trim_duration=None,
            )), encoding='utf-8')
            def durations(path):
                return 7217.45 if str(path).endswith('.flac') else 7215.33
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=7215.33,format_duration=7215.33)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio') as make:
                result=extract.ensure_audio(str(source),str(flac))
                self.assertEqual(result,str(flac))
                make.assert_not_called()
            stored=json.loads(sidecar.read_text(encoding='utf-8'))
            self.assertAlmostEqual(stored['extracted_duration'],7217.45)

    def test_recorded_extract_duration_mismatch_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            stat=source.stat()
            sidecar=Path(str(flac)+'.source.json')
            sidecar.write_text(json.dumps(dict(
                source=os.path.normcase(os.path.abspath(source)),
                size=stat.st_size, mtime_ns=stat.st_mtime_ns, sr=16000,
                loudnorm=True, trim_start=0.0, trim_duration=None,
                extracted_duration=7217.45,
            )), encoding='utf-8')
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            def durations(path):
                return 100.0 if str(path).endswith('.flac') else 7215.33
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=7215.33,format_duration=7215.33)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio',side_effect=make) as generate:
                extract.ensure_audio(str(source),str(flac))
                self.assertEqual(generate.call_count,1)

    def test_mtime_mismatch_still_reuses_loudnorm_extract(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            stat=source.stat()
            sidecar=Path(str(flac)+'.source.json')
            sidecar.write_text(json.dumps(dict(
                source=os.path.normcase(os.path.abspath(source)),
                size=stat.st_size, mtime_ns=1, sr=16000,
                loudnorm=True, trim_start=0.0, trim_duration=None,
                extracted_duration=7217.45,
            )), encoding='utf-8')
            def durations(path):
                return 7217.45 if str(path).endswith('.flac') else 7215.33
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=7215.33,format_duration=7215.33)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio') as make:
                result=extract.ensure_audio(str(source),str(flac))
                self.assertEqual(result,str(flac))
                make.assert_not_called()

    def test_no_sidecar_loudnorm_window_reuses(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            def durations(path):
                return 7217.45 if str(path).endswith('.flac') else 7215.33
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=7215.33,format_duration=7215.33)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio') as make:
                result=extract.ensure_audio(str(source),str(flac))
                self.assertEqual(result,str(flac))
                make.assert_not_called()
            stored=json.loads(Path(str(flac)+'.source.json').read_text(encoding='utf-8'))
            self.assertAlmostEqual(stored['extracted_duration'],7217.45)

    def test_size_mismatch_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            sidecar=Path(str(flac)+'.source.json')
            sidecar.write_text(json.dumps(dict(
                source=os.path.normcase(os.path.abspath(source)),
                size=1, mtime_ns=1, sr=16000, loudnorm=True,
                trim_start=0.0, trim_duration=None, extracted_duration=7217.45,
            )), encoding='utf-8')
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            def durations(path):
                return 7217.45 if str(path).endswith('.flac') else 7215.33
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=7215.33,format_duration=7215.33)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio',side_effect=make) as generate:
                extract.ensure_audio(str(source),str(flac))
                self.assertEqual(generate.call_count,1)

    def test_loudnorm_off_rejects_stretched_duration(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            wav=Path(td)/'audio16k.wav'; wav.write_bytes(b'a'*2000)
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            def durations(path):
                return 4.0 if str(path).endswith('.wav') else 2.0
            with patch.object(extract,'ffprobe_duration',side_effect=durations), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=2.0,format_duration=2.0)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio',side_effect=make) as generate:
                extract.ensure_audio(str(source),str(wav),loudnorm=False)
                self.assertEqual(generate.call_count,1)

    def test_missing_file_log_includes_path_and_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            wav=Path(td)/'audio16k.wav'
            Path(str(wav)+'.source.json').write_text('{}', encoding='utf-8')
            notes=[]
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            with patch.object(extract,'log',side_effect=lambda msg,kind='info': notes.append(msg)), \
                 patch.object(extract,'ffprobe_duration',return_value=2), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=2.0,format_duration=2.0)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio',side_effect=make):
                extract.ensure_audio(str(source),str(wav),trim_duration=2)
            joined='\n'.join(notes)
            self.assertIn('missing_file', joined)
            self.assertIn(str(wav), joined)
            self.assertIn('có sidecar', joined)

    def test_truncated_extract_is_logged_then_unlinked(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            flac=Path(td)/'audio16k.flac'; flac.write_bytes(b'')
            notes=[]
            def make(video,out,**kwargs):
                Path(out).write_bytes(b'a'*2000)
                return out
            with patch.object(extract,'log',side_effect=lambda msg,kind='info': notes.append(msg)), \
                 patch.object(extract,'ffprobe_duration',return_value=2), \
                 patch.object(extract,'probe_media_clocks',
                              return_value=dict(audio_duration=2.0,format_duration=2.0)), \
                 patch.object(extract,'run',return_value=SimpleNamespace(stdout=json.dumps(
                     {'streams':[{'sample_rate':'16000','channels':1}]}))), \
                 patch.object(extract,'extract_audio',side_effect=make):
                extract.ensure_audio(str(source),str(flac),trim_duration=2)
            self.assertIn('size=0', '\n'.join(notes))
            self.assertFalse(flac.exists() and flac.stat().st_size == 0)

    def test_extract_writes_partial_then_replaces(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.mp4'; source.write_bytes(b'source')
            dest=Path(td)/'audio16k.wav'
            seen=[]
            def fake_run(cmd, **kwargs):
                if cmd and cmd[0]=='ffprobe':
                    return SimpleNamespace(stdout=json.dumps(
                        {'streams':[{'sample_rate':'16000','channels':1}]}), stderr='')
                if cmd[:3]==['ffmpeg','-hide_banner','-i']:
                    return SimpleNamespace(stdout='', stderr='mean_volume: -20.0 dB')
                seen.append(cmd)
                Path(cmd[-1]).write_bytes(b'a'*2000)
                return SimpleNamespace(stdout='', stderr='')
            with patch.object(extract,'run',side_effect=fake_run), \
                 patch.object(extract,'ffprobe_duration',return_value=2):
                extract.extract_audio(str(source), str(dest), loudnorm=False)
            self.assertTrue(seen)
            cmd=seen[0]
            self.assertEqual(cmd[-3:-1], ['-f','wav'])
            self.assertTrue(cmd[-1].endswith('.partial.wav'))
            self.assertFalse(cmd[-1].endswith('.wav.partial'))
            self.assertTrue(dest.exists())
            self.assertGreater(dest.stat().st_size, 1024)
            self.assertFalse((Path(str(dest)+'.partial')).exists())


class TempCleanup(unittest.TestCase):
    def test_cleanup_keeps_asr_extract_and_deletes_dub(self):
        from autodub.server import helpers
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td)/'output'/'film'/'_tmp'
            tmp.mkdir(parents=True)
            flac=tmp/'audio16k.flac'; flac.write_bytes(b'a'*2000)
            sidecar=Path(str(flac)+'.source.json'); sidecar.write_text('{}', encoding='utf-8')
            wav=tmp/'audio16k.wav'; wav.write_bytes(b'b'*2000)
            dub=tmp/'dub.wav'; dub.write_bytes(b'c'*2000)
            picture=tmp/'dub.picture.wav'; picture.write_bytes(b'd'*2000)
            with patch.object(helpers,'HERE',td):
                result=helpers._cleanup_temp_files()
            self.assertTrue(flac.exists())
            self.assertTrue(sidecar.exists())
            self.assertTrue(wav.exists())
            self.assertFalse(dub.exists())
            self.assertFalse(picture.exists())
            self.assertEqual(result['files'],2)

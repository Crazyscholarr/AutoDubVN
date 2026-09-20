"""Production regressions: cancellation, bounded work and atomic media outputs."""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from autodub.srt_utils import Segment
from autodub.tts import edge
from autodub.video import assemble, process


class EdgeProduction(unittest.IsolatedAsyncioTestCase):
    async def test_zero_concurrency_rejected_without_hanging(self):
        with self.assertRaises(ValueError):
            await asyncio.wait_for(edge._synth_all([], '.', '+0%', 0), .5)

    async def test_large_batch_has_bounded_tasks_and_ordered_results(self):
        segs = [Segment(i, i, i + 1, 'Xin chào') for i in range(10000)]
        peak = 0

        async def synth(*args, **kwargs):
            nonlocal peak
            peak = max(peak, len(asyncio.all_tasks()))
            await asyncio.sleep(0)
            return True

        with mock.patch.object(edge, '_synth_one', side_effect=synth):
            paths = await edge._synth_all(segs, '.', '+0%', 4)
        self.assertLessEqual(peak, 6)
        self.assertEqual(len(paths), len(segs))
        self.assertTrue(paths[9999].endswith('line_09999.mp3'))

    async def test_worker_failure_drains_siblings_before_return(self):
        cancelled = asyncio.Event()
        entered = asyncio.Event()

        async def synth(text, *args, **kwargs):
            if text == 'Một':
                await entered.wait()
                raise InterruptedError('cancel')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with mock.patch.object(edge, '_synth_one', side_effect=synth):
            with self.assertRaises(InterruptedError):
                await edge._synth_all([Segment(1, 0, 1, 'Một'),
                                       Segment(2, 1, 2, 'Hai')], '.', '+0%', 2)
        self.assertTrue(cancelled.is_set())

    async def test_cancel_inflight_preserves_previous_audio(self):
        event = threading.Event()
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def save(path):
            Path(path).write_bytes(b'partial')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'line.mp3'
            out.write_bytes(b'previous-good-audio')
            with mock.patch('edge_tts.Communicate') as comm:
                comm.return_value.save = save
                task = asyncio.create_task(edge._synth_one(
                    'Xin chào', 'vi-VN-NamMinhNeural', '+0Hz', '+0%', str(out),
                    cancel_event=event))
                await entered.wait()
                event.set()
                with self.assertRaises(InterruptedError):
                    await asyncio.wait_for(task, .8)
            self.assertTrue(stopped.is_set())
            self.assertEqual(out.read_bytes(), b'previous-good-audio')
            self.assertEqual(list(Path(td).iterdir()), [out])

    async def test_success_atomically_replaces_previous_audio(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'line.mp3'
            out.write_bytes(b'old')

            async def save(path):
                self.assertNotEqual(Path(path), out)
                self.assertEqual(out.read_bytes(), b'old')
                Path(path).write_bytes(b'new')

            with mock.patch('edge_tts.Communicate') as comm:
                comm.return_value.save = save
                self.assertTrue(await edge._synth_one(
                    'Xin chào', 'vi-VN-NamMinhNeural', '+0Hz', '+0%', str(out)))
            self.assertEqual(out.read_bytes(), b'new')
            self.assertEqual(list(Path(td).iterdir()), [out])


class AtomicMedia(unittest.TestCase):
    def test_stream_failure_never_falls_back_to_full_ram_allocation(self):
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / 'source.wav', Path(td) / 'out.wav'
            src.write_bytes(b'source')

            def mix(items, duration, path, *args, **kwargs):
                Path(path).write_bytes(b'audio')

            with mock.patch.object(assemble, '_assemble_stream_torch', side_effect=MemoryError), \
                 mock.patch.object(assemble, '_assemble_torch') as ram, \
                 mock.patch.object(assemble, '_mix_batch', side_effect=mix):
                assemble.assemble_timeline_audio([str(src)], [0], 2, str(out), mode='stream')
            ram.assert_not_called()
            self.assertEqual(out.read_bytes(), b'audio')

    def test_failed_timeline_mix_preserves_destination(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out.wav'
            out.write_bytes(b'good')

            def fail(clips, starts, duration, temp, *args):
                Path(temp).write_bytes(b'partial')
                raise InterruptedError('cancel')

            with mock.patch.object(assemble, '_assemble_timeline_audio', side_effect=fail):
                with self.assertRaises(InterruptedError):
                    assemble.assemble_timeline_audio([], [], 2, str(out))
            self.assertEqual(out.read_bytes(), b'good')
            self.assertEqual(list(Path(td).iterdir()), [out])

    def test_timeline_rejects_missing_positions_instead_of_truncating_zip(self):
        with self.assertRaises(ValueError):
            assemble.assemble_timeline_audio(['clip.wav'], [], 2, 'out.wav')

    def test_failed_speed_change_preserves_destination_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'good.wav'
            out.write_bytes(b'good')

            def fail(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b'broken')
                raise RuntimeError('encoder failed')

            with mock.patch.object(process, 'run', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    process.change_speed('source.wav', str(out), 1.5)
            self.assertEqual(out.read_bytes(), b'good')
            self.assertEqual(list(Path(td).iterdir()), [out])

    def test_failed_copy_reencodes_even_if_partial_file_exists(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'new.wav'
            calls = []

            def run(cmd, **kwargs):
                calls.append(cmd)
                Path(cmd[-1]).write_bytes(b'bad' if len(calls) == 1 else b'good')
                if len(calls) == 1:
                    raise RuntimeError('incompatible codec')

            with mock.patch.object(process, 'run', side_effect=run):
                process.change_speed('source.mp3', str(out), 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(out.read_bytes(), b'good')

    def test_nonfinite_speed_rejected_before_starting_encoder(self):
        with mock.patch.object(process, 'run') as run:
            for value in (float('inf'), float('nan'), 0, -1):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    process.change_speed('in.wav', 'out.wav', value)
            run.assert_not_called()
        self.assertEqual(process._atempo_chain(float('inf')), 'atempo=1.000000')

    def test_cancelled_copy_concat_does_not_start_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'source.wav'
            src.write_bytes(b'source')
            with mock.patch.object(assemble, '_probe_total_duration', return_value=1), \
                 mock.patch.object(assemble, '_concat_copy_chunks', side_effect=InterruptedError), \
                 mock.patch.object(assemble, '_concat_audio_chunks') as fallback:
                with self.assertRaises(InterruptedError):
                    assemble._join_audio_chunks([str(src)], str(Path(td) / 'out.wav'), 48000, td)
            fallback.assert_not_called()

    def test_missing_concat_input_does_not_silently_drop_dialogue(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'out.wav'
            out.write_bytes(b'good')
            with self.assertRaises(FileNotFoundError):
                assemble.concat_audio_clips([str(Path(td) / 'missing.wav')], str(out))
            self.assertEqual(out.read_bytes(), b'good')

    def test_failed_concat_preserves_destination(self):
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / 'source.wav', Path(td) / 'out.wav'
            src.write_bytes(b'source')
            out.write_bytes(b'good')

            def fail(paths, temp, *args, **kwargs):
                Path(temp).write_bytes(b'broken')
                raise RuntimeError('failed')

            with mock.patch.object(assemble, '_concat_audio_chunks', side_effect=fail):
                with self.assertRaises(RuntimeError):
                    assemble.concat_audio_clips([str(src)], str(out))
            self.assertEqual(out.read_bytes(), b'good')
            self.assertEqual(len(list(Path(td).iterdir())), 2)


class ClipMeasurements(unittest.TestCase):
    def test_unchanged_clips_are_measured_only_once(self):
        from autodub.tts import timeline
        with tempfile.TemporaryDirectory() as td:
            segs = [Segment(i, i * 5, i * 5 + 4, 'Xin chào') for i in range(100)]
            clips = [str(Path(td) / f'{i}.wav') for i in range(100)]
            with mock.patch.object(timeline, '_synth_all', new=mock.AsyncMock(return_value=clips)), \
                 mock.patch.object(timeline, 'ffprobe_duration', return_value=1.0) as probe:
                result, _, _ = timeline.build_voice_track(segs, td, 500, trim=False)
            self.assertEqual(result, clips)
            self.assertEqual(probe.call_count, 100)
            self.assertTrue(all(seg.voice_duration == 1.0 for seg in segs))


if __name__ == '__main__':
    unittest.main()

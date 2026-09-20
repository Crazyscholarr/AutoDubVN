"""Review contract: raw issues + persisted decisions -> one effective gate."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from autodub.asr.review import review_state
from autodub.asr import nonspeech as store


def issue(i=0, **kw):
    return dict(dict(start=10*i,end=10*i+1,reason='missing_speech_marks',
                     text='',timestamps=[],withheld=True),**kw)


def decision(i=0, value='CONFIRMED_NOISE'):
    return dict(start=10*i,end=10*i+1,resolution=value)


class ReviewStateContract(unittest.TestCase):
    def test_matrix_raw_missing_or_empty_requires_review(self):
        for reason in ['missing_speech_marks','suspicious_chunk']:
            self.assertEqual(review_state([issue(reason=reason)])['unresolved'],1)
            for resolution in ['CONFIRMED_NOISE','CONFIRMED_EFFECT','IGNORED','RESOLVED']:
                with self.subTest(reason=reason,resolution=resolution):
                    state=review_state([issue(reason=reason)],[decision(value=resolution)])
                    self.assertEqual(state['unresolved'],0)
                    self.assertTrue(state['can_continue_translation'])
                    self.assertTrue(state['can_continue_tts'])
                    self.assertTrue(state['items'][0]['raw_withheld'])

    def test_confirmed_speech_still_requires_repair(self):
        self.assertEqual(review_state([issue()],[decision(value='CONFIRMED_SPEECH')])['unresolved'],1)

    def test_unknown_resolution_never_grants_permission(self):
        self.assertEqual(review_state([issue()],[decision(value='future_unknown')])['unresolved'],1)

    def test_duplicate_diagnostics_and_reconciliation_are_idempotent(self):
        rows=[issue(),issue(withheld=False),issue(1),issue(1)]
        decisions=[decision()]
        a=review_state(rows,decisions,'source')
        b=review_state(a['items'],decisions,'source')
        c=review_state(b['items'],decisions,'source')
        self.assertEqual(a,b);self.assertEqual(b,c)
        self.assertEqual(a['total_issues'],2)
        self.assertEqual(a['unresolved'],1)
        self.assertEqual([r['issue_id'] for r in a['items']],
                         [r['issue_id'] for r in review_state(list(reversed(rows)),decisions,'source')['items']])

    def test_one_blocker_among_ten_resolved(self):
        state=review_state([issue(i) for i in range(11)],[decision(i) for i in range(10)])
        self.assertEqual(state['resolved'],10)
        self.assertEqual(state['unresolved'],len(state['effective_blockers']))
        self.assertEqual(state['unresolved'],1)

    def test_source_scoped_identity_and_significant_region_change(self):
        self.assertNotEqual(review_state([issue()],source_id='a')['items'][0]['issue_id'],
                            review_state([issue()],source_id='b')['items'][0]['issue_id'])
        self.assertEqual(review_state([issue(start=4,end=5)],[decision()])['unresolved'],1)

    def test_restart_redetection_and_seven_persisted_resolutions(self):
        with tempfile.TemporaryDirectory() as td:
            media=Path(td)/'video.mp4';media.write_bytes(b'one')
            anchor=Path(td)/'_tmp'
            store.bind_source(anchor,media)
            store.save_non_speech(anchor,[decision(i,'CONFIRMED_EFFECT' if i%2 else 'CONFIRMED_NOISE') for i in range(7)])
            self.assertEqual(len(store.load_non_speech(anchor)),7)
            before=store.effective_review(anchor,[issue(i) for i in range(7)])
            store.bind_source(anchor,media)  # a new session with the same stat identity
            after=store.effective_review(anchor,[issue(i) for i in reversed(range(7))])
            self.assertEqual(before,after)
            self.assertEqual(after['effective_blockers'],[])

    def test_source_change_invalidates_without_deleting_old_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            media=Path(td)/'video.mp4';media.write_bytes(b'one')
            store.bind_source(td,media);store.save_non_speech(td,[decision()])
            media.write_bytes(b'different source')
            self.assertTrue(store.bind_source(td,media)['require_asr'])
            self.assertEqual(store.effective_review(td,[issue()])['unresolved'],1)
            persisted=json.loads((Path(td)/'non_speech.json').read_text())
            self.assertEqual(len(persisted['ranges']),1)

    def test_failed_commit_cannot_report_resolved_state(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(store,'atomic_json',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.save_non_speech(td,[decision()])
            self.assertEqual(store.effective_review(td,[issue()])['unresolved'],1)

    def test_legacy_valid_portions_survive_invalid_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'non_speech.json'
            path.write_text(json.dumps([dict(start=0,end=1,confirmed=True,type='effect'),None,
                                        dict(start='bad',end=4)]))
            state=store.effective_review(td,[issue()])
            self.assertEqual(state['unresolved'],0)
            self.assertEqual(state['items'][0]['resolution'],'CONFIRMED_EFFECT')

    def test_malformed_file_uses_valid_backup_or_refuses_destructive_save(self):
        with tempfile.TemporaryDirectory() as td:
            store.save_non_speech(td,[decision()]);store.save_non_speech(td,[decision()])
            path=Path(td)/'non_speech.json';path.write_text('{partial')
            self.assertEqual(store.effective_review(td,[issue()])['unresolved'],0)
            path.with_suffix('.json.bak').unlink()
            with self.assertRaises(ValueError):
                store.save_non_speech(td,[decision(1)])
            self.assertEqual(path.read_text(),'{partial')

    def test_batch_and_concurrent_commits_do_not_lose_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i:store.save_non_speech(td,[decision(i)]),range(20)))
            store.save_non_speech(td,[decision(i) for i in range(20,200)])
            self.assertEqual(len(store.load_non_speech(td)),200)
            self.assertEqual(store.effective_review(td,[issue(i) for i in range(200)])['unresolved'],0)

    def test_bulk_evaluation_does_not_recompact_decisions_for_each_issue(self):
        decisions=[decision(i) for i in range(200)]
        with patch.object(store,'compact_ranges',wraps=store.compact_ranges) as compact:
            state=review_state([issue(i) for i in range(10000)],decisions)
        self.assertEqual(compact.call_count,1)
        self.assertEqual(state['unresolved'],9800)

    def test_structural_clock_error_is_not_a_valid_noise_resolution(self):
        state=review_state([issue(reason='invalid_source_clock')],[decision()])
        self.assertEqual(state['unresolved'],1)

    def test_empty_snapshot_does_not_resurrect_stale_job_flags(self):
        from autodub.server.review import review_for_job
        from autodub.server import helpers
        with tempfile.TemporaryDirectory() as td, patch.object(helpers,'HERE',td):
            anchor=Path(td)/'output/film/_tmp'
            root=anchor/'caption-review-empty';root.mkdir(parents=True)
            store.save_latest_review(anchor,[])
            state=review_for_job(dict(review_dir=str(root),result_status='REVIEW_REQUIRED',
                                      review_gaps=[issue()]))
            self.assertEqual(state['items'],[])
            self.assertEqual(state['unresolved'],0)

    def test_source_checkpoint_generation_prevents_same_duration_reuse(self):
        from autodub.asr.long_audio import compatible_checkpoint
        old=dict(audio_sha256='old',duration=100,review_source_id='one')
        new=dict(audio_sha256='new',duration=100,review_source_id='two')
        self.assertFalse(compatible_checkpoint(old,new))
        new['review_source_id']='one'
        self.assertTrue(compatible_checkpoint(old,new))

    def test_malformed_diagnostic_is_blocking_not_a_ui_crash(self):
        result=review_state([dict(start='bad',end=None)])
        self.assertEqual(result['unresolved'],1)

    def test_empty_crashed_review_directory_recovers_previous_valid_artifact(self):
        from autodub.server.review import review_for_job
        from autodub.server import helpers
        with tempfile.TemporaryDirectory() as td, patch.object(helpers,'HERE',td):
            anchor=Path(td)/'output/film/_tmp'
            good=anchor/'caption-review-good';good.mkdir(parents=True)
            (good/'source.srt').write_text('source')
            (good/'unresolved.json').write_text(json.dumps([issue()]))
            empty=anchor/'caption-review-crashed';empty.mkdir()
            store.save_non_speech(anchor,[decision()])
            state=review_for_job(dict(review_dir=str(empty),result_status='REVIEW_REQUIRED'))
            self.assertEqual(Path(state['review_dir']),good)
            self.assertEqual(state['unresolved'],0)

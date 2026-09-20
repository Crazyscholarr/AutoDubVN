"""Real local HTTP regression tests; all writable paths are temporary."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from autodub.server import config_api, helpers, http_api, pipeline, projects, state
from autodub.server.job_manager import JobManager
from autodub.server.content import common as content_common


class HttpRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = JobManager(str(self.root / "jobs.json"))
        self.addCleanup(lambda: self.manager.shutdown(timeout=2))
        self.patches = []
        for module, key, value in (
            (state, "JOB_MANAGER", self.manager),
            (http_api, "JOB_MANAGER", self.manager),
            (http_api, "HERE", self.tmp.name),
            (helpers, "HERE", self.tmp.name),
            (content_common, "HERE", self.tmp.name),
            (config_api, "CONFIG_PATH", str(self.root / "config.yaml")),
        ):
            patch = mock.patch.object(module, key, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.saved_state = copy.deepcopy(state.STATE)
        self.saved_projects = dict(state.PROJECTS)
        self.addCleanup(self.restore_state)
        state.STATE.update(queue=[], selected=None, running=False, busy="", nvenc=False)
        state.PROJECTS.clear()
        state._CANCEL_EVENT.clear()
        self.server = http_api.QuietServer(("127.0.0.1", 0), http_api.Handler)
        self.server.handle_error = mock.Mock()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def restore_state(self):
        state.STATE.clear()
        state.STATE.update(self.saved_state)
        state.PROJECTS.clear()
        state.PROJECTS.update(self.saved_projects)
        state._CANCEL_EVENT.clear()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def request(self, path, body=None, *, raw=None, headers=None, method=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
            conn.request(method or ("POST" if payload is not None else "GET"), path,
                         body=payload, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_reject_non_object_and_malformed_json(self):
        for raw in ("[]", "null", '"hello"', "42", "{broken"):
            with self.subTest(raw=raw):
                status, _, body = self.request("/api/queue/select", raw=raw)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(body))

    def test_invalid_query_returns_json_and_server_stays_usable(self):
        for path in ("/api/project?id=oops", "/api/video?id=nan", "/api/preview?id=x"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(body))
        self.assertEqual(self.request("/api/state")[0], 200)

    def test_feature_routes_validate_empty_input_without_internal_errors(self):
        for path in (
            "/api/run", "/api/queue/add", "/api/manual/run_all", "/api/manual/tts",
            "/api/manual/use_audio", "/api/manual/slideshow", "/api/manual/mux",
            "/api/content/analyze", "/api/content/search", "/api/content/use_story",
            "/api/content/import", "/api/content/reload", "/api/content/update",
            "/api/tools/search_videos", "/api/tools/cut_videos", "/api/tools/download_videos",
            "/api/story/search_sources", "/api/story/search_references",
            "/api/story/download_sources", "/api/story/cut_sources", "/api/story/generate_and_run",
        ):
            with self.subTest(path=path):
                status, _, body = self.request(path, {})
                self.assertIn(status, (400, 404, 409))
                self.assertIn("error", json.loads(body))

    def test_media_ranges_and_head(self):
        path = self.root / "sample.mp4"
        path.write_bytes(b"0123456789")
        endpoint = "/api/local_video?" + urlencode({"path": str(path)})
        for value, expected, code in (
            ("bytes=2-5", b"2345", 206),
            ("bytes=-3", b"789", 206),
            ("bytes=7-", b"789", 206),
            ("bytes=20-", None, 416),
            ("bytes=7-2", None, 416),
        ):
            with self.subTest(value=value):
                status, headers, body = self.request(endpoint, headers={"Range": value})
                self.assertEqual(status, code)
                if expected is not None:
                    self.assertEqual(body, expected)
                else:
                    self.assertEqual(headers.get("Content-Range"), "bytes */10")
        status, headers, body = self.request(endpoint, method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], "10")
        self.assertEqual(body, b"")

    def test_percent_in_local_filename_decoded_once(self):
        path = self.root / "clip%20literal.mp4"
        path.write_bytes(b"media")
        endpoint = "/api/local_video?" + urlencode({"path": str(path)})
        self.assertEqual(self.request(endpoint)[2], b"media")

    def test_failed_queue_add_does_not_leave_ghost_item(self):
        path = self.root / "bad.mp4"
        path.write_bytes(b"not a video")
        with mock.patch.object(http_api, "get_project", side_effect=RuntimeError("bad media")):
            self.assertGreaterEqual(self.request("/api/queue/add", {"path": str(path)})[0], 400)
        self.assertEqual(state.STATE["queue"], [])
        self.assertIsNone(state.STATE["selected"])

    def test_unknown_explicit_cancel_never_kills_other_processes(self):
        with mock.patch.object(http_api, "cancel_running_processes") as kill:
            self.request("/api/cancel", {"job_id": "no-such-job"})
        kill.assert_not_called()
        self.assertFalse(state._CANCEL_EVENT.is_set())

    def test_unknown_steps_are_rejected_before_submitting_work(self):
        path = self.root / "clip.mp4"
        path.write_bytes(b"media")
        state.STATE["queue"] = [{"id": 1, "path": str(path)}]
        with mock.patch.object(http_api, "submit_job") as submit:
            status, _, _ = self.request("/api/run", {"id": 1, "steps": ["typo"]})
        self.assertEqual(status, 400)
        submit.assert_not_called()
        self.assertFalse(state.STATE["running"])

    def test_sync_check_step_is_accepted(self):
        path = self.root / "clip.mp4"
        path.write_bytes(b"media")
        state.STATE["queue"] = [{"id": 1, "path": str(path)}]
        with mock.patch.object(http_api, "submit_job", return_value="bg-1") as submit:
            status, _, _ = self.request("/api/run", {"id": 1, "steps": ["sync_check"]})
        self.assertEqual(status, 200)
        submit.assert_called()
        state.STATE["running"] = False

    def test_project_subtitle_edits_survive_reload(self):
        path = self.root / "project.mp4"
        path.write_bytes(b"media")
        original = {"video": str(path), "segments": [
            {"start": 0, "end": 1, "src": "hello", "vi": "Bản sửa của tôi"}],
            "options": {}, "regions": [], "logo": None, "sub_style": {}}
        state.STATE["queue"] = [{"id": 1, "path": str(path)}]
        state.PROJECTS[1] = original
        self.assertEqual(self.request("/api/project", {"id": 1, **original})[0], 200)
        restored = {"video": str(path), "segments": []}
        projects._load_project_state(restored)
        self.assertEqual(restored["segments"], original["segments"])

    def test_cancel_queued_pipeline_releases_busy_state(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.manager.submit(lambda: (started.set(), release.wait(2)),
                            name="occupied slot", resource="ffmpeg", foreground=False)
        self.assertTrue(started.wait(1))
        state.STATE["running"] = True
        state.STATE["queue"] = [{"id": 7, "status": "chờ"}]
        target = mock.Mock()
        job_id = state.submit_job(target, name="queued dub", resource="ffmpeg",
                                  metadata={"kind": "dub_pipeline", "queue_id": 7})
        self.assertTrue(self.manager.cancel(job_id))
        self.assertFalse(state.STATE["running"])
        self.assertEqual(state.STATE["queue"][0]["status"], "đã huỷ")
        target.assert_not_called()

    def test_rejected_submission_releases_reserved_state(self):
        self.manager.shutdown(timeout=1)
        state.STATE.update(running=True, busy="Tạo giọng")
        state.STATE["manual"]["working"] = True
        with self.assertRaises(RuntimeError):
            state.submit_job(mock.Mock(), name="tts", metadata={"kind": "manual_tts"})
        self.assertFalse(state.STATE["running"])
        self.assertFalse(state.STATE["manual"]["working"])

    def test_pipeline_initialization_failure_releases_running_flag(self):
        state.STATE.update(running=True, queue=[{"id": 9, "status": "chờ"}])
        with mock.patch.object(pipeline, "get_project", return_value={"video": "fixture"}), \
                mock.patch.object(pipeline, "_load_cfg", side_effect=ValueError("bad config")):
            with self.assertRaisesRegex(ValueError, "bad config"):
                pipeline.run_pipeline(9, ["asr"])
        self.assertFalse(state.STATE["running"])
        self.assertEqual(state.STATE["queue"][0]["status"], "lỗi")

    def test_review_status_does_not_leak_into_successful_rerun(self):
        job = {"id": 9, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
               "review_dir": "previous", "working_source": "previous.srt",
               "review_gaps": [{"start": 1, "end": 2, "reason": "unresolved_speech_gap"}]}
        state.STATE.update(running=True, queue=[job])
        with mock.patch.object(pipeline, "_run_pipeline", side_effect=lambda *_: job.update(status="xong")):
            self.assertIsNone(pipeline.run_pipeline(9, ["asr"]))
        self.assertNotIn("result_status", job)
        self.assertNotIn("review_dir", job)
        self.assertNotIn("review_gaps", job)

    def test_review_required_blocks_translate_tts_and_render(self):
        job = {"id": 9, "status": "cần kiểm tra",
               "result_status": "REVIEW_REQUIRED",
               "review_dir": "caption-review-current",
               "working_source": "source.needs-review.srt",
               "review_gaps": [{"start": 1, "end": 2,
                                "reason": "unresolved_speech_gap"}]}
        state.STATE.update(running=True, queue=[job])
        with mock.patch.object(pipeline, "_run_pipeline") as run:
            result = pipeline.run_pipeline(9, ["translate", "tts", "render"])
        run.assert_not_called()
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(job["result_status"], "REVIEW_REQUIRED")
        self.assertEqual(job["review_dir"], "caption-review-current")
        self.assertEqual(len(job["review_gaps"]), 1)
        self.assertFalse(state.STATE["running"])

    def test_pipeline_returns_review_status(self):
        job = {"id": 9, "status": "chờ"}
        state.STATE.update(running=True, queue=[job])
        def review(*_):
            job.update(status="cần kiểm tra", result_status="REVIEW_REQUIRED", review_dir="new")
        with mock.patch.object(pipeline, "_run_pipeline", side_effect=review):
            self.assertEqual(pipeline.run_pipeline(9, ["asr"])["status"], "REVIEW_REQUIRED")
        self.assertFalse(state.STATE["running"])

    def test_pipeline_returns_sync_gate_instead_of_background_success(self):
        job = {"id": 9, "status": "chờ"}
        state.STATE.update(running=True, queue=[job])

        def fail_sync(*_):
            job.update(
                status="cần kiểm tra",
                result_status="SYNC_CHECK_FAILED",
                last_sync_check={"verdict": "fail", "block_render": True},
            )

        with mock.patch.object(pipeline, "_run_pipeline", side_effect=fail_sync):
            result = pipeline.run_pipeline(9, ["render"])
        self.assertEqual(result["status"], "SYNC_CHECK_FAILED")
        self.assertEqual(result["report"]["verdict"], "fail")
        self.assertFalse(state.STATE["running"])

    def test_caption_review_reads_unresolved_from_output(self):
        review_dir = self.root / "output" / "film" / "_tmp" / "caption-review-abc"
        review_dir.mkdir(parents=True)
        (review_dir / "unresolved.json").write_text(
            json.dumps([
                {"start": 23.7, "end": 25.8, "reason": "unresolved_speech_gap", "withheld": True},
                {"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk", "withheld": True,
                 "diagnostics": {"chunk": "chunk_0008aaa"}},
            ]),
            encoding="utf-8",
        )
        (review_dir / "source.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n活下去\n", encoding="utf-8")
        state.STATE["queue"] = [{
            "id": 4, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
            "review_dir": str(review_dir),
        }]
        status, _, body = self.request("/api/caption_review?id=4")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual([g["reason"] for g in payload["gaps"]],
                         ["unresolved_speech_gap", "suspicious_chunk"])
        self.assertEqual(payload["gaps"][0]["start"], 23.7)
        self.assertTrue(payload["source_srt"].endswith("source.srt"))
        self.assertNotIn("diagnostics", payload["gaps"][1])

    def test_caption_review_rejects_path_outside_output(self):
        outside = self.root / "elsewhere" / "caption-review-abc"
        outside.mkdir(parents=True)
        (outside / "unresolved.json").write_text(
            json.dumps([{"start": 1, "end": 2, "reason": "unresolved_speech_gap"}]),
            encoding="utf-8",
        )
        state.STATE["queue"] = [{
            "id": 4, "status": "cần kiểm tra",
            "review_dir": str(outside),
            "review_gaps": [{"start": 8.0, "end": 9.5, "reason": "suspicious_chunk"}],
        }]
        status, _, body = self.request("/api/caption_review?id=4")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["review_dir"], "")
        self.assertEqual(payload["gaps"][0]["reason"], "suspicious_chunk")
        self.assertEqual(payload["source_srt"], "")

    def test_caption_review_ack_non_speech_then_continue(self):
        review_dir = self.root / "output" / "film" / "_tmp" / "caption-review-ack"
        review_dir.mkdir(parents=True)
        gaps = [
            {"start": 23.7, "end": 25.8, "reason": "unresolved_speech_gap", "withheld": True},
            {"start": 599.66, "end": 601.6, "reason": "unresolved_speech_gap", "withheld": True},
            {"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk", "withheld": True},
        ]
        (review_dir / "unresolved.json").write_text(json.dumps(gaps), encoding="utf-8")
        srt = "1\n00:00:00,000 --> 00:00:01,000\n活下去\n"
        (review_dir / "source.srt").write_text(srt, encoding="utf-8")
        (review_dir / "packed.needs-review.srt").write_text(srt, encoding="utf-8")
        video = self.root / "film.mp4"
        video.write_bytes(b"fake-video")
        state.STATE["queue"] = [{
            "id": 4, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
            "review_dir": str(review_dir), "path": str(video), "name": "film",
        }]
        state.PROJECTS[4] = {
            "video": str(video), "w": 1280, "h": 720, "duration": 100.0,
            "picture_duration": 100.0, "clocks": {}, "regions": [], "logo": None,
            "sub_style": {}, "segments": [], "options": {"trim_enabled": False},
        }
        with mock.patch.object(pipeline, "HERE", self.tmp.name):
            status, _, body = self.request("/api/caption_review/ack", {
                "id": 4, "ranges": [gaps[0]], "continue_pipeline": True,
            })
        self.assertEqual(status, 409)
        self.assertIn("Còn đoạn", json.loads(body)["error"])

        with mock.patch.object(pipeline, "HERE", self.tmp.name), \
                mock.patch.object(http_api, "submit_job", return_value="bg-ack") as submit:
            status, _, body = self.request("/api/caption_review/ack", {
                "id": 4,
                "ranges": [{"start": g["start"], "end": g["end"], "reason": g["reason"]}
                           for g in gaps],
                "continue_pipeline": True,
            })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["remaining"], [])
        self.assertTrue(payload["started"])
        self.assertEqual(payload["steps"], ["translate", "tts", "render"])
        submit.assert_called_once()
        self.assertEqual(submit.call_args.kwargs["args"], (4, ["translate", "tts", "render"]))
        copied = (self.root / "output" / "film" / "film.src.srt").read_text(encoding="utf-8")
        self.assertIn("活下去", copied)
        self.assertNotIn("蟋蟀", copied)

    def test_caption_review_rerecognize_starts_background_job(self):
        review_dir = self.root / "output" / "film" / "_tmp" / "caption-review-re"
        review_dir.mkdir(parents=True)
        (review_dir / "unresolved.json").write_text(json.dumps([
            {"start": 4193.08, "end": 4268.44, "reason": "suspicious_chunk", "withheld": True},
        ]), encoding="utf-8")
        video = self.root / "film.mp4"
        video.write_bytes(b"fake-video")
        state.STATE["queue"] = [{
            "id": 4, "status": "cần kiểm tra", "result_status": "REVIEW_REQUIRED",
            "review_dir": str(review_dir), "path": str(video), "name": "film",
        }]
        with mock.patch.object(http_api, "submit_job", return_value="bg-re") as submit:
            status, _, body = self.request("/api/caption_review/rerecognize", {
                "id": 4, "start": 4193.08, "end": 4268.44,
            })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["started"])
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[0], pipeline.rerecognize_caption_gap)
        self.assertEqual(submit.call_args.kwargs["args"], (4, 4193.08, 4268.44))
        state.STATE["running"] = False

    def test_saved_missing_marks_ack_can_resume_without_acking_again(self):
        from autodub.asr.nonspeech import save_non_speech
        review = self.root / 'output/film/_tmp/caption-review-marks'
        review.mkdir(parents=True)
        gap = dict(start=14054.35,end=14054.9,reason='missing_speech_marks',withheld=True)
        (review/'unresolved.json').write_text(json.dumps([gap]),encoding='utf-8')
        source = '1\n00:00:00,000 --> 00:00:01,000\n活下去\n'
        (review/'packed.needs-review.srt').write_text(source,encoding='utf-8')
        (review/'source.srt').write_text(source,encoding='utf-8')
        # Old versions persisted the user's confirmation under this reason.
        save_non_speech(review,[dict(start=gap['start'],end=gap['end'],reason='unresolved_speech_gap')])
        video=self.root/'film.mp4';video.write_bytes(b'fake')
        state.STATE['queue']=[dict(id=4,path=str(video),name='film',status='cần kiểm tra',
                                  result_status='REVIEW_REQUIRED',review_dir=str(review))]
        state.PROJECTS[4]=dict(video=str(video),w=1280,h=720,duration=14425.,
                              picture_duration=14425.,clocks={},regions=[],logo=None,
                              sub_style={},segments=[],options={'trim_enabled':False})
        status,_,body=self.request('/api/caption_review?id=4')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['remaining'],[])
        with mock.patch.object(pipeline,'HERE',self.tmp.name), \
             mock.patch.object(http_api,'submit_job',return_value='resume') as submit:
            status,_,body=self.request('/api/caption_review/ack',
                                      dict(id=4,ranges=[],continue_pipeline=True))
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['started'])
        self.assertEqual(submit.call_args.kwargs['args'],(4,['translate','tts','render']))
        self.assertNotIn('result_status',state.STATE['queue'][0])

    def test_seven_empty_regions_restart_resume_without_asr(self):
        from autodub.asr.nonspeech import bind_source, save_latest_review
        review = self.root/'output/film/_tmp/caption-review-seven'
        review.mkdir(parents=True)
        video=self.root/'film.mp4'; video.write_bytes(b'fake-video')
        rows=[dict(start=10*i+2,end=10*i+3,reason='missing_speech_marks',
                   text='',timestamps=[],withheld=True) for i in range(7)]
        (review/'review.json').write_text(json.dumps(rows),encoding='utf-8')
        (review/'source.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n活下去\n',encoding='utf-8')
        template=dict(video=str(video),w=1280,h=720,duration=100.,picture_duration=100.,
                      clocks={},regions=[],logo=None,sub_style={},segments=[],options={'trim_enabled':False})
        state.PROJECTS[4]=copy.deepcopy(template)
        state.STATE['queue']=[dict(id=4,path=str(video),status='cần kiểm tra',
                                  result_status='REVIEW_REQUIRED',review_dir=str(review))]
        bind_source(review,video)
        save_latest_review(review,rows,str(review))
        with mock.patch('autodub.asr.nonspeech.save_non_speech',side_effect=OSError('disk full')):
            status,_,body=self.request('/api/caption_review/ack',dict(id=4,ranges=rows))
        self.assertEqual(status,500)
        self.assertFalse((review.parent/'non_speech.json').exists())
        status,_,body=self.request('/api/caption_review?id=4')
        self.assertEqual(json.loads(body)['unresolved'],7)
        decisions=[dict(r,resolution='CONFIRMED_EFFECT' if i%2 else 'CONFIRMED_NOISE')
                   for i,r in enumerate(rows)]
        status,_,body=self.request('/api/caption_review/ack',dict(id=4,ranges=decisions))
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['unresolved'],0)
        # Recreate both queue item and project; no in-memory review flags survive.
        state.PROJECTS.clear()
        state.STATE['queue']=[dict(id=4,path=str(video),status='chờ')]
        with mock.patch.object(projects,'default_project',side_effect=lambda _:copy.deepcopy(template)):
            status,_,body=self.request('/api/caption_review?id=4')
        restored=json.loads(body)
        self.assertEqual(restored['total_issues'],7)
        self.assertEqual(restored['unresolved'],0)
        self.assertTrue(restored['can_continue_translation'])
        self.assertEqual(len({r['issue_id'] for r in restored['items']}),7)
        translated=mock.Mock()
        asr=mock.Mock()
        def execute(job_id,steps):
            if 'asr' in steps: asr()
            if 'translate' in steps:
                translated()
                state.PROJECTS[job_id]['segments'][0]['vi']='Hãy sống tiếp'
        with mock.patch.object(pipeline,'HERE',self.tmp.name), \
             mock.patch.object(pipeline,'_run_pipeline',side_effect=execute):
            pipeline.run_pipeline(4,['translate'])
            self.assertEqual(state.PROJECTS[4]['segments'][0]['vi'],'Hãy sống tiếp')
            # Repeated preparation/TTS preserves translated rows and saved source.
            result=pipeline.ack_caption_review(4,decisions,continue_pipeline=True)
            self.assertTrue(result['continue_pipeline'])
            pipeline.run_pipeline(4,['tts'])
        asr.assert_not_called()
        translated.assert_called_once()
        self.assertEqual(state.PROJECTS[4]['segments'][0]['vi'],'Hãy sống tiếp')

    def test_caption_retry_rejects_nonfinite_and_negative_clock(self):
        with mock.patch.object(http_api, "submit_job") as submit:
            for start, end in [("nan", 12), (10, "inf"), (-1, 12), (12, 10)]:
                with self.subTest(start=start, end=end):
                    status, _, _ = self.request("/api/caption_review/rerecognize", {
                        "id": 4, "start": start, "end": end,
                    })
                    self.assertEqual(status, 400)
            submit.assert_not_called()
        self.assertFalse(state.STATE["running"])

    def test_caption_ack_cannot_mutate_review_during_recognition(self):
        state.STATE["running"] = True
        with mock.patch.object(http_api, "ack_caption_review") as acknowledge:
            status, _, _ = self.request("/api/caption_review/ack", {
                "id": 4, "ranges": [{"start": 10, "end": 12}],
            })
            self.assertEqual(status, 409)
            acknowledge.assert_not_called()

    def test_caption_retry_respects_other_busy_operation(self):
        state.STATE["busy"] = "Đang dò phụ đề"
        with mock.patch.object(http_api, "submit_job") as submit:
            status, _, _ = self.request("/api/caption_review/rerecognize", {
                "id": 4, "start": 10, "end": 12,
            })
            self.assertEqual(status, 409)
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

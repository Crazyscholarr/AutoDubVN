"""Real Edge pointer regressions on the real HTTP/UI stack, with isolated media/state.

Run: venv\\Scripts\\python -m unittest discover -s tests -p test_dub_editor_ui.py -v
AUTODUB_EDITOR_QA=1 retains screenshots and event evidence under output/editor-qa.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image
from playwright.sync_api import sync_playwright

import test_http_runtime as fixture
from autodub import overlays
from autodub.server import projects


class DubEditorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory(prefix="autodub-editor-media-")
        cls.media_root = Path(cls.media.name)
        for name, size in (("wide", "960x540"), ("vertical", "540x960")):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"testsrc2=size={size}:rate=24", "-t", "12", "-c:v",
                            "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                            str(cls.media_root / f"{name}.mp4")], check=True, timeout=30)
        Image.new("RGBA", (160, 90), (255, 180, 20, 230)).save(cls.media_root / "logo.png")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.media.cleanup()

    def setUp(self):
        self.http = fixture.HttpRuntimeTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        (self.http.root / "config.yaml").write_text(json.dumps({
            "output": {"dir": str(self.http.root / "exports")},
            "content_pipeline": {"database": str(self.http.root / "ideas.sqlite")}
        }), encoding="utf-8")
        self.context = self.browser.new_context(viewport={"width": 1600, "height": 1000})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.writes = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.on("request", lambda req: self.writes.append(req.post_data_json)
                     if req.method == "POST" and req.url.endswith("/api/project") else None)
        self.page.add_init_script("""
            window.qaMedia = {play:0, pause:0, events:[]};
            for (const name of ['play','pause']) {
                const original = HTMLMediaElement.prototype[name];
                HTMLMediaElement.prototype[name] = function(...args) {
                    if(this.id === 'video') qaMedia[name]++;
                    return original.apply(this,args);
                };
            }
            for(const name of ['pointerdown','pointermove','pointerup'])
                document.addEventListener(name, e => {
                    if(name !== 'pointermove' || e.buttons)
                        qaMedia.events.push({type:name, target:e.target.id || e.target.className,
                            x:e.clientX,y:e.clientY, trusted:e.isTrusted});
                }, true);
        """)
        self.base = "http://%s:%s" % self.http.server.server_address
        self.page.goto(self.base + "/?editorDebug=1")
        self.page.wait_for_function("typeof selectJob === 'function'")
        self.page.wait_for_function("document.querySelector('#titlebar').style.display === 'none'")

    def load_video(self, name="wide"):
        # Exercise the existing browser file-picker prompt, not a fake video object.
        self.page.once("dialog", lambda dialog: dialog.accept(str(self.media_root / f"{name}.mp4")))
        self.page.locator('[onclick="pickFile()"]',).first.click()
        self.page.wait_for_function("PR && V().readyState >= 2 && V().videoWidth > 0")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)
        self.assertTrue(self.page.evaluate("V().paused"))

    def state(self, kind):
        return self.page.evaluate({"blur":"PR.regions[0]", "logo":"PR.logo",
                                   "sub":"PR.sub_style.box"}[kind])

    def drag(self, kind, dx=27, dy=-20, handle=None):
        selector = f"#layers .box.{kind}" + (f" .h.{handle}" if handle else "")
        el = self.page.locator(selector).first
        el.wait_for(state="visible")
        bounds = el.bounding_box()
        self.assertIsNotNone(bounds, f"No visible target for {selector}")
        x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
        hit = self.page.evaluate("""([x,y]) => {
            const el=document.elementFromPoint(x,y);
            return {target:el.id || el.className, box:el.closest('.box')?.className};
        }""", [x,y])
        before = self.state(kind)
        timestamp = self.page.evaluate("V().currentTime")
        paused = self.page.evaluate("V().paused")
        self.assertIn(kind, hit.get("box") or "", f"Actual hit target: {hit}")
        self.page.keyboard.down("Alt")
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x+dx, y+dy, steps=8)
        self.page.wait_for_timeout(50)
        during = self.state(kind)
        self.assertNotEqual(before, during, "State must change before pointerup")
        self.assertEqual(self.page.evaluate("ACT.kind"), {"blur":"rgn"}.get(kind,kind))
        self.assertTrue(self.page.evaluate("_isDragging"))
        self.assertTrue(self.page.evaluate("""() => {
            const el=document.querySelector('#layers .dragging');
            return el && el.hasPointerCapture(1);
        }"""), "Mouse pointer capture must survive preview updates")
        self.page.mouse.up()
        self.page.keyboard.up("Alt")
        self.page.wait_for_timeout(80)
        after = self.state(kind)
        self.assertEqual(during, after, "pointerup must retain the edited state")
        self.assertFalse(self.page.evaluate("_isDragging"))
        if paused:
            self.assertEqual(timestamp, self.page.evaluate("V().currentTime"))
            self.assertTrue(self.page.evaluate("V().paused"))
        self.assertEqual(self.errors, [])
        self.assert_dom_geometry(kind)
        return after

    def assert_dom_geometry(self, kind):
        error = self.page.evaluate("""kind => {
            const r=kind==='blur'?PR.regions[0]:kind==='logo'?PR.logo:PR.sub_style.box;
            const v=V().getBoundingClientRect();
            const b=document.querySelector('.box.'+kind).getBoundingClientRect();
            return Math.max(Math.abs(b.x-v.x-r.x*v.width/PR.w),
                Math.abs(b.y-v.y-r.y*v.height/PR.h),
                Math.abs(b.width-r.w*v.width/PR.w),Math.abs(b.height-r.h*v.height/PR.h));
        }""", kind)
        self.assertLess(error, 1.2, "DOM must reflect the current source coordinates")

    def add_logo(self):
        self.page.locator('#tools [onclick="addLogoBox()"]').click()
        self.page.locator("#logopath").fill(str(self.media_root / "logo.png"))
        self.page.locator("#logopath").press("Tab")
        self.page.wait_for_function("document.querySelector('.logo-image')?.naturalWidth === 160")

    def assert_saved_for_export(self):
        self.page.evaluate("saveNow()")
        jid = self.page.evaluate("JID")
        saved = self.page.request.get(f"{self.base}/api/project?id={jid}").json()
        local = self.page.evaluate("PR")
        for key in ("regions", "logo", "sub_style", "options"):
            self.assertEqual(saved[key], local[key])
        disk = json.loads(Path(projects._project_state_path(saved["video"])).read_text(encoding="utf-8"))
        for key in ("regions", "logo", "sub_style", "options"):
            self.assertEqual(disk[key], local[key])
        if saved["logo"]:
            logo = saved["logo"]
            filters, _ = overlays.build_overlay_filters(saved["regions"], saved["w"], saved["h"],
                                                       logo_input_index=1, logo=logo)
            self.assertIn(f"overlay=x='{logo['x']}':y='{logo['y']}'", ";".join(filters))
        return saved

    def exercise_features(self, mode):
        self.load_video()
        if mode != "never-played":
            self.page.locator("#play").click()
            self.page.wait_for_function("V().currentTime >= 5")
            if mode == "paused":
                self.page.locator("#play").click()
                self.page.wait_for_function("V().paused")
        media_before = self.page.evaluate("({play:qaMedia.play,pause:qaMedia.pause})")
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        self.drag("blur")
        self.drag("blur", -20, -8, "se")
        self.add_logo()
        self.drag("logo", 36, 24)
        self.drag("logo", 22, 14, "se")
        self.page.locator('#panel input[type="range"]').fill("45")
        self.page.wait_for_function("document.querySelector('.logo-image').style.opacity === '0.45'")
        self.drag("sub", 0, -40)
        self.drag("sub", 15, 28)
        self.drag("sub", -10, -8, "se")
        size = self.page.locator('[onchange="setSt(\'size\',+this.value)"]')
        size.fill("42")
        size.press("Tab")
        self.page.wait_for_function("PR.sub_style.size === 42")
        # Rendering is requestAnimationFrame-coalesced. Waiting merely for a
        # positive font size races with the already-visible old 30px preview.
        self.page.wait_for_function(
            "Math.abs(parseFloat(getComputedStyle("
            "document.querySelector('#subtext')).fontSize)-42*scale())<0.1"
        )
        self.assertAlmostEqual(self.page.evaluate("parseFloat(getComputedStyle(document.querySelector('#subtext')).fontSize)"),
                               self.page.evaluate("42*scale()"), delta=.1)
        self.page.locator("#prev").uncheck()
        self.page.wait_for_function("!document.querySelector('.logo-image') && !document.querySelector('#subtext')")
        self.assertEqual(self.page.locator(".box.blur").evaluate("el=>getComputedStyle(el).backdropFilter"), "none")
        self.page.locator("#prev").check()
        self.page.wait_for_function("document.querySelector('.logo-image') && document.querySelector('#subtext')")
        self.assertIn("blur(", self.page.locator(".box.blur").evaluate("el=>getComputedStyle(el).backdropFilter"))
        self.page.locator('[data-t="cut"]').click()
        trim = self.page.locator("#trkCut .trimH.b").bounding_box()
        before_trim_time=self.page.evaluate("V().currentTime")
        self.page.mouse.move(trim["x"]+1,trim["y"]+trim["height"]/2)
        self.page.mouse.down()
        self.page.mouse.move(trim["x"]-25,trim["y"]+trim["height"]/2,steps=5)
        self.page.mouse.up()
        self.assertTrue(self.page.evaluate("PR.options.trim_enabled"))
        self.assertLess(self.page.evaluate("PR.options.trim_end"), 12)
        if mode != "playing":
            self.assertEqual(before_trim_time,self.page.evaluate("V().currentTime"))
        self.assert_saved_for_export()
        if mode != "playing":
            self.page.locator("#scrub").click(position={"x":210,"y":3})
            self.page.wait_for_function("!V().seeking && V().currentTime > 1 && document.querySelector('#layers .box.sub')")
            self.page.wait_for_timeout(80)
            self.drag("sub", 5, -18)
            self.drag("blur", 10, -10)
        self.assertEqual(self.page.evaluate("({play:qaMedia.play,pause:qaMedia.pause})"),media_before)
        self.evidence(mode)
        self.page.locator('[data-t="logo"]').click()
        self.page.get_by_role("button",name="Bỏ logo",exact=True).click()
        self.page.wait_for_function("!PR.logo && !document.querySelector('.box.logo')")
        self.page.locator(".box.blur .rm").click()
        self.page.wait_for_function("!PR.regions.length && !document.querySelector('.box.blur')")
        self.assertEqual(self.page.evaluate("({play:qaMedia.play,pause:qaMedia.pause})"),media_before)
        self.assertEqual(self.errors, [])

    def test_never_played_complete_editor_and_export(self):
        self.exercise_features("never-played")

    def test_paused_after_five_seconds_complete_editor_and_export(self):
        self.exercise_features("paused")

    def test_playing_complete_editor_and_export(self):
        self.exercise_features("playing")

    def test_all_eight_blur_resize_handles(self):
        self.load_video()
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        for handle in ("nw","ne","sw","se","n","s","w","e"):
            with self.subTest(handle=handle):
                self.drag("blur", -7 if "w" in handle else 7 if "e" in handle else 0,
                          -6 if "n" in handle else 6 if "s" in handle else 0, handle)
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)

    def test_layout_changes_and_vertical_video_while_stopped(self):
        self.load_video("vertical")
        self.add_logo()
        for width, zoom, sidebar, css_zoom in ((1400,100,True,1),(1720,70,False,1),
                                             (1500,85,True,1.15),(1600,100,True,1)):
            with self.subTest(width=width,zoom=zoom,sidebar=sidebar,css_zoom=css_zoom):
                self.page.set_viewport_size({"width":width,"height":1040})
                self.page.evaluate("""([show,z])=>{
                    document.querySelector('#left').style.display=show?'':'none';
                    document.body.style.zoom=z;
                }""", [sidebar,css_zoom])
                self.page.locator("#zoom").fill(str(zoom))
                self.page.wait_for_timeout(200)
                before=self.state("logo")
                k=self.page.evaluate("V().getBoundingClientRect().width/PR.w")
                after=self.drag("logo",12,10)
                self.assertAlmostEqual(after["x"]-before["x"],12/k,delta=1)
                self.assertAlmostEqual(after["y"]-before["y"],10/k,delta=1)
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)

    def test_overlap_hit_priority_and_center_play_button(self):
        self.load_video()
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        self.add_logo()
        # Arrange a fixture covering the center Play button and all three layers.
        self.page.evaluate("""() => {
            Object.assign(PR.regions[0],{x:0,y:0,w:PR.w,h:PR.h});
            Object.assign(PR.logo,{x:400,y:240,w:160,h:60});draw();
        }""")
        self.page.wait_for_timeout(60)
        self.drag("logo",18,-4)
        self.drag("sub",20,0, "w")
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)
        # The large blur cannot intercept a logo/subtitle; exposed blur remains editable.
        self.page.locator(".box.blur").click(position={"x":70,"y":75})
        self.assertEqual(self.page.evaluate("ACT.kind"),"rgn")
        self.assertEqual(self.page.evaluate("qaMedia.play"),0)
        self.page.evaluate("""() => {
            PR.regions=[]; PR.logo=null;
            Object.assign(PR.sub_style.box,{x:0,y:0,w:48,h:32});
            draw();
        }""")
        self.page.wait_for_timeout(60)
        plays=self.page.evaluate("qaMedia.play")
        self.page.locator("#play").click()
        self.page.wait_for_function("!V().paused")
        self.assertEqual(self.page.evaluate("qaMedia.play"),plays+1)
        self.page.locator("#play").click()
        self.page.wait_for_function("V().paused")
        empty=self.page.evaluate("""() => {
            const v=V().getBoundingClientRect();
            return {x:v.x+v.width/2, y:v.y+v.height/2};
        }""")
        self.page.mouse.click(empty["x"], empty["y"])
        self.page.wait_for_function("!V().paused")
        self.assertEqual(self.page.evaluate("qaMedia.play"),plays+2)
        self.page.locator("#play").click()
        self.page.wait_for_function("V().paused")
        self.page.evaluate("document.body.focus()")
        self.page.keyboard.press("Space")
        self.page.wait_for_function("!V().paused")
        self.assertEqual(self.page.evaluate("qaMedia.play"),plays+3)

    def test_refresh_and_redraw_cannot_dispose_captured_drag(self):
        self.load_video()
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        self.page.evaluate("saveNow()")
        b=self.page.locator('.box.blur').bounding_box()
        x,y=b['x']+b['width']/2,b['y']+b['height']/2
        self.page.mouse.move(x,y)
        self.page.mouse.down()
        self.page.mouse.move(x+30,y-20,steps=5)
        self.page.evaluate("draw();_lastRev=-999;refresh()")
        self.page.wait_for_timeout(600)
        self.assertTrue(self.page.evaluate("document.querySelector('.box.blur').hasPointerCapture(1)"))
        self.page.mouse.move(x+60,y-40,steps=5)
        self.page.mouse.up()
        edited=self.state("blur")
        self.page.wait_for_timeout(1500)
        self.assertEqual(self.state("blur"),edited)
        self.assertEqual(self.page.evaluate("qaMedia.play"),0)
        self.assert_saved_for_export()

    def evidence(self, label):
        if not os.environ.get("AUTODUB_EDITOR_QA"):
            return
        out = Path(__file__).resolve().parents[1] / "output" / "editor-qa"
        out.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(out / f"{label}.png"))
        (out / f"{label}.json").write_text(json.dumps(self.page.evaluate("""({
            media:qaMedia, time:V().currentTime, paused:V().paused, selection:ACT,
            regions:PR.regions,logo:PR.logo,subtitle:PR.sub_style,
            debug:window.editorDebugEvents || []
        })"""), ensure_ascii=False, indent=2), encoding="utf-8")

    def preview_hit(self, x=None, y=None):
        return self.page.evaluate("""([x,y]) => {
            const v=V().getBoundingClientRect();
            if(x==null) x=v.x+v.width/2;
            if(y==null) y=v.y+v.height/2;
            const el=document.elementFromPoint(x,y);
            const hit=typeof hitTestEditor==='function'?hitTestEditor(x,y):null;
            return {
                x,y,
                id:el&&el.id, cls:String(el&&el.className||""),
                box:el&&el.closest&&el.closest('.box')&&el.closest('.box').className,
                overlay:!!(el&&(el.id==='playOverlay'||el.closest&&el.closest('#playOverlay'))),
                hitKind:hit&&hit.kind||null,
                hitHandle:hit&&hit.handle||null
            };
        }""", [x, y])

    def test_never_played_pointer_reaches_blur(self):
        self.load_video()
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        try:
            self.drag("blur")
        finally:
            self.evidence("never-played")

    def test_never_played_center_play_icon_does_not_steal_subtitle(self):
        self.load_video()
        hit=self.preview_hit()
        self.assertFalse(hit["overlay"], hit)
        self.assertIn("sub", hit.get("box") or "", hit)
        self.assertEqual(hit["hitKind"], "sub", hit)
        self.page.mouse.click(hit["x"], hit["y"])
        self.assertTrue(self.page.evaluate("V().paused"))
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)
        self.assertEqual(self.page.evaluate("ACT.kind"), "sub")
        before=self.state("sub")
        self.drag("sub", 0, 24)
        after=self.state("sub")
        self.assertNotEqual(before, after)
        self.assertEqual(self.page.evaluate("qaMedia.play"), 0)
        self.assertTrue(self.page.evaluate("V().paused"))

    def test_paused_click_and_drag_keep_timestamp(self):
        self.load_video()
        self.page.locator("#play").click()
        self.page.wait_for_function("V().currentTime >= 5")
        self.page.locator("#play").click()
        self.page.wait_for_function("V().paused")
        t=self.page.evaluate("V().currentTime")
        plays=self.page.evaluate("qaMedia.play")
        self.page.locator('#tools [onclick="addRegion(\'blur\')"]').click()
        self.page.locator(".box.blur").click()
        self.assertEqual(self.page.evaluate("ACT.kind"), "rgn")
        self.drag("blur", 18, -12)
        self.drag("sub", 0, -16)
        self.assertAlmostEqual(t, self.page.evaluate("V().currentTime"), delta=0.05)
        self.assertTrue(self.page.evaluate("V().paused"))
        self.assertEqual(self.page.evaluate("qaMedia.play"), plays)


if __name__ == "__main__":
    unittest.main()

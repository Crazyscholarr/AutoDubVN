"""Disposable real app for pointer QA at localhost:8760. No user state is loaded.

Run from the repository root: venv/Scripts/python tests/manual_dub_editor_qa.py
Close with Ctrl+C. Generated media/config/project state are temporary.
"""
import json
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import test_http_runtime as fixture
from autodub.server import http_api


def main():
    app = fixture.HttpRuntimeTests()
    app.setUp()
    try:
        app.stop_server()
        app.server = http_api.QuietServer(("127.0.0.1", 8760), http_api.Handler)
        app.worker = threading.Thread(target=app.server.serve_forever, daemon=True)
        app.worker.start()
        (app.root / "config.yaml").write_text(json.dumps({
            "output": {"dir": str(app.root / "exports")},
            "content_pipeline": {"database": str(app.root / "ideas.sqlite")}
        }), encoding="utf-8")
        video = app.root / "QA-editor-960x540.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=960x540:rate=24", "-t", "60", "-c:v",
                        "libx264", "-preset", "ultrafast", str(video)], check=True)
        logo = app.root / "QA-logo.png"
        Image.new("RGBA", (160, 90), (255, 180, 20, 230)).save(logo)
        status, _, body = app.request("/api/queue/add", {"path": str(video)})
        jid = json.loads(body)["id"]
        app.request("/api/project", {"id": jid,"segments":[
            {"start":0,"end":60,"src":"QA","vi":"Phụ đề Việt kiểm thử khi video dừng"}
        ]})
        print(json.dumps({"url":"http://127.0.0.1:8760/?editorDebug=1",
                          "video":str(video),"logo":str(logo),"id":jid}),flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        app.doCleanups()


if __name__ == "__main__":
    main()

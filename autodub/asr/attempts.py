"""Shared persistent failure ledger for gap and clock repair."""
import hashlib
import json
from pathlib import Path
from .long_audio import atomic_json


class Attempts:
    def __init__(self, audio):
        source=Path(audio)
        self.path=None
        self.data={}
        if source.is_file():
            stat=source.stat()
            key=hashlib.sha256(f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:v3'.encode()).hexdigest()[:24]
            self.path=source.parent/'asr_attempts'/f'{key}.json'
            try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass

    @staticmethod
    def key(start,end,engine,language,model,device,compute,batch,beam,prompt,direct):
        return json.dumps([round(start,3),round(end,3),engine,language,model,device,
                           compute,batch,beam,prompt,direct],ensure_ascii=False)

    def failed(self,key):
        return self.data.get(key,{}).get('reason') not in (None,'fixed','started')

    def forget_range(self, start, end):
        """Drop ledger rows that overlap a span so SFX-heavy holes can be retried."""
        try:
            lo, hi = float(start), float(end)
        except (TypeError, ValueError):
            return 0
        drop = []
        for key, row in list(self.data.items()):
            if not isinstance(row, dict):
                continue
            try:
                a = float(row.get("start", row.get("crop_start")))
                b = float(row.get("end", row.get("crop_end")))
            except (TypeError, ValueError):
                try:
                    parsed = json.loads(key)
                    a, b = float(parsed[0]), float(parsed[1])
                except (TypeError, ValueError, IndexError, json.JSONDecodeError):
                    continue
            if b > lo and a < hi:
                drop.append(key)
        for key in drop:
            self.data.pop(key, None)
        if drop and self.path:
            atomic_json(self.path, self.data)
        return len(drop)

    def record(self,key,row):
        self.data[key]=dict(row)
        if self.path:atomic_json(self.path,self.data)

#!/usr/bin/env python3
"""
開賽手勢「分類點選」標記工具（零依賴，Python 內建 http.server）。

一場一場顯示裁判特寫連續幀（每場 ~18 幀）。你點選「手剛伸出準備」那 1-2 幀
（點了變綠 = 正樣本）。沒點的自動當負樣本。

輸出 labels.json：{ "g_c01_t00239.jpg": 1, "g_c01_t00240.jpg": 0, ... }
  1 = 開賽準備手勢（正）; 0 = 非（負）。

啟動：detector/.venv/bin/python detector/label_gesture.py  → http://localhost:8013
"""
import os, json, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "dataset_gesture", "images")
LABELS_PATH = os.path.join(HERE, "dataset_gesture", "labels.json")

IMAGES = sorted(f for f in os.listdir(IMG_DIR) if f.endswith(".jpg"))
# 依場次分組: g_cNN_tTTTTT.jpg
groups = {}
for f in IMAGES:
    m = re.match(r"g_c(\d+)_t(\d+)\.jpg", f)
    if m:
        groups.setdefault(int(m.group(1)), []).append(f)
GROUP_IDS = sorted(groups.keys())
for gid in GROUP_IDS:
    groups[gid].sort()


def load_labels():
    if os.path.exists(LABELS_PATH):
        try:
            return json.load(open(LABELS_PATH))
        except Exception:
            return {}
    return {}


PAGE = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>開賽手勢標記</title>
<style>
 body{margin:0;font-family:system-ui,"PingFang TC",sans-serif;background:#111;color:#eee}
 header{padding:10px 14px;background:#1c1c1c;display:flex;gap:14px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:5}
 header b{color:#4ade80}
 button{font-size:15px;padding:8px 14px;border:0;border-radius:6px;cursor:pointer;background:#2563eb;color:#fff}
 button.sec{background:#444}
 .grid{display:flex;flex-wrap:wrap;gap:8px;padding:12px}
 .cell{position:relative;cursor:pointer;border:3px solid transparent;border-radius:8px;overflow:hidden}
 .cell.sel{border-color:#22c55e}
 .cell.sel::after{content:"✓ 準備手勢";position:absolute;top:4px;left:4px;background:#16a34a;color:#fff;font-size:12px;padding:2px 6px;border-radius:4px}
 .cell img{display:block;width:230px}
 .cell .t{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.6);color:#ffd;font-size:12px;padding:2px 4px;text-align:center}
 .hint{font-size:13px;color:#aaa}
</style></head><body>
<header>
 <span>場次 <b id="gid">?</b>（第 <span id="gpos">?</span>/<span id="gtot">?</span> 組）</span>
 <button class="sec" onclick="go(-1)">← 上一場</button>
 <button onclick="go(1)">下一場 →</button>
 <span id="saved" class="hint"></span>
 <span class="hint">點選「裁判手剛伸出來準備開賽」那 1-2 張（變綠=已選）。沒選的算負樣本。可複選、再點取消。</span>
</header>
<div id="grid" class="grid"></div>
<script>
let gids=[], gi=0, labels={};
const el=id=>document.getElementById(id);
async function load(){
 const r=await fetch('/api/data'); const d=await r.json();
 gids=d.group_ids; labels=d.labels||{}; el('gtot').textContent=gids.length;
 show();
}
function show(){
 const gid=gids[gi];
 el('gid').textContent=gid; el('gpos').textContent=gi+1;
 fetch('/api/group/'+gid).then(r=>r.json()).then(files=>{
  const grid=el('grid'); grid.replaceChildren();
  for(const f of files){
   const div=document.createElement('div');
   div.className='cell'+(labels[f]===1?' sel':'');
   const img=document.createElement('img'); img.src='/img/'+encodeURIComponent(f); img.loading='lazy';
   const t=document.createElement('div'); t.className='t'; t.textContent=f.match(/t(\\d+)/)[1];
   div.append(img,t);
   div.onclick=()=>{ labels[f]=labels[f]===1?0:1; div.classList.toggle('sel'); save(f,labels[f]); };
   grid.append(div);
  }
 });
}
function save(f,v){
 fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({file:f,label:v})}).then(()=>{el('saved').textContent='已存 '+f+' = '+v;});
}
function go(d){ gi=Math.max(0,Math.min(gids.length-1,gi+d)); show(); }
window.addEventListener('keydown',e=>{if(e.key==='ArrowRight')go(1);if(e.key==='ArrowLeft')go(-1);});
load();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/data":
            return self._send(200, json.dumps({"group_ids": GROUP_IDS, "labels": load_labels()}))
        if p.startswith("/api/group/"):
            gid = int(p.rsplit("/", 1)[-1])
            return self._send(200, json.dumps(groups.get(gid, [])))
        if p.startswith("/img/"):
            name = unquote(p[len("/img/"):])
            fp = os.path.join(IMG_DIR, name)
            if os.path.exists(fp):
                return self._send(200, open(fp, "rb").read(), "image/jpeg")
            return self._send(404, b"x", "text/plain")
        return self._send(404, b"x", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path == "/api/save":
            n = int(self.headers.get("Content-Length", 0))
            pl = json.loads(self.rfile.read(n) or b"{}")
            labels = load_labels()
            labels[pl["file"]] = int(pl["label"])
            json.dump(labels, open(LABELS_PATH, "w"), indent=2)
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, b"x", "text/plain")


if __name__ == "__main__":
    print("開賽手勢標記：http://localhost:8013")
    print(f"{len(IMAGES)} 幀 / {len(GROUP_IDS)} 場")
    ThreadingHTTPServer(("127.0.0.1", 8013), H).serve_forever()

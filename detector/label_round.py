#!/usr/bin/env python3
"""
第一回合分類「點選」標記工具(零依賴)。

顯示全片 38 個交界點的記分板裁圖。你點選「第一回合(回合格全零 000:000)」那些
(點了變綠 = 第一回合正樣本)。沒點的 = 第二/三回合(負樣本)。

輸出 labels.json:{ "x001_t252.jpg": 1, ... }  1=第一回合, 0=其他回合。
啟動:detector/.venv/bin/python detector/label_round.py → http://localhost:8014
"""
import os, json, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
# ROUND_DIR 環境變數指定要標的資料夾(預設 dataset_round);可標任何場地不必複製工具。
ROUND_DIR = os.environ.get("ROUND_DIR", "dataset_round")
IMG_DIR = os.path.join(HERE, ROUND_DIR, "images")
LABELS_PATH = os.path.join(HERE, ROUND_DIR, "labels.json")
IMAGES = sorted((f for f in os.listdir(IMG_DIR) if f.endswith(".jpg")),
                key=lambda f: int(re.search(r"x(\d+)", f).group(1)))


def load_labels():
    if os.path.exists(LABELS_PATH):
        try:
            return json.load(open(LABELS_PATH))
        except Exception:
            return {}
    return {}


PAGE = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>第一回合標記</title>
<style>
 body{margin:0;font-family:system-ui,"PingFang TC",sans-serif;background:#111;color:#eee}
 header{padding:10px 14px;background:#1c1c1c;position:sticky;top:0;z-index:5;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 header b{color:#4ade80}
 .grid{display:flex;flex-wrap:wrap;gap:10px;padding:12px}
 .cell{position:relative;cursor:pointer;border:3px solid transparent;border-radius:8px;overflow:hidden}
 .cell.sel{border-color:#22c55e}
 .cell.sel::after{content:"✓ 第一回合";position:absolute;top:4px;left:4px;background:#16a34a;color:#fff;font-size:12px;padding:2px 6px;border-radius:4px}
 .cell img{display:block;width:300px}
 .cell .t{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.6);color:#ffd;font-size:13px;padding:2px 4px;text-align:center}
 .hint{font-size:13px;color:#aaa}
 b#cnt{color:#4ade80}
</style></head><body>
<header>
 <span>已選第一回合 <b id="cnt">0</b> / <span id="tot">?</span> 個交界</span>
 <span class="hint">點選「回合格全零 000:000」= 第一回合(真開賽)那些。有出現 1 的是第2/3回合,不要點。可再點取消。自動儲存。</span>
</header>
<div id="grid" class="grid"></div>
<script>
let labels={}, files=[];
const el=id=>document.getElementById(id);
function cnt(){return Object.values(labels).filter(v=>v===1).length;}
async function load(){
 const d=await (await fetch('/api/data')).json();
 files=d.files; labels=d.labels||{}; el('tot').textContent=files.length;
 const grid=el('grid'); grid.replaceChildren();
 for(const f of files){
  const div=document.createElement('div');
  div.className='cell'+(labels[f]===1?' sel':'');
  const img=document.createElement('img'); img.src='/img/'+encodeURIComponent(f); img.loading='lazy';
  const t=document.createElement('div'); t.className='t';
  const tm=f.match(/t(\\d+)/)[1]; const s=parseInt(tm);
  t.textContent=`${f.match(/x(\\d+)/)[1]}  ${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
  div.append(img,t);
  div.onclick=()=>{ labels[f]=labels[f]===1?0:1; div.classList.toggle('sel'); save(f,labels[f]); el('cnt').textContent=cnt(); };
  grid.append(div);
 }
 el('cnt').textContent=cnt();
}
function save(f,v){fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:f,label:v})});}
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
            return self._send(200, json.dumps({"files": IMAGES, "labels": load_labels()}))
        if p.startswith("/img/"):
            fp = os.path.join(IMG_DIR, unquote(p[5:]))
            if os.path.exists(fp):
                return self._send(200, open(fp, "rb").read(), "image/jpeg")
            return self._send(404, b"x", "text/plain")
        return self._send(404, b"x", "text/plain")
    def do_POST(self):
        if urlparse(self.path).path == "/api/save":
            n = int(self.headers.get("Content-Length", 0))
            pl = json.loads(self.rfile.read(n) or b"{}")
            labels = load_labels(); labels[pl["file"]] = int(pl["label"])
            json.dump(labels, open(LABELS_PATH, "w"), indent=2)
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, b"x", "text/plain")


if __name__ == "__main__":
    print(f"第一回合標記:http://localhost:8014  ({len(IMAGES)} 個交界)")
    ThreadingHTTPServer(("127.0.0.1", 8014), H).serve_forever()

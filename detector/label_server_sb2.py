#!/usr/bin/env python3
"""
極簡記分板標記工具（零額外依賴，只用 Python 內建 http.server）。

用途：逐張顯示訓練幀，你用滑鼠拖一個框框住記分板，存成 YOLO 格式標記。
  - 一張圖只標一個框（記分板），class id = 0
  - 標記檔輸出到 dataset/labels/<same_name>.txt，格式: "0 xc yc w h"（皆正規化 0..1）
  - 記分板被完全遮擋看不到 → 按「跳過(無記分板)」，寫入空標記檔（負樣本）

啟動：
    detector/.venv/bin/python detector/label_server.py
然後開 http://localhost:8011
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "dataset_sb2", "images")
LBL_DIR = os.path.join(HERE, "dataset_sb2", "labels")
os.makedirs(LBL_DIR, exist_ok=True)

IMAGES = sorted(f for f in os.listdir(IMG_DIR) if f.lower().endswith((".jpg", ".png")))

PAGE = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>記分板標記</title>
<style>
  body{margin:0;font-family:system-ui,-apple-system,"PingFang TC",sans-serif;background:#111;color:#eee}
  header{padding:8px 12px;background:#1c1c1c;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  header b{color:#4ade80}
  .wrap{position:relative;display:inline-block;margin:8px}
  img{display:block;max-width:100%;user-select:none;-webkit-user-drag:none}
  #box{position:absolute;border:3px solid #22c55e;background:rgba(34,197,94,.15);display:none;pointer-events:none}
  button{font-size:15px;padding:8px 14px;border:0;border-radius:6px;cursor:pointer;background:#2563eb;color:#fff}
  button.sec{background:#444}
  button.warn{background:#b45309}
  .hint{font-size:13px;color:#aaa}
  kbd{background:#333;border-radius:4px;padding:1px 6px;border:1px solid #555}
</style></head><body>
<header>
  <span>第 <b id="idx">?</b> / <span id="total">?</span> 張</span>
  <span id="fname" class="hint"></span>
  <span id="saved" class="hint"></span>
  <button class="sec" onclick="go(-1)">← 上一張 (P)</button>
  <button onclick="save()">儲存並下一張 (S / Enter)</button>
  <button class="warn" onclick="skip()">跳過·無記分板 (X)</button>
  <span class="hint">在圖上「拖曳」框住整個記分板（含紅、計時器、藍三部分）。拖新框會覆蓋舊框。</span>
</header>
<div class="wrap" id="wrap">
  <img id="img" draggable="false">
  <div id="box"></div>
</div>
<script>
let items = [], cur = 0, imgW=0, imgH=0;
let drag=null, box=null; // box in DISPLAY px {x,y,w,h}
const el = id => document.getElementById(id);

async function load(){
  const r = await fetch('/api/list'); const d = await r.json();
  items = d.items; el('total').textContent = items.length;
  cur = d.start || 0; show();
}
function show(){
  const it = items[cur];
  el('idx').textContent = cur+1;
  el('fname').textContent = it.name + (it.labeled? '  ✓已標':'');
  const img = el('img');
  img.onload = ()=>{ imgW=img.naturalWidth; imgH=img.naturalHeight;
    // restore existing label as display box
    box=null; el('box').style.display='none';
    if(it.box){ // normalized xc,yc,w,h
      const dw=img.clientWidth, dh=img.clientHeight;
      const w=it.box[2]*dw, h=it.box[3]*dh;
      const x=it.box[0]*dw - w/2, y=it.box[1]*dh - h/2;
      box={x,y,w,h}; drawBox();
    }
  };
  img.src = '/img/'+encodeURIComponent(it.name)+'?t='+Date.now();
}
function drawBox(){
  const b=el('box');
  if(!box){b.style.display='none';return;}
  b.style.display='block';
  b.style.left=box.x+'px'; b.style.top=box.y+'px';
  b.style.width=box.w+'px'; b.style.height=box.h+'px';
}
const wrap=el('wrap');
el('img').addEventListener('mousedown',e=>{
  const r=el('img').getBoundingClientRect();
  drag={x0:e.clientX-r.left, y0:e.clientY-r.top}; e.preventDefault();
});
window.addEventListener('mousemove',e=>{
  if(!drag)return;
  const r=el('img').getBoundingClientRect();
  let x1=e.clientX-r.left, y1=e.clientY-r.top;
  x1=Math.max(0,Math.min(r.width,x1)); y1=Math.max(0,Math.min(r.height,y1));
  box={x:Math.min(drag.x0,x1),y:Math.min(drag.y0,y1),w:Math.abs(x1-drag.x0),h:Math.abs(y1-drag.y0)};
  drawBox();
});
window.addEventListener('mouseup',()=>{drag=null;});
function normBox(){
  if(!box||box.w<5||box.h<5) return null;
  const img=el('img'); const dw=img.clientWidth, dh=img.clientHeight;
  return [(box.x+box.w/2)/dw,(box.y+box.h/2)/dh, box.w/dw, box.h/dh];
}
async function save(){
  const nb=normBox();
  if(!nb){ alert('請先拖一個框框住記分板，或按「跳過」'); return; }
  await post({name:items[cur].name, box:nb});
  items[cur].box=nb; items[cur].labeled=true;
  go(1);
}
async function skip(){
  await post({name:items[cur].name, box:null});
  items[cur].box=null; items[cur].labeled=true;
  go(1);
}
async function post(payload){
  await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  el('saved').textContent='已存 '+payload.name;
}
function go(d){ cur=Math.max(0,Math.min(items.length-1,cur+d)); show(); }
window.addEventListener('keydown',e=>{
  if(e.key==='s'||e.key==='S'||e.key==='Enter'){e.preventDefault();save();}
  else if(e.key==='p'||e.key==='P'){go(-1);}
  else if(e.key==='x'||e.key==='X'){skip();}
});
load();
</script></body></html>"""


def yolo_path(name):
    stem = os.path.splitext(name)[0]
    return os.path.join(LBL_DIR, stem + ".txt")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if parsed.path == "/api/list":
            items = []
            first_unlabeled = 0
            seen_unlabeled = False
            for i, name in enumerate(IMAGES):
                lp = yolo_path(name)
                labeled = os.path.exists(lp)
                box = None
                if labeled:
                    txt = open(lp).read().strip()
                    if txt:
                        parts = txt.split()
                        if len(parts) == 5:
                            box = [float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])]
                else:
                    if not seen_unlabeled:
                        first_unlabeled = i
                        seen_unlabeled = True
                items.append({"name": name, "labeled": labeled, "box": box})
            return self._send(200, json.dumps({"items": items, "start": first_unlabeled}))
        if parsed.path.startswith("/img/"):
            name = parsed.path[len("/img/"):]
            from urllib.parse import unquote
            name = unquote(name)
            fp = os.path.join(IMG_DIR, name)
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                return self._send(200, data, "image/jpeg")
            return self._send(404, b"not found", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path == "/api/save":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = payload.get("name")
            box = payload.get("box")
            if not name:
                return self._send(400, json.dumps({"error": "no name"}))
            with open(yolo_path(name), "w") as f:
                if box:
                    f.write(f"0 {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}\n")
                # empty file = negative sample (no scoreboard)
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    port = 8015
    print(f"標記工具啟動：http://localhost:{port}")
    print(f"圖片來源：{IMG_DIR} ({len(IMAGES)} 張)")
    print(f"標記輸出：{LBL_DIR}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

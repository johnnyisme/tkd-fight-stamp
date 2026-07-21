// TKDFightStamp(Vercel 版)— 開賽時間戳複審 + 對戰表匹配
// 左影片 + 右純文字編輯器。貼「時間戳 場次」,複審後匹配對戰表產出 YouTube 章節清單。
// 偵測(YOLO)在本機版做,這版只做複審 + 匹配(對戰表合併走 serverless /api/merge)。

const STORAGE_KEY = "tkd-review-vercel-v1";
const SEEK_SECONDS = 5;

const state = { player: null, playerReady: false, lastKnownTime: 0 };
const el = (id) => document.getElementById(id);
const E = {
  youtubeUrl: el("youtube-url"),
  loadVideoBtn: el("load-video-btn"),
  currentTime: el("current-time"),
  back5Btn: el("back-5-btn"),
  forward5Btn: el("forward-5-btn"),
  togglePlayBtn: el("toggle-play-btn"),
  jumpLineBtn: el("jump-line-btn"),
  insertNowBtn: el("insert-now-btn"),
  lineStatus: el("line-status"),
  editor: el("editor"),
  finalOutput: el("final-output"),
  copyBtn: el("copy-btn"),
  buildFinalBtn: el("build-final-btn"),
  copyFinalBtn: el("copy-final-btn"),
  downloadFinalBtn: el("download-final-btn"),
  mergeSource: el("merge-source"),
  wegoFields: el("wego-fields"),
  tondarFields: el("tondar-fields"),
  eventId: el("event-id"),
  tondarEventNo: el("tondar-eventno"),
  tondarLoadDatesBtn: el("tondar-load-dates-btn"),
  tondarDate: el("tondar-date"),
  mergeStatus: el("merge-status"),
};

const pad2 = (v) => String(v).padStart(2, "0");
const fmtTime = (sec) => {
  const s = Math.max(0, Math.floor(sec || 0));
  return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`;
};
function parseLineTime(line) {
  const m = String(line).trim().match(/^(\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}|\d+)/);
  if (!m) return null;
  const p = m[1].split(":").map((x) => parseInt(x, 10));
  if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
  if (p.length === 2) return p[0] * 60 + p[1];
  return p[0];
}
function parseVideoId(input) {
  const t = (input || "").trim();
  if (/^[a-zA-Z0-9_-]{11}$/.test(t)) return t;
  try {
    const url = new URL(t);
    if (url.hostname === "youtu.be") return url.pathname.replace(/^\//, "").slice(0, 11);
    if (url.searchParams.get("v")) return url.searchParams.get("v").slice(0, 11);
    const parts = url.pathname.split("/").filter(Boolean);
    const i = parts.findIndex((p) => ["embed", "shorts", "live"].includes(p));
    if (i >= 0 && parts[i + 1]) return parts[i + 1].slice(0, 11);
  } catch (_e) { return ""; }
  return "";
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    youtubeUrl: E.youtubeUrl.value.trim(),
    eventId: E.eventId.value.trim(),
    mergeSource: E.mergeSource ? E.mergeSource.value : "wego",
    tondarEventNo: E.tondarEventNo ? E.tondarEventNo.value.trim() : "",
    text: E.editor.value,
  }));
}
function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (_e) { return null; }
}

// ---- 播放器 ----
function seekTo(sec) {
  if (!state.playerReady || !state.player) { window.alert("請先載入影片。"); return; }
  state.player.seekTo(sec, true); state.player.playVideo(); state.lastKnownTime = sec;
}
function seekBy(d) {
  if (!state.playerReady || !state.player) return;
  const n = Math.max(0, state.player.getCurrentTime() + d);
  state.player.seekTo(n, true); state.lastKnownTime = n;
}
function togglePlay() {
  if (!state.playerReady || !state.player) return;
  const st = state.player.getPlayerState();
  if (st === YT.PlayerState.PLAYING) state.player.pauseVideo(); else state.player.playVideo();
}
let pendingVideoId = null;
function createOrLoadPlayer(videoId) {
  if (!videoId) { window.alert("請輸入有效的 YouTube 網址或 ID。"); return; }
  if (!window.YT || !window.YT.Player) { pendingVideoId = videoId; return; }
  if (state.player && state.player.loadVideoById) { state.player.loadVideoById(videoId); save(); return; }
  state.player = new YT.Player("player-frame", {
    videoId,
    playerVars: { rel: 0, modestbranding: 1 },
    events: {
      onReady: () => { state.playerReady = true; },
      onStateChange: () => { if (state.player?.getCurrentTime) state.lastKnownTime = state.player.getCurrentTime(); },
    },
  });
  save();
}

// ---- 文字編輯器工具 ----
function currentLineInfo() {
  const ta = E.editor;
  const pos = ta.selectionStart;
  const text = ta.value;
  const before = text.slice(0, pos);
  const lineStart = before.lastIndexOf("\n") + 1;
  let lineEnd = text.indexOf("\n", pos);
  if (lineEnd === -1) lineEnd = text.length;
  const lineIndex = before.split("\n").length - 1;
  return { lineIndex, lineStart, lineEnd, lineText: text.slice(lineStart, lineEnd) };
}
function jumpToCurrentLine() {
  const { lineText } = currentLineInfo();
  const s = parseLineTime(lineText);
  if (s == null) { E.lineStatus.textContent = "本行無可解析的時間戳"; return; }
  E.lineStatus.textContent = `跳到 ${fmtTime(s)}`;
  seekTo(s);
}
function insertNowLine() {
  if (!state.playerReady || !state.player) { window.alert("請先載入影片。"); return; }
  const t = Math.floor(state.player.getCurrentTime());
  const ta = E.editor;
  const text = ta.value;
  const { lineEnd } = currentLineInfo();
  const insert = `\n${fmtTime(t)}\t`;
  ta.value = text.slice(0, lineEnd) + insert + text.slice(lineEnd);
  const newPos = lineEnd + insert.length;
  ta.focus(); ta.setSelectionRange(newPos, newPos);
  save(); updateLineCount();
}
function updateLineCount() {
  const lines = E.editor.value.split("\n").filter((l) => l.trim());
  E.lineStatus.textContent = `${lines.length} 行`;
}

// ---- 對戰表來源切換 ----
function parseEventId(input) {
  const t = (input || "").trim();
  if (/^\d+$/.test(t)) return t;
  const m = t.match(/\/event\/(\d+)/);
  return m ? m[1] : "";
}
function parseTondarEventNo(input) {
  const t = (input || "").trim();
  if (/^\d+$/.test(t)) return t;
  const m = t.match(/EventNo=(\d+)/i);
  return m ? m[1] : "";
}
function tondarDateLabel(dte) {
  const s = String(dte);
  if (s.length === 7) return `${s.slice(0, 3)}/${s.slice(3, 5)}/${s.slice(5, 7)}`;
  return s;
}
function syncMergeSource() {
  const src = E.mergeSource ? E.mergeSource.value : "wego";
  if (E.wegoFields) E.wegoFields.style.display = src === "wego" ? "" : "none";
  if (E.tondarFields) E.tondarFields.style.display = src === "tondar" ? "" : "none";
  save();
}

// ---- 事件 ----
E.loadVideoBtn.addEventListener("click", () => createOrLoadPlayer(parseVideoId(E.youtubeUrl.value)));
E.back5Btn.addEventListener("click", () => seekBy(-SEEK_SECONDS));
E.forward5Btn.addEventListener("click", () => seekBy(SEEK_SECONDS));
E.togglePlayBtn.addEventListener("click", togglePlay);
E.jumpLineBtn.addEventListener("click", jumpToCurrentLine);
E.insertNowBtn.addEventListener("click", insertNowLine);
E.editor.addEventListener("input", () => { save(); updateLineCount(); });
E.editor.addEventListener("click", () => {
  const { lineText } = currentLineInfo();
  const s = parseLineTime(lineText);
  E.lineStatus.textContent = s != null ? `本行 ${fmtTime(s)}(可按「跳到本行時間」)` : "本行無時間戳";
});
E.youtubeUrl.addEventListener("change", save);
E.eventId.addEventListener("change", save);
if (E.mergeSource) E.mergeSource.addEventListener("change", syncMergeSource);
E.copyBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(E.editor.value).then(() => {
    const o = E.copyBtn.textContent; E.copyBtn.textContent = "已複製"; setTimeout(() => (E.copyBtn.textContent = o), 1200);
  });
});

// tondar:載入日期(走 main 現有的 /api/tondar-schedule?mode=dates)
if (E.tondarLoadDatesBtn) E.tondarLoadDatesBtn.addEventListener("click", async () => {
  const eventNo = parseTondarEventNo(E.tondarEventNo.value);
  if (!eventNo) { E.mergeStatus.textContent = "請填 tondar EventNo"; return; }
  E.mergeStatus.textContent = "載入日期中...";
  try {
    const d = await (await fetch(`/api/tondar-schedule?eventNo=${eventNo}&mode=dates`)).json();
    const dates = d.dates || [];
    E.tondarDate.innerHTML = "";
    dates.forEach((dte) => {
      const o = document.createElement("option");
      o.value = dte; o.textContent = tondarDateLabel(dte);
      E.tondarDate.appendChild(o);
    });
    E.mergeStatus.textContent = `已載入 ${dates.length} 個日期`;
    save();
  } catch (e) {
    E.mergeStatus.textContent = "載入日期失敗:" + e.message;
  }
});

// 補齊對戰資訊(走 serverless /api/merge)
E.buildFinalBtn.addEventListener("click", async () => {
  if (!E.editor.value.trim()) { E.mergeStatus.textContent = "上方清單是空的"; return; }
  const src = E.mergeSource ? E.mergeSource.value : "wego";
  let body;
  if (src === "tondar") {
    const eventNo = parseTondarEventNo(E.tondarEventNo.value);
    const edte = E.tondarDate.value;
    if (!eventNo) { E.mergeStatus.textContent = "請填 tondar EventNo"; return; }
    if (!edte) { E.mergeStatus.textContent = "請先「載入日期」並選一天"; return; }
    body = { source: "tondar", event_no: eventNo, edte, text: E.editor.value };
  } else {
    const eventId = parseEventId(E.eventId.value);
    if (!eventId) { E.mergeStatus.textContent = "請填入賽事 event 網址或 ID"; return; }
    body = { source: "wego", event_id: eventId, text: E.editor.value };
  }
  E.mergeStatus.textContent = "補齊中(爬對戰表)...";
  try {
    const r = await fetch("/api/merge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || "未知錯誤");
    E.finalOutput.value = d.result;
    E.mergeStatus.textContent = "完成 ✓";
    save();
  } catch (e) {
    E.mergeStatus.textContent = "補齊失敗:" + e.message;
  }
});
E.copyFinalBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(E.finalOutput.value).then(() => {
    const o = E.copyFinalBtn.textContent; E.copyFinalBtn.textContent = "已複製"; setTimeout(() => (E.copyFinalBtn.textContent = o), 1200);
  });
});
E.downloadFinalBtn.addEventListener("click", () => {
  const blob = new Blob([E.finalOutput.value], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "youtube-chapters.txt"; a.click();
  URL.revokeObjectURL(a.href);
});

// 快捷鍵
document.addEventListener("keydown", (e) => {
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  if (e.key === "j" || e.key === "J") seekBy(-SEEK_SECONDS);
  if (e.key === "l" || e.key === "L") seekBy(SEEK_SECONDS);
});

setInterval(() => {
  if (state.playerReady && state.player?.getCurrentTime) {
    state.lastKnownTime = state.player.getCurrentTime();
    E.currentTime.textContent = fmtTime(state.lastKnownTime);
  }
}, 300);

// 啟動還原
(function restore() {
  const saved = loadSaved();
  if (saved) {
    if (saved.text) { E.editor.value = saved.text; updateLineCount(); }
    if (saved.youtubeUrl) E.youtubeUrl.value = saved.youtubeUrl;
    if (saved.eventId) E.eventId.value = saved.eventId;
    if (saved.mergeSource && E.mergeSource) E.mergeSource.value = saved.mergeSource;
    if (saved.tondarEventNo && E.tondarEventNo) E.tondarEventNo.value = saved.tondarEventNo;
  }
  syncMergeSource();
})();

window.onYouTubeIframeAPIReady = function () {
  let vid = pendingVideoId;
  if (!vid) {
    const u = E.youtubeUrl.value.trim();
    if (u) vid = parseVideoId(u);
  }
  if (vid) { pendingVideoId = null; createOrLoadPlayer(vid); }
};

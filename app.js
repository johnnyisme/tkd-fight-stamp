// TKDFightStamp — AI 開賽時間戳複審(純文字工作流)
// 左影片 + 右純文字編輯器。每行「時間戳 場次」,自由改/插/刪。
// 游標所在行可跳轉影片;播到精確點可插入目前時間。

const STORAGE_KEY = "tkd-review-text-v1";
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
  loadCandidatesBtn: el("load-candidates-btn"),
  jumpLineBtn: el("jump-line-btn"),
  insertNowBtn: el("insert-now-btn"),
  candidateStatus: el("candidate-status"),
  lineStatus: el("line-status"),
  editor: el("editor"),
  finalOutput: el("final-output"),
  copyBtn: el("copy-btn"),
  buildFinalBtn: el("build-final-btn"),
  eventId: el("event-id"),
  mergeStatus: el("merge-status"),
  copyFinalBtn: el("copy-final-btn"),
  downloadFinalBtn: el("download-final-btn"),
  videoPath: el("video-path"),
  roundFull: el("round-full"),
  modelSet: el("model-set"),
  detectBtn: el("detect-btn"),
  dlUrl: el("dl-url"),
  dlBtn: el("dl-btn"),
  dlProgress: el("dl-progress"),
  dlStage: el("dl-stage"),
  dlFill: el("dl-fill"),
  dlMsg: el("dl-msg"),
  applyFilter: el("apply-filter"),
  refilterBtn: el("refilter-btn"),
  mergeSource: el("merge-source"),
  wegoFields: el("wego-fields"),
  tondarFields: el("tondar-fields"),
  tondarEventNo: el("tondar-eventno"),
  tondarLoadDatesBtn: el("tondar-load-dates-btn"),
  tondarDate: el("tondar-date"),
  detectProgress: el("detect-progress"),
  progressStage: el("progress-stage"),
  progressFill: el("progress-fill"),
  progressMsg: el("progress-msg"),
};

const pad2 = (v) => String(v).padStart(2, "0");
const fmtTime = (sec) => {
  const s = Math.max(0, Math.floor(sec || 0));
  return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`;
};
// 解析一行開頭的時間戳(HH:MM:SS / MM:SS / 秒),回傳秒或 null
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
    videoPath: E.videoPath.value.trim(),
    modelSet: E.modelSet ? E.modelSet.value : "combined",
    roundFull: E.roundFull ? E.roundFull.value : "120",
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
let pendingVideoId = null;   // YT API 還沒好時,先記住要載入的影片
function createOrLoadPlayer(videoId) {
  if (!videoId) { window.alert("請輸入有效的 YouTube 網址或 ID。"); return; }
  // YT API 還沒載完 → 記住,等 onYouTubeIframeAPIReady 再建
  if (!window.YT || !window.YT.Player) { pendingVideoId = videoId; return; }
  if (state.player && state.player.loadVideoById) { state.player.loadVideoById(videoId); save(); return; }
  state.player = new YT.Player("player-frame", {
    videoId,
    playerVars: { rel: 0, modestbranding: 1 },
    events: {
      onReady: () => { state.playerReady = true; },
      onError: (e) => { E.candidateStatus.textContent = "影片載入錯誤 code=" + e.data + "(可能不允許嵌入或影片私人)"; },
      onStateChange: () => { if (state.player?.getCurrentTime) state.lastKnownTime = state.player.getCurrentTime(); },
    },
  });
  save();
}

// ---- 文字編輯器工具 ----
function currentLineInfo() {
  // 回傳 {lineIndex, lineStart, lineEnd, lineText} 依游標位置
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
  // 在游標所在行的「行尾」後插入新行
  const { lineEnd } = currentLineInfo();
  const insert = `\n${fmtTime(t)}\t`;
  ta.value = text.slice(0, lineEnd) + insert + text.slice(lineEnd);
  // 游標移到新行末(場次待填)
  const newPos = lineEnd + insert.length;
  ta.focus(); ta.setSelectionRange(newPos, newPos);
  save(); updateLineCount();
}
function updateLineCount() {
  const lines = E.editor.value.split("\n").filter((l) => l.trim());
  E.lineStatus.textContent = `${lines.length} 行`;
}

// ---- 載入 AI 候選 ----
async function loadCandidates() {
  E.candidateStatus.textContent = "載入中...";
  try {
    const r = await fetch("./candidates.json?t=" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    const lines = (data.candidates || [])
      .slice().sort((a, b) => a.t_sec - b.t_sec)
      .map((c) => `${fmtTime(c.t_sec)}\t${c.matchnum || ""}`);
    // 若編輯器已有內容,問是否覆蓋
    if (E.editor.value.trim() && !window.confirm("編輯器已有內容,要用 AI 候選覆蓋嗎?(取消則附加在後面)")) {
      E.editor.value += "\n" + lines.join("\n");
    } else {
      E.editor.value = lines.join("\n");
    }
    if (data.youtube_url && !E.youtubeUrl.value.trim()) {
      E.youtubeUrl.value = data.youtube_url;
      const vid = parseVideoId(data.youtube_url);
      if (vid) createOrLoadPlayer(vid);
    }
    save(); updateLineCount();
    E.candidateStatus.textContent = `已載入 ${lines.length} 場`;
  } catch (e) {
    E.candidateStatus.textContent = "載入失敗:" + e.message + "(需用本機伺服器開啟)";
  }
}

// ---- 事件 ----
E.loadVideoBtn.addEventListener("click", () => createOrLoadPlayer(parseVideoId(E.youtubeUrl.value)));
E.back5Btn.addEventListener("click", () => seekBy(-SEEK_SECONDS));
E.forward5Btn.addEventListener("click", () => seekBy(SEEK_SECONDS));
E.togglePlayBtn.addEventListener("click", togglePlay);
E.loadCandidatesBtn.addEventListener("click", loadCandidates);
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
E.videoPath.addEventListener("change", save);
E.detectBtn.addEventListener("click", startDetect);
if (E.dlBtn) E.dlBtn.addEventListener("click", startDownload);
if (E.refilterBtn) E.refilterBtn.addEventListener("click", startRefilter);
E.copyBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(E.editor.value).then(() => {
    const o = E.copyBtn.textContent; E.copyBtn.textContent = "已複製"; setTimeout(() => (E.copyBtn.textContent = o), 1200);
  });
});
// ---- 下載 YouTube 影片(呼叫後端 yt-dlp + 輪詢進度)----
let dlTimer = null;
async function startDownload() {
  const url = E.dlUrl.value.trim();
  if (!url) { E.dlStage.textContent = "請填 YouTube 網址"; E.dlProgress.style.display = "block"; return; }
  E.dlBtn.disabled = true;
  E.dlProgress.style.display = "block";
  E.dlStage.textContent = "啟動下載...";
  E.dlFill.style.width = "0%";
  E.dlMsg.textContent = "";
  try {
    const r = await fetch("/api/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const d = await r.json();
    if (!d.ok) { E.dlStage.textContent = "無法開始:" + d.error; E.dlBtn.disabled = false; return; }
    pollDownload();
  } catch (e) {
    E.dlStage.textContent = "下載請求失敗:" + e.message;
    E.dlBtn.disabled = false;
  }
}
function pollDownload() {
  if (dlTimer) clearInterval(dlTimer);
  dlTimer = setInterval(async () => {
    try {
      const d = await (await fetch("/api/dl_progress?t=" + Date.now())).json();
      E.dlStage.textContent = d.running ? "下載中..." : (d.done ? "" : "處理中...");
      E.dlFill.style.width = (d.percent || 0) + "%";
      E.dlMsg.textContent = d.msg || "";
      if (d.done) {
        clearInterval(dlTimer); dlTimer = null;
        E.dlBtn.disabled = false;
        if (d.error) {
          E.dlStage.textContent = "下載失敗";
          E.dlMsg.textContent = d.error.slice(0, 300);
        } else {
          E.dlStage.textContent = "下載完成 ✓";
          E.dlFill.style.width = "100%";
          // 路徑自動填入偵測欄
          if (d.path) { E.videoPath.value = d.path; save(); }
          E.dlMsg.textContent = "已存到:" + (d.path || "") + " —— 可直接按「開始偵測」";
        }
      }
    } catch (e) { /* 輪詢暫時失敗,下次再試 */ }
  }, 2000);
}
// ---- 偵測(呼叫後端 pipeline + 輪詢進度)----
let pollTimer = null;
async function startDetect() {
  const video = E.videoPath.value.trim();
  if (!video) { E.progressStage.textContent = "請填本機影片路徑"; E.detectProgress.style.display = "block"; return; }
  const roundFull = E.roundFull.value;
  const modelSet = E.modelSet ? E.modelSet.value : "combined";
  const applyFilter = E.applyFilter ? E.applyFilter.checked : true;
  E.detectBtn.disabled = true;
  E.detectProgress.style.display = "block";
  E.progressStage.textContent = "啟動偵測...";
  E.progressFill.style.width = "0%";
  E.progressMsg.textContent = "";
  try {
    const r = await fetch("/api/detect", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video, round_full: roundFull, model_set: modelSet, apply_filter: applyFilter }),
    });
    const d = await r.json();
    if (!d.ok) { E.progressStage.textContent = "無法開始:" + d.error; E.detectBtn.disabled = false; return; }
    pollProgress();
  } catch (e) {
    E.progressStage.textContent = "偵測請求失敗:" + e.message;
    E.detectBtn.disabled = false;
  }
}
// ---- 重新過濾(不重掃,只用上次掃描交界重跑 finalize+OCR)----
async function startRefilter() {
  const modelSet = E.modelSet ? E.modelSet.value : "combined";
  const applyFilter = E.applyFilter ? E.applyFilter.checked : true;
  const video = E.videoPath.value.trim();
  E.refilterBtn.disabled = true; E.detectBtn.disabled = true;
  E.detectProgress.style.display = "block";
  E.progressStage.textContent = "重新過濾中(不重掃)...";
  E.progressFill.style.width = "0%"; E.progressMsg.textContent = "";
  try {
    const r = await fetch("/api/refilter", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_set: modelSet, apply_filter: applyFilter, video }),
    });
    const d = await r.json();
    if (!d.ok) {
      E.progressStage.textContent = "無法重新過濾:" + d.error;
      E.refilterBtn.disabled = false; E.detectBtn.disabled = false; return;
    }
    pollProgress(() => { E.refilterBtn.disabled = false; });
  } catch (e) {
    E.progressStage.textContent = "重新過濾請求失敗:" + e.message;
    E.refilterBtn.disabled = false; E.detectBtn.disabled = false;
  }
}
function pollProgress(onDone) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const d = await (await fetch("/api/progress?t=" + Date.now())).json();
      E.progressStage.textContent = d.stage || "處理中...";
      E.progressFill.style.width = (d.percent || 0) + "%";
      E.progressMsg.textContent = d.msg || "";
      if (d.done) {
        clearInterval(pollTimer); pollTimer = null;
        E.detectBtn.disabled = false;
        if (typeof onDone === "function") onDone();
        if (d.error) {
          E.progressStage.textContent = "失敗";
          E.progressMsg.textContent = d.error.slice(0, 300);
        } else {
          E.progressStage.textContent = "完成 ✓";
          E.progressFill.style.width = "100%";
          // 結果(時間戳+場次)填入編輯器
          if (d.result) {
            const lines = d.result.split("\n").filter((l) => l.trim() && !l.startsWith("時間戳"));
            E.editor.value = lines.join("\n");
            updateLineCount(); save();
          } else {
            E.progressMsg.textContent = "(結果 0 筆 —— 可取消「套用第一回合過濾」再按「重新過濾」拿全部交界)";
          }
        }
      }
    } catch (e) { /* 輪詢暫時失敗,下次再試 */ }
  }, 2000);
}

function parseEventId(input) {
  const t = (input || "").trim();
  if (/^\d+$/.test(t)) return t;
  const m = t.match(/\/event\/(\d+)/);
  return m ? m[1] : "";
}
// tondar:從「18」或整個 ScheduleC 網址取 EventNo
function parseTondarEventNo(input) {
  const t = (input || "").trim();
  if (/^\d+$/.test(t)) return t;
  const m = t.match(/EventNo=(\d+)/i);
  return m ? m[1] : "";
}
// 對戰表來源切換(wego / tondar)
function syncMergeSource() {
  const src = E.mergeSource ? E.mergeSource.value : "wego";
  if (E.wegoFields) E.wegoFields.style.display = src === "wego" ? "" : "none";
  if (E.tondarFields) E.tondarFields.style.display = src === "tondar" ? "" : "none";
  save();
}
if (E.mergeSource) E.mergeSource.addEventListener("change", syncMergeSource);

// tondar:載入該賽事的比賽日期到下拉選單
if (E.tondarLoadDatesBtn) E.tondarLoadDatesBtn.addEventListener("click", async () => {
  const eventNo = parseTondarEventNo(E.tondarEventNo.value);
  if (!eventNo) { E.mergeStatus.textContent = "請填 tondar EventNo"; return; }
  E.mergeStatus.textContent = "載入日期中...";
  try {
    const r = await fetch("/api/tondar_dates", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_no: eventNo }),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || "未知錯誤");
    E.tondarDate.innerHTML = "";
    (d.dates || []).forEach((dte) => {
      const o = document.createElement("option");
      o.value = dte; o.textContent = tondarDateLabel(dte);
      E.tondarDate.appendChild(o);
    });
    E.mergeStatus.textContent = `已載入 ${(d.dates || []).length} 個日期`;
    save();
  } catch (e) {
    E.mergeStatus.textContent = "載入日期失敗:" + e.message;
  }
});
// 民國日期 1150512 → 115/05/12
function tondarDateLabel(dte) {
  const s = String(dte);
  if (s.length === 7) return `${s.slice(0, 3)}/${s.slice(3, 5)}/${s.slice(5, 7)}`;
  return s;
}

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
    E.mergeStatus.textContent = "補齊失敗:" + e.message + "(需用 serve.py 啟動,非 http.server)";
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

// 快捷鍵(游標不在輸入框/文字框時)
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

// 啟動還原(頁面載入時先跑,不等 YT API)
(function restore() {
  const saved = loadSaved();
  if (saved) {
    if (saved.text) { E.editor.value = saved.text; updateLineCount(); }
    if (saved.youtubeUrl) E.youtubeUrl.value = saved.youtubeUrl;
    if (saved.eventId) E.eventId.value = saved.eventId;
    if (saved.videoPath) E.videoPath.value = saved.videoPath;
    if (saved.modelSet && E.modelSet) E.modelSet.value = saved.modelSet;
    if (saved.roundFull && E.roundFull) E.roundFull.value = saved.roundFull;
    if (saved.mergeSource && E.mergeSource) E.mergeSource.value = saved.mergeSource;
    if (saved.tondarEventNo && E.tondarEventNo) E.tondarEventNo.value = saved.tondarEventNo;
  }
  syncMergeSource();  // 依還原的來源顯示對應欄位
})();

// YT API 就緒 → 建立播放器(消化 pending,或還原上次的網址)
window.onYouTubeIframeAPIReady = function () {
  let vid = pendingVideoId;
  if (!vid) {
    const u = E.youtubeUrl.value.trim();
    if (u) vid = parseVideoId(u);
  }
  if (vid) { pendingVideoId = null; createOrLoadPlayer(vid); }
};

const DEFAULT_SOURCE_FILE = "./對打第二天-各場地對戰資訊.txt";
const STORAGE_KEY = "tkd-fight-stamp-v1";
const SEEK_SECONDS = 60;
const EVENT_BASE_URL = "https://wego-tkd-web.onrender.com";

const state = {
  sourceText: "",
  matches: [],
  selectedIndex: 0,
  videoInput: "",
  player: null,
  playerReady: false,
  lastKnownTime: 0,
};

const elements = {
  youtubeUrl: document.getElementById("youtube-url"),
  eventUrl: document.getElementById("event-url"),
  generateFromEventBtn: document.getElementById("generate-from-event-btn"),
  fetchStatus: document.getElementById("fetch-status"),
  loadVideoBtn: document.getElementById("load-video-btn"),
  currentTime: document.getElementById("current-time"),
  recordedCount: document.getElementById("recorded-count"),
  totalCount: document.getElementById("total-count"),
  remainingCount: document.getElementById("remaining-count"),
  back5Btn: document.getElementById("back-5-btn"),
  forward5Btn: document.getElementById("forward-5-btn"),
  togglePlayBtn: document.getElementById("toggle-play-btn"),
  sourceText: document.getElementById("source-text"),
  loadMatchesBtn: document.getElementById("load-matches-btn"),
  resetProgressBtn: document.getElementById("reset-progress-btn"),
  autoNext: document.getElementById("auto-next"),
  prevBtn: document.getElementById("prev-btn"),
  markBtn: document.getElementById("mark-btn"),
  clearBtn: document.getElementById("clear-btn"),
  nextBtn: document.getElementById("next-btn"),
  currentMatchCard: document.getElementById("current-match-card"),
  matchList: document.getElementById("match-list"),
  outputText: document.getElementById("output-text"),
  copyOutputBtn: document.getElementById("copy-output-btn"),
  downloadOutputBtn: document.getElementById("download-output-btn"),
};

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatTime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remain = seconds % 60;
  return `${pad2(hours)}:${pad2(minutes)}:${pad2(remain)}`;
}

function parseVideoId(input) {
  const trimmed = input.trim();
  if (!trimmed) return "";
  if (/^[a-zA-Z0-9_-]{11}$/.test(trimmed)) return trimmed;

  try {
    const url = new URL(trimmed);
    if (url.hostname === "youtu.be") {
      return url.pathname.replace(/^\//, "").slice(0, 11);
    }
    if (url.searchParams.get("v")) {
      return url.searchParams.get("v").slice(0, 11);
    }
    const parts = url.pathname.split("/").filter(Boolean);
    const embedIndex = parts.findIndex((part) => ["embed", "shorts", "live"].includes(part));
    if (embedIndex >= 0 && parts[embedIndex + 1]) {
      return parts[embedIndex + 1].slice(0, 11);
    }
  } catch (_error) {
    return "";
  }

  return "";
}

function parseEventId(input) {
  const trimmed = input.trim();
  if (!trimmed) return "";
  if (/^\d+$/.test(trimmed)) return trimmed;

  try {
    const url = new URL(trimmed);
    const match = url.pathname.match(/\/event\/(\d+)/);
    return match?.[1] || "";
  } catch (_error) {
    const match = trimmed.match(/\/event\/(\d+)/);
    return match?.[1] || "";
  }
}

function simplifyTeamName(team) {
  let cleaned = (team || "").trim();
  const patterns = [
    /^(?:臺|台)北市立/,
    /^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄)市/,
    /^(?:臺|台)北縣/,
    /^基隆市/,
    /^新竹縣/,
    /^屏東縣/,
    /^宜蘭縣/,
    /^花蓮縣/,
    /^台東縣/,
    /^臺東縣/,
    /^苗栗縣/,
    /^彰化縣/,
    /^南投縣/,
    /^雲林縣/,
    /^嘉義縣/,
    /^嘉義市/,
  ];
  for (const pattern of patterns) {
    cleaned = cleaned.replace(pattern, "");
  }
  return cleaned.trim() || (team || "").trim();
}

function simplifyCategoryName(categoryName) {
  const text = (categoryName || "").trim();
  let level = "";
  if (text.includes("國中")) level = "(國)";
  else if (text.includes("高中社會")) level = "(社高)";

  const gender = text.includes("女子") ? "女子" : text.includes("男子") ? "男子" : text;
  const match = text.match(/(\d+KG\+?|\d+KG)/);
  const weight = match ? match[1] : "";
  return gender && weight ? `${level}${gender}${weight}` : text;
}

function formatCompetitor(team, name, isWinner) {
  const competitor = [simplifyTeamName(team), (name || "").trim()].filter(Boolean).join(" ");
  return isWinner ? `${competitor} (勝)` : competitor || "資料缺漏";
}

function courtHeading(courtId) {
  const chineseNumbers = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
    11: "十一",
    12: "十二",
  };
  return `第${chineseNumbers[courtId] || courtId}場地`;
}

function matchSortKey(matchNumber) {
  return String(matchNumber)
    .split("-")
    .map((part) => {
      const value = Number.parseInt(part, 10);
      return Number.isNaN(value) ? 999999 : value;
    });
}

function compareMatchSortKey(left, right) {
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const leftValue = left[index] ?? -1;
    const rightValue = right[index] ?? -1;
    if (leftValue !== rightValue) return leftValue - rightValue;
  }
  return 0;
}

function setFetchStatus(message, tone = "") {
  elements.fetchStatus.textContent = message;
  elements.fetchStatus.className = tone ? `fetch-status ${tone}` : "fetch-status";
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function describeError(error) {
  if (!error) return "未知錯誤";
  if (error instanceof Error && error.message) return error.message;
  return String(error);
}

function isConnectionPoolError(error) {
  const message = describeError(error);
  return /connection pool exhausted|HTTP 500/i.test(message);
}

async function fetchJson(path, contextLabel = path) {
  const response = await fetch(`${EVENT_BASE_URL}${path}`);
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    const suffix = body ? ` | ${body.slice(0, 180)}` : "";
    throw new Error(`${contextLabel} 回傳 HTTP ${response.status}${suffix}`);
  }
  return response.json();
}

async function fetchJsonWithRetry(path, contextLabel, maxAttempts = 3) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fetchJson(path, contextLabel);
    } catch (error) {
      lastError = error;
      if (attempt < maxAttempts) {
        const waitMs = isConnectionPoolError(error)
          ? Math.min(1200 * (2 ** (attempt - 1)), 10000)
          : 500 * attempt;
        const waitText = (waitMs / 1000).toFixed(waitMs >= 1000 ? 1 : 0);
        setFetchStatus(`${contextLabel} 失敗，第 ${attempt} 次重試中，等待 ${waitText} 秒...`, "pending");
        await sleep(waitMs);
      }
    }
  }
  throw lastError;
}

async function generateSourceTextFromEvent(eventId) {
  setFetchStatus(`正在讀取賽事 ${eventId} 的量級清單...`, "pending");
  const categories = await fetchJsonWithRetry(
    `/api/public/events/${eventId}/categories`,
    `賽事 ${eventId} 量級清單`,
    5
  );

  if (!Array.isArray(categories) || !categories.length) {
    throw new Error(`賽事 ${eventId} 沒有可用的量級資料`);
  }

  const groupedMatches = new Map();
  const seenMatchIds = new Set();

  const schedules = [];
  for (const [index, category] of categories.entries()) {
    const label = category.code ? `${category.code} ${category.name}` : category.name;
    setFetchStatus(`正在抓取量級 ${index + 1}/${categories.length}: ${label}`, "pending");
    const schedule = await fetchJsonWithRetry(
      `/api/public/events/${eventId}/categories/${category.id}/schedule`,
      `量級 ${label} 賽程`,
      5
    );
    schedules.push({ categoryName: category.name, data: schedule.data || [] });
    await sleep(650);
  }

  for (const schedule of schedules) {
    for (const match of schedule.data) {
      const matchId = String(match.match_id || "").trim();
      const matchNumber = match.matchnumber;
      const courtId = match.courtid;
      const player1Id = match.player1_id;
      const player2Id = match.player2_id;
      const winnerId = match.winner_id;

      if (!matchId || seenMatchIds.has(matchId)) continue;
      if (courtId === null || courtId === undefined || matchNumber === null || matchNumber === undefined || matchNumber === "") continue;
      if (player1Id === "BYE" || player2Id === "BYE") continue;

      seenMatchIds.add(matchId);
      const courtKey = Number(courtId);
      if (!groupedMatches.has(courtKey)) groupedMatches.set(courtKey, []);
      groupedMatches.get(courtKey).push({
        matchNumber: String(matchNumber),
        sortKey: matchSortKey(matchNumber),
        left: formatCompetitor(match.p1_team, match.p1_display, winnerId === player1Id),
        right: formatCompetitor(match.p2_team, match.p2_display, winnerId === player2Id),
        categoryName: simplifyCategoryName(schedule.categoryName),
        round: match.round || "",
      });
    }
  }

  const sortedCourts = [...groupedMatches.entries()].sort((left, right) => left[0] - right[0]);
  const lines = [];
  for (const [courtId, matches] of sortedCourts) {
    if (lines.length) lines.push("");
    lines.push(courtHeading(courtId));
    matches.sort((left, right) => compareMatchSortKey(left.sortKey, right.sortKey));
    for (const match of matches) {
      lines.push(`${match.matchNumber} ${match.left} vs ${match.right} | ${match.categoryName} | ${match.round}`);
    }
  }

  setFetchStatus(`抓取完成，共 ${lines.filter((line) => /^\d/.test(line)).length} 場。`, "success");
  return `${lines.join("\n")}\n`;
}

function buildMatchKey(match) {
  return `${match.court}|${match.matchNumber}|${match.description}`;
}

function parseMatches(text, savedTimestamps = {}) {
  const lines = text.split(/\r?\n/);
  const matches = [];
  let currentCourt = "";

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (/^第.+場地$/.test(line)) {
      currentCourt = line;
      continue;
    }

    const withTimestamp = line.match(/^(\d{2}:\d{2}:\d{2})\s+(.+)$/);
    const timestamp = withTimestamp ? withTimestamp[1] : "";
    const content = withTimestamp ? withTimestamp[2] : line;
    const parts = content.split(/\s+/);
    const matchNumber = parts.shift() || "";
    const description = content.slice(matchNumber.length).trim();
    const detailParts = description.split(" | ");

    const match = {
      court: currentCourt,
      matchNumber,
      description,
      players: detailParts[0] || description,
      shortMeta: detailParts.slice(1).join(" | "),
      timestamp: timestamp || savedTimestamps[`${currentCourt}|${matchNumber}|${description}`] || "",
    };
    matches.push(match);
  }

  return matches;
}

function renderCurrentMatch() {
  const match = state.matches[state.selectedIndex];
  if (!match) {
    elements.currentMatchCard.classList.add("empty");
    elements.currentMatchCard.textContent = "尚未載入場次";
    return;
  }

  elements.currentMatchCard.classList.remove("empty");
  elements.currentMatchCard.innerHTML = `
    <div><strong>${match.court}</strong></div>
    <div><strong>${match.matchNumber}</strong> ${match.players}</div>
    <div>${match.shortMeta || ""}</div>
    <div class="timestamp ${match.timestamp ? "" : "pending"}">目前記錄：${match.timestamp || "尚未標記"}</div>
  `;
}

function renderMatchList() {
  const items = state.matches.map((match, index) => {
    const isActive = index === state.selectedIndex;
    const item = document.createElement("button");
    item.type = "button";
    item.className = `match-item ${isActive ? "active" : ""} ${match.timestamp ? "recorded" : "pending"}`;
    item.innerHTML = `
      <div class="match-item-header">
        <span>${match.matchNumber}</span>
        <span class="timestamp ${match.timestamp ? "" : "pending"}">${match.timestamp || "未標記"}</span>
      </div>
      <div>${match.players}</div>
      <div class="match-meta">${match.court} | ${match.shortMeta || ""}</div>
    `;
    item.addEventListener("click", () => {
      state.selectedIndex = index;
      updateUi();
      saveState();
    });
    return item;
  });

  elements.matchList.replaceChildren(...items);
}

function buildOutputText() {
  const grouped = new Map();
  for (const match of state.matches) {
    if (!grouped.has(match.court)) grouped.set(match.court, []);
    grouped.get(match.court).push(match);
  }

  const lines = [];
  for (const [court, matches] of grouped.entries()) {
    if (lines.length) lines.push("");
    lines.push(court);
    for (const match of matches) {
      const prefix = match.timestamp ? `${match.timestamp} ` : "";
      lines.push(`${prefix}${match.matchNumber} ${match.description}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function updateStats() {
  const recorded = state.matches.filter((match) => match.timestamp).length;
  const total = state.matches.length;
  elements.recordedCount.textContent = String(recorded);
  elements.totalCount.textContent = String(total);
  elements.remainingCount.textContent = String(total - recorded);
}

function updateUi() {
  elements.currentTime.textContent = formatTime(state.lastKnownTime);
  renderCurrentMatch();
  renderMatchList();
  updateStats();
  elements.outputText.value = buildOutputText();
}

function saveState() {
  const timestamps = {};
  for (const match of state.matches) {
    if (match.timestamp) timestamps[buildMatchKey(match)] = match.timestamp;
  }
  const payload = {
    videoInput: elements.youtubeUrl.value.trim(),
    eventInput: elements.eventUrl.value.trim(),
    sourceText: elements.sourceText.value,
    selectedIndex: state.selectedIndex,
    timestamps,
    autoNext: elements.autoNext.checked,
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function restoreState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch (_error) {
    return null;
  }
}

function loadMatchesFromText() {
  const sourceText = elements.sourceText.value.trim();
  const restored = restoreState();
  const savedTimestamps = restored?.timestamps || {};
  state.sourceText = sourceText;
  state.matches = parseMatches(sourceText, savedTimestamps);
  state.selectedIndex = Math.min(restored?.selectedIndex || 0, Math.max(state.matches.length - 1, 0));
  updateUi();
  saveState();
}

function selectRelative(step) {
  if (!state.matches.length) return;
  state.selectedIndex = Math.max(0, Math.min(state.matches.length - 1, state.selectedIndex + step));
  updateUi();
  saveState();
}

function markCurrentMatch() {
  if (!state.matches.length) return;
  const match = state.matches[state.selectedIndex];
  match.timestamp = formatTime(state.lastKnownTime);
  updateUi();
  saveState();
  if (elements.autoNext.checked) {
    selectRelative(1);
  }
}

function clearCurrentMatch() {
  if (!state.matches.length) return;
  state.matches[state.selectedIndex].timestamp = "";
  updateUi();
  saveState();
}

function copyOutput() {
  navigator.clipboard.writeText(elements.outputText.value).then(() => {
    elements.copyOutputBtn.textContent = "已複製";
    setTimeout(() => {
      elements.copyOutputBtn.textContent = "複製結果";
    }, 1200);
  });
}

function downloadOutput() {
  const blob = new Blob([elements.outputText.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "TKDFightStamp-timestamps.txt";
  anchor.click();
  URL.revokeObjectURL(url);
}

function seekBy(delta) {
  if (!state.playerReady || !state.player) return;
  const next = Math.max(0, state.player.getCurrentTime() + delta);
  state.player.seekTo(next, true);
  state.lastKnownTime = next;
  updateUi();
}

function togglePlay() {
  if (!state.playerReady || !state.player) return;
  const status = state.player.getPlayerState();
  if (status === YT.PlayerState.PLAYING) {
    state.player.pauseVideo();
  } else {
    state.player.playVideo();
  }
}

function createOrLoadPlayer(videoId) {
  if (!videoId) {
    window.alert("請輸入有效的 YouTube 影片網址或影片 ID。");
    return;
  }

  if (!window.YT || !window.YT.Player) {
    window.alert("YouTube 播放器仍在載入，請稍候再試一次。");
    return;
  }

  state.videoInput = elements.youtubeUrl.value.trim();

  if (state.player) {
    state.player.loadVideoById(videoId);
    saveState();
    return;
  }

  state.player = new YT.Player("player-frame", {
    videoId,
    playerVars: {
      rel: 0,
      modestbranding: 1,
    },
    events: {
      onReady: () => {
        state.playerReady = true;
      },
      onStateChange: () => {
        if (state.player && state.player.getCurrentTime) {
          state.lastKnownTime = state.player.getCurrentTime();
          updateUi();
        }
      },
    },
  });
  saveState();
}

window.onYouTubeIframeAPIReady = function onYouTubeIframeAPIReady() {
  const restored = restoreState();
  if (restored?.videoInput) {
    elements.youtubeUrl.value = restored.videoInput;
    const videoId = parseVideoId(restored.videoInput);
    if (videoId) createOrLoadPlayer(videoId);
  }
};

async function bootstrapSourceText() {
  const restored = restoreState();
  if (restored?.sourceText) {
    elements.sourceText.value = restored.sourceText;
    elements.eventUrl.value = restored.eventInput || "";
    elements.autoNext.checked = restored.autoNext !== false;
    loadMatchesFromText();
    return;
  }

  try {
    const response = await fetch(DEFAULT_SOURCE_FILE);
    if (!response.ok) throw new Error("fetch failed");
    const text = await response.text();
    elements.sourceText.value = text;
    loadMatchesFromText();
  } catch (_error) {
    elements.sourceText.value = "";
    updateUi();
  }
}

function handleKeyboard(event) {
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;

  if (event.code === "Space") {
    event.preventDefault();
    togglePlay();
  }
  if (event.key === "n" || event.key === "N") {
    event.preventDefault();
    markCurrentMatch();
  }
  if (event.key === "p" || event.key === "P") {
    event.preventDefault();
    selectRelative(-1);
  }
  if (event.key === "j" || event.key === "J") {
    event.preventDefault();
    seekBy(-SEEK_SECONDS);
  }
  if (event.key === "l" || event.key === "L") {
    event.preventDefault();
    seekBy(SEEK_SECONDS);
  }
}

elements.loadVideoBtn.addEventListener("click", () => {
  createOrLoadPlayer(parseVideoId(elements.youtubeUrl.value));
});
elements.generateFromEventBtn.addEventListener("click", async () => {
  const eventId = parseEventId(elements.eventUrl.value);
  if (!eventId) {
    setFetchStatus("請輸入有效的賽事網址，例如 https://wego-tkd-web.onrender.com/event/3", "error");
    window.alert("請輸入有效的賽事網址，例如 https://wego-tkd-web.onrender.com/event/3");
    return;
  }

  const originalLabel = elements.generateFromEventBtn.textContent;
  elements.generateFromEventBtn.disabled = true;
  elements.generateFromEventBtn.textContent = "抓取中...";
  try {
    const sourceText = await generateSourceTextFromEvent(eventId);
    elements.sourceText.value = sourceText;
    loadMatchesFromText();
  } catch (error) {
    console.error(error);
    const message = describeError(error);
    setFetchStatus(`抓取失敗：${message}`, "error");
    window.alert(`抓取對戰表失敗\n\n${message}`);
  } finally {
    elements.generateFromEventBtn.disabled = false;
    elements.generateFromEventBtn.textContent = originalLabel;
    saveState();
  }
});
elements.back5Btn.addEventListener("click", () => seekBy(-SEEK_SECONDS));
elements.forward5Btn.addEventListener("click", () => seekBy(SEEK_SECONDS));
elements.togglePlayBtn.addEventListener("click", togglePlay);
elements.loadMatchesBtn.addEventListener("click", loadMatchesFromText);
elements.resetProgressBtn.addEventListener("click", () => {
  if (!window.confirm("確定要清空所有已記錄的時間戳嗎？")) return;
  state.matches = state.matches.map((match) => ({ ...match, timestamp: "" }));
  state.selectedIndex = 0;
  updateUi();
  saveState();
});
elements.prevBtn.addEventListener("click", () => selectRelative(-1));
elements.markBtn.addEventListener("click", markCurrentMatch);
elements.clearBtn.addEventListener("click", clearCurrentMatch);
elements.nextBtn.addEventListener("click", () => selectRelative(1));
elements.copyOutputBtn.addEventListener("click", copyOutput);
elements.downloadOutputBtn.addEventListener("click", downloadOutput);
elements.sourceText.addEventListener("change", saveState);
elements.eventUrl.addEventListener("change", saveState);
elements.youtubeUrl.addEventListener("change", saveState);
elements.autoNext.addEventListener("change", saveState);
document.addEventListener("keydown", handleKeyboard);

setInterval(() => {
  if (!state.playerReady || !state.player || !state.player.getCurrentTime) return;
  state.lastKnownTime = state.player.getCurrentTime();
  elements.currentTime.textContent = formatTime(state.lastKnownTime);
}, 300);

bootstrapSourceText();
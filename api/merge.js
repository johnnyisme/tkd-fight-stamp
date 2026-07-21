// Vercel serverless:接「時間戳+場次清單 + 賽事」→ 按場次號比對對戰表 → 補齊每行。
// 移植自本機版 serve.py 的 merge 邏輯(wego + tondar),讓純前端(Vercel)也能匹配對戰。
//
// POST body:
//   { source:"wego",   text, event_id }                → wego-tkd-web.onrender.com
//   { source:"tondar", text, event_no, edte }          → tondar-cn.com(edte 民國日期)
// 回:{ ok, result }  result = 每行「時間戳 場次 選手 vs 選手 (勝) | 分組 | 賽別」

const WEGO_BASE = "https://wego-tkd-web.onrender.com";
const TONDAR_BASE = "https://www.tondar-cn.com/Competition";
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36";

const TEAM_PATTERNS = [
  /^(?:臺|台)北市立/, /^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄|嘉義)市立/,
  /^(?:臺|台)北縣立/, /^新竹縣立/, /^屏東縣立/, /^宜蘭縣立/, /^花蓮縣立/, /^(?:台|臺)東縣立/,
  /^苗栗縣立/, /^彰化縣立/, /^南投縣立/, /^雲林縣立/, /^嘉義縣立/,
  /^(?:新北|臺北|台北|新竹|基隆|台中|臺中|桃園|臺南|台南|高雄)市/, /^(?:臺|台)北縣/,
  /^基隆市/, /^新竹縣/, /^屏東縣/, /^宜蘭縣/, /^花蓮縣/, /^台東縣/, /^臺東縣/,
  /^苗栗縣/, /^彰化縣/, /^南投縣/, /^雲林縣/, /^嘉義縣/, /^嘉義市/,
];

function simplifyTeam(team) {
  let t = String(team || "").trim();
  for (const p of TEAM_PATTERNS) t = t.replace(p, "");
  return t.trim() || String(team || "").trim();
}

// 分組簡化:青少年/國中→(國)、青年/高中/社會→(社高);-44公斤級→44KG、+59→59KG+
function simplifyCategory(name) {
  const t = String(name || "").trim();
  let level = "";
  if (t.includes("青少年") || t.includes("國中")) level = "(國)";
  else if (t.includes("青年") || t.includes("高中") || t.includes("社會")) level = "(社高)";
  const gender = t.includes("女") ? "女子" : (t.includes("男") ? "男子" : "");
  const m = t.match(/([+\-]?)(\d+)\s*(?:公斤級|KG)(以上)?/i);
  let weight = "";
  if (m) weight = m[2] + "KG" + (m[1] === "+" || m[3] ? "+" : "");
  return (gender && weight) ? `${level}${gender}${weight}` : t;
}

// 解析複審清單每行:時間戳(可帶 ~ 粗估標記) + 場次號
function parseLines(text) {
  const rows = [];
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("時間戳")) continue;
    const parts = line.split(/[\s\t]+/);
    const ts = (parts[0] || "").replace(/~+$/, ""); // 去 approx 標記
    const num = (parts[1] || "").trim();
    rows.push({ ts, num });
  }
  return rows;
}

async function fetchJson(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} HTTP ${r.status}`);
  return r.json();
}

// ---- wego ----
async function wegoIndex(eventId) {
  const cats = await fetchJson(`${WEGO_BASE}/api/public/events/${eventId}/categories`,
    { headers: { "User-Agent": UA } });
  const idx = {};
  for (const cat of cats) {
    let sch;
    try {
      sch = await fetchJson(`${WEGO_BASE}/api/public/events/${eventId}/categories/${cat.id}/schedule`,
        { headers: { "User-Agent": UA } });
    } catch (_e) { continue; }
    for (const m of (sch.data || [])) {
      const num = String(m.matchnumber || "").trim();
      if (num) { m._category = cat.name; idx[num] = m; }
    }
  }
  return idx;
}

function wegoLine(ts, num, m) {
  if (!m) return `${ts} ${num} (對戰表查無此場次)`;
  let p1 = `${simplifyTeam(m.p1_team)} ${m.p1_display || ""}`.trim();
  let p2 = `${simplifyTeam(m.p2_team)} ${m.p2_display || ""}`.trim();
  if (m.winner_id && m.winner_id === m.player1_id) p1 += " (勝)";
  else if (m.winner_id && m.winner_id === m.player2_id) p2 += " (勝)";
  return `${ts} ${num} ${p1} vs ${p2} | ${simplifyCategory(m._category)} | ${m.round || ""}`;
}

// ---- tondar ----
const TONDAR_SYSTEM = { R64: "64強", R32: "32強", R16: "16強", R8: "8強", 四強賽: "4強", 冠亞軍: "冠亞軍", 敗部: "敗部復活" };

async function tondarPost(endpoint, params, eventNo) {
  const r = await fetch(`${TONDAR_BASE}/${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
      Referer: `${TONDAR_BASE}/ScheduleC.php?EventNo=${eventNo}`,
    },
    body: new URLSearchParams(params),
  });
  if (!r.ok) throw new Error(`${endpoint} HTTP ${r.status}`);
  return r.json();
}

async function tondarIndex(eventNo, edte) {
  const idx = {};
  for (let court = 1; court <= 5; court += 1) {
    let rows;
    try {
      rows = await tondarPost("Return_ScheduleC.php", { EventNo: eventNo, EDte: edte, ECourt: court }, eventNo);
    } catch (_e) { continue; }
    for (const m of (rows || [])) {
      const num = String(m.Match || "").trim();
      if (num) idx[num] = m;
    }
  }
  return idx;
}

function tondarCategory(grade, weight) {
  const g = String(grade || "");
  const level = g.includes("青少年") ? "(國)" : (g.includes("青年") ? "(社高)" : "");
  const gender = g.includes("女") ? "女子" : (g.includes("男") ? "男子" : "");
  const m = String(weight || "").match(/([+\-]?)(\d+)公斤級/);
  const wt = m ? m[2] + "KG" + (m[1] === "+" ? "+" : "") : String(weight || "");
  return `${level}${gender}${wt}`.trim();
}

function tondarLine(ts, num, m) {
  if (!m) return `${ts} ${num} (對戰表查無此場次)`;
  let blue = `${simplifyTeam(m.Blue_Dptname)} ${m.Blue || ""}`.trim();
  let red = `${simplifyTeam(m.Red_Dptname)} ${m.Red || ""}`.trim();
  if (m.Win === "B") blue += " (勝)";
  else if (m.Win === "R") red += " (勝)";
  const cat = tondarCategory(m.EGrade, m.EWeight);
  const rnd = TONDAR_SYSTEM[m.ESystem] || m.ESystem || "";
  return `${ts} ${num} ${blue} vs ${red} | ${cat} | ${rnd}`;
}

module.exports = async (req, res) => {
  if (req.method !== "POST") { res.status(405).json({ ok: false, error: "Method Not Allowed" }); return; }
  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch (_e) { body = {}; } }
  const source = String(body.source || "wego").trim();
  const rows = parseLines(body.text);
  if (!rows.length) { res.status(400).json({ ok: false, error: "清單是空的" }); return; }

  try {
    let out;
    if (source === "tondar") {
      const eventNo = String(body.event_no || "").trim();
      const edte = String(body.edte || "").trim();
      if (!eventNo || !edte) { res.status(400).json({ ok: false, error: "tondar 需要 event_no 與 edte" }); return; }
      const idx = await tondarIndex(eventNo, edte);
      out = rows.map((r) => tondarLine(r.ts, r.num, r.num ? idx[r.num] : null));
    } else {
      const eventId = String(body.event_id || "").trim();
      if (!eventId) { res.status(400).json({ ok: false, error: "wego 需要 event_id" }); return; }
      const idx = await wegoIndex(eventId);
      out = rows.map((r) => wegoLine(r.ts, r.num, r.num ? idx[r.num] : null));
    }
    res.status(200).json({ ok: true, result: out.join("\n") });
  } catch (e) {
    res.status(502).json({ ok: false, error: e instanceof Error ? e.message : String(e) });
  }
};

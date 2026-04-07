const TONDAR_BASE_URL = "https://www.tondar-cn.com/Competition";
const USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function simplifyTeamName(team) {
  let cleaned = String(team || "").trim();
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
  return cleaned.trim() || String(team || "").trim();
}

function formatCompetitor(team, name, isWinner) {
  const competitor = [simplifyTeamName(team), String(name || "").trim()].filter(Boolean).join(" ");
  return isWinner ? `${competitor} (勝)` : competitor || "資料缺漏";
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

function buildCourtHeading(dateValue, courtValue, includeDatePrefix) {
  const heading = `第${courtValue}場地`;
  return includeDatePrefix ? `${dateValue} ${heading}` : heading;
}

async function postJson(endpoint, payload, attempts = 5) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(`${TONDAR_BASE_URL}/${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "User-Agent": USER_AGENT,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: new URLSearchParams(payload),
      });

      if (!response.ok) {
        const body = await response.text().catch(() => "");
        throw new Error(`${endpoint} 回傳 HTTP ${response.status}${body ? ` | ${body.slice(0, 160)}` : ""}`);
      }

      return await response.json();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await sleep(Math.min(1200 * (2 ** (attempt - 1)), 10000));
      }
    }
  }
  throw lastError;
}

module.exports = async (req, res) => {
  if (req.method !== "GET") {
    res.status(405).json({ error: "Method Not Allowed" });
    return;
  }

  const eventNo = String(req.query.eventNo || "").trim();
  const dateFilter = String(req.query.date || "").trim();
  const mode = String(req.query.mode || "schedule").trim();
  if (!eventNo) {
    res.status(400).json({ error: "Missing eventNo" });
    return;
  }

  try {
    const dates = await postJson("Return_KyoDte.php", { EventNo: eventNo });
    const normalizedDates = dates.map((item) => String(item.Value || item.Text || "").trim()).filter(Boolean);

    if (mode === "dates") {
      res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
      res.status(200).json({
        source: "tondar",
        eventNo,
        dates: normalizedDates,
      });
      return;
    }

    const selectedDates = dateFilter
      ? dates.filter((item) => String(item.Value) === dateFilter)
      : dates;

    if (!selectedDates.length) {
      res.status(404).json({ error: `No schedule date found for event ${eventNo}${dateFilter ? ` and date ${dateFilter}` : ""}` });
      return;
    }

    const lines = [];
    const includeDatePrefix = selectedDates.length > 1;

    for (const dateItem of selectedDates) {
      const dateValue = String(dateItem.Value || dateItem.Text || "").trim();
      const courts = await postJson("Return_Court.php", {
        EventNo: eventNo,
        EDte: dateValue,
      });

      for (const court of courts) {
        const courtValue = String(court.Text || court.Value || "").trim();
        if (!courtValue) continue;

        const scheduleRows = await postJson("Return_ScheduleC.php", {
          EventNo: eventNo,
          EDte: dateValue,
          ECourt: courtValue,
        });

        if (!Array.isArray(scheduleRows) || !scheduleRows.length) {
          await sleep(500);
          continue;
        }

        if (lines.length) lines.push("");
        lines.push(buildCourtHeading(dateValue, courtValue, includeDatePrefix));

        scheduleRows
          .map((row) => ({
            matchNumber: String(row.Match || "").trim(),
            sortKey: matchSortKey(row.Match || ""),
            left: formatCompetitor(row.Blue_Dptname, row.Blue, row.Win === "B"),
            right: formatCompetitor(row.Red_Dptname, row.Red, row.Win === "R"),
            categoryName: `${String(row.EGrade || "").trim()}${String(row.EWeight || "").trim()}`,
            round: String(row.ESystem || "").trim(),
          }))
          .filter((row) => row.matchNumber)
          .sort((left, right) => compareMatchSortKey(left.sortKey, right.sortKey))
          .forEach((row) => {
            lines.push(`${row.matchNumber} ${row.left} vs ${row.right} | ${row.categoryName} | ${row.round}`);
          });

        await sleep(700);
      }
    }

    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=300");
    res.status(200).json({
      source: "tondar",
      eventNo,
      date: dateFilter || null,
      sourceText: `${lines.join("\n")}\n`,
      matchCount: lines.filter((line) => /^\d/.test(line)).length,
      dates: selectedDates.map((item) => String(item.Value || item.Text || "").trim()),
    });
  } catch (error) {
    res.status(502).json({ error: error instanceof Error ? error.message : String(error) });
  }
};
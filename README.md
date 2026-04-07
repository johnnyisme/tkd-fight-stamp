# TKDFightStamp

TKDFightStamp 是一個以靜態頁面為主、搭配少量 Vercel API 代理的工具，讓你一邊看 YouTube 影片、一邊逐場記錄跆拳道對戰時間戳，最後輸出成可貼回場次表或 YouTube 介紹欄的文字。

## 使用方式

1. 在這個資料夾啟動本機靜態伺服器
2. 用瀏覽器打開工具頁面
3. 貼上賽事網址，自動抓取對戰表，或直接貼上既有場次表
4. 貼上 YouTube 影片網址
5. 播到某場「兩邊選手準備對戰前」時，按「記錄目前時間」或快捷鍵 `N`
6. 工具會把時間戳寫到該場前面，並可自動跳到下一場

## 啟動方式

在此資料夾執行：

```bash
python3 -m http.server 8000
```

然後打開：

```text
http://localhost:8000
```

## GitHub 與 Vercel

建議名稱：

- GitHub repo: `tkd-fight-stamp`
- Vercel project: `tkd-fight-stamp`

這個工具前端是靜態頁面；若要支援全中運 `tondar-cn.com` 的賽程抓取，需要一起部署 Vercel API 代理。

### 部署方式

1. 把這個資料夾推到 GitHub repo `tkd-fight-stamp`
2. 在 Vercel 匯入該 repo
3. Framework Preset 選 `Other`
4. Root Directory 指到本專案資料夾
5. 直接部署

如果使用本資料夾內的 [vercel.json](vercel.json)，首頁會直接指向 `index.html`。

## 快捷鍵

- `N`: 記錄目前場次時間，並依設定自動跳下一場
- `P`: 跳到上一場
- `J`: 倒退 60 秒
- `L`: 快進 60 秒
- `Space`: 播放或暫停

## 自動抓取對戰表

可以直接貼上賽事網址，例如：

```text
https://wego-tkd-web.onrender.com/event/3
https://www.tondar-cn.com/Competition/ScheduleC.php?EventNo=10
```

然後按下「抓取對戰表」。

如果是全中運網站，可直接從下拉選單選競賽日期，例如：

```text
1150329
```

保留「全部日期」時會抓該賽事所有日期。

工具會自動：

1. 抓取所有量級賽程
2. 依場地與場次排序
3. 產出整理好的場次表
4. 直接載入右側對戰清單

### 全中運站台注意事項

- 全中運網站的 PHP 查詢端點沒有對外開放前端跨網域存取
- 因此本專案是透過 [api/tondar-schedule.js](api/tondar-schedule.js) 代理抓資料
- 如果你在本機只用 `python3 -m http.server 8000` 啟動，這個代理不會存在
- 本機若要測全中運抓取，請改用 `vercel dev`，或直接測部署後網址

## 匯出結果

右側匯出區會自動更新，例如：

```text
00:12:31 101 弘道國中 郭采婕 (勝) vs 士東道館 曾一 | 女子42KG | 八強賽
00:16:48 102 弘道國中 林品妤 (勝) vs 龍門國中 廖予荷 | 女子42KG | 八強賽
```

可直接：

- 複製到剪貼簿
- 下載成 TXT

預設下載檔名：`TKDFightStamp-timestamps.txt`

## 注意

- 這是人工判斷開始點、工具幫你記錄時間的半自動方式
- 工具會把進度存在瀏覽器 `localStorage`
- 若你修改原始場次表後重新載入，已記錄的時間戳會盡量依場地 + 場次 + 描述對回去
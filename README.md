# 洗評論過濾 Agent (Fake Review Filter Agent)

兩種入口：① 在台北捷運示意圖上選一站，抓附近美食、依評分先篩一輪；② 直接輸入商家名稱。
選定餐廳後透過 **SerpAPI** 抓 Google Maps 評論 → 逐則交給 **OpenAI LLM** 判斷是否為真實評論 /
可疑 / 疑似洗評（灌水、業配、樣板化好評或惡意攻擊），並提供可篩選的結果頁面與整體「可信度分數」。
同一間店分析過一次後結果會快取，下次選到同一間不會重複呼叫 SerpAPI/LLM。

## 架構

```
food_agent/
├── config.py             # 讀取 .env 設定
├── mrt_data.py           # 台北捷運各線車站靜態資料（示意圖用）
├── cache_store.py        # JSON 檔案快取：捷運站座標、餐廳分析結果
├── serpapi_client.py     # SerpAPI: 搜商家/抓評論/解析捷運站座標/搜附近美食
├── review_classifier.py  # OpenAI: 批次分類每則評論 genuine / suspicious / fake
├── pipeline.py           # 串接 搜尋 -> (快取檢查) -> 抓評論 -> 分類 -> 彙總，並回報進度
├── web_server.py         # FastAPI: REST + WebSocket + /api/mrt/* 端點
├── static/index.html     # 前端頁面（純 HTML+CSS+JS，無需建置）
├── requirements.txt
└── .env.example
```

判斷完全基於「評論文字本身與星等」，不使用評論者歷史或跨評論群體訊號（可日後擴充）。

## 捷運找美食流程

1. `GET /api/mrt/lines` 回傳六條線（淡水信義/松山新店/板南/中和新蘆/文湖/環狀）的車站示意圖資料。
2. 使用者選一站 → `GET /api/mrt/nearby?station=大安&min_rating=4.5&limit=10`：
   - 後端把站名解析成經緯度（`serpapi_client.resolve_station_coordinates`，結果快取在
     `data/station_coords_cache.json`，同一站不會重複查）
   - 用該座標搜附近餐廳（`engine=google_maps`），依 `min_rating` 過濾、依評分排序，只回傳前 N 間
3. 附近餐廳清單一出來就自動全部分析（不用手動點），透過 `WS /ws/analyze_batch` 逐間跑
   `analyze_business`（`BUSINESS_CONCURRENCY` 控制同時分析幾間，預設 2），每間卡片會即時顯示
   「抓取中/分類中 X/Y/可信度 N%」，全部跑完後彙整成一份報告（依可信度排序，附平均可信度與
   疑似洗評論店家數，每列可展開看該店的完整評論列表）
4. 分析結果依 `data_id` 存進 `data/analysis_result_cache.json`；下次選到同一間店會直接命中快取
   （手動搜尋商家名稱的單店分析畫面則會顯示「重新分析（略過快取）」按鈕可強制重跑）

## 快速開始

1. 安裝套件：
   ```bash
   pip install -r requirements.txt
   ```

2. 設定金鑰：複製 `.env.example` 為 `.env`，填入
   - `SERP_API_KEY`：https://serpapi.com/manage-api-key
   - `OPENAI_API_KEY`

3. 啟動伺服器：
   ```bash
   python web_server.py
   ```
   開啟 http://localhost:8000，輸入商家名稱（例如「鼎泰豐 信義店」）即可分析。

## API

- `POST /api/analyze` `{"query": "商家名稱", "max_reviews": 60}` → 一次性回傳完整結果（不含進度）。
- `WS /ws/analyze` → 送出同樣的 JSON，會即時收到 `{"stage": ..., "message": ...}` 進度事件，
  最後收到 `{"stage": "result", "data": {...}}`。
- 若已知 Google Maps 的 `data_id`，可直接帶入 `data_id` 欄位略過商家搜尋步驟。
- `WS /ws/analyze_batch` `{"places": [{"title":..., "data_id":...}, ...], "max_reviews": 60}` →
  逐間分析多間餐廳，過程中收到的事件多帶 `business_index`/`business_title`（對應輸入的
  `places` 順序），每間分析完會收到一次 `{"stage": "business_done", "done":..., "total":...}`，
  全部跑完收到 `{"stage": "batch_result", "data": {報告}}`。

## 回傳結果格式

```json
{
  "business": {"data_id": "...", "title": "...", "address": "...", "rating": 4.5, "reviews_count": 1234},
  "reviews": [
    {"review_id": "...", "author": "...", "rating": 5, "date": "...", "text": "...",
     "verdict": "genuine|suspicious|fake|unknown", "confidence": 0.0-1.0, "reason": "..."}
  ],
  "summary": {"total": 60, "genuine": 45, "suspicious": 10, "fake": 5, "unknown": 0, "trust_score": 0.75}
}
```

`trust_score` = genuine / (total - unknown)，忽略分類失敗的評論。

`/ws/analyze_batch` 的 `batch_result.data` 結構：

```json
{
  "restaurants": [
    {"data_id": "...", "title": "...", "address": "...", "google_rating": 4.7,
     "summary": {...同上...}, "reviews": [...同上...]}
  ],
  "overview": {"total_restaurants": 8, "avg_trust_score": 0.71, "flagged_count": 2, "flagged_titles": [...]}
}
```
`restaurants` 依 `trust_score` 由高到低排序；`flagged_count` 是 `trust_score < 0.6` 的店家數。

## 已知限制 / 後續可擴充方向

- 目前每則評論的判斷只看文字與星等，未使用評論者發文歷史、短時間內大量相似評論等群體訊號。
- SerpAPI 免費額度有限，抓取大量評論（`max_reviews` 調高）會消耗較多 API 額度且較慢。
- 分類模型預設為 `gpt-5.2`（見 `.env` 的 `CLASSIFIER_MODEL`），可視精準度/成本需求更換。

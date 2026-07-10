---
name: verify-env
description: Use this skill whenever you need to actually run/verify backend code in this food_agent project — starting the FastAPI server, checking imports or syntax, hitting REST/WebSocket endpoints. This project's Python environment is a plain native Windows venv at f:\food_agent\.venv (created with `python -m venv .venv`, no WSL, no conda). Triggers on: "run the server", "test the endpoint", "check it imports", "verify this works", "start uvicorn", "curl the API", or any request to actually execute Python/FastAPI in this repo rather than just read/edit code.
---

# 本機驗證環境（原生 Windows venv）

Claude Code 跑在 **Windows（Git Bash）**，這個專案的 Python 執行環境**就在同一台機器上**，
是一個用標準 `venv` 建立的環境，位於 `f:\food_agent\.venv`。不需要 WSL、不需要 conda，
直接 `source .venv/Scripts/activate` 即可，Git Bash 也不會有路徑轉換問題。

## 關鍵路徑

| 項目 | 值 |
|---|---|
| Python 版本 | 3.10.2（系統內建，`python --version`） |
| venv 位置 | `f:\food_agent\.venv`（Git Bash 下用 `.venv/Scripts/activate`，不是 `.venv/bin/activate`） |
| 專案路徑 | `f:\food_agent`（Git Bash 下對應 `/f/food_agent`） |

## 常用指令範本

### 啟用環境 + 檢查套件是否已安裝
```bash
source .venv/Scripts/activate && python -c "import fastapi, openai, requests; print('ok')"
```

### import 檢查（含 .env 讀取邏輯）
```bash
source .venv/Scripts/activate && python -c "import config, serpapi_client, review_classifier, pipeline, web_server; print('OK'); print('missing keys:', config.missing_keys())"
```

### 背景啟動 FastAPI 服務做端對端測試
```bash
source .venv/Scripts/activate && (uvicorn web_server:app --host 127.0.0.1 --port 8000 > /tmp/server.log 2>&1 &) && sleep 3 && curl -s http://127.0.0.1:8000/api/health
```

### 收尾：關掉背景服務
Git Bash 起的背景 uvicorn 找不到簡單的 `kill`，要用 PowerShell 兩步驟：
```bash
netstat -ano | grep ":8000"          # 找 LISTENING 那行最後的 PID
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
```

### 安裝/更新套件
```bash
source .venv/Scripts/activate && pip install -r requirements.txt
```

## 其他已知狀況
- `SERP_API_KEY` / `OPENAI_API_KEY` 若未在 `.env` 設定，`config.missing_keys()` 會回報缺哪個，
  `/api/analyze` 與 `/ws/analyze` 會回傳清楚的 400 / error 事件而不是 crash，這是預期行為，
  不代表程式有問題。
- 沒有前端建置流程（`static/index.html` 是純 HTML+CSS+JS），不需要 npm / Node。

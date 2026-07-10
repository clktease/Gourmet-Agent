"""
FastAPI web server for the fake-review filter agent.

  GET  /                  -> static frontend
  POST /api/analyze       -> one-shot analysis (no progress streaming)
  WS   /ws/analyze         -> analysis with live progress events

Usage:
    python web_server.py
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import mrt_data
import serpapi_client
from pipeline import analyze_business, analyze_multiple
from serpapi_client import SerpApiError

app = FastAPI(title="Fake Review Filter Agent")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AnalyzeRequest(BaseModel):
    query: str
    max_reviews: int = config.MAX_REVIEWS_DEFAULT
    data_id: str | None = None
    force_refresh: bool = False


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "missing_env": config.missing_keys()}


@app.post("/api/analyze")
async def api_analyze(req: AnalyzeRequest):
    missing = config.missing_keys()
    if missing:
        return JSONResponse({"error": f"Missing env vars: {', '.join(missing)}"}, status_code=400)
    try:
        result = await analyze_business(
            req.query, req.max_reviews, req.data_id, force_refresh=req.force_refresh
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return result


@app.get("/api/mrt/lines")
async def mrt_lines():
    return {"lines": mrt_data.LINES}


@app.get("/api/mrt/nearby")
async def mrt_nearby(
    station: str,
    min_rating: float = 0.0,
    max_rating: float = 5.0,
    limit: int = 10,
    keyword: str = "美食",
):
    missing = config.missing_keys()
    if missing:
        return JSONResponse({"error": f"Missing env vars: {', '.join(missing)}"}, status_code=400)
    try:
        coords = await asyncio.to_thread(serpapi_client.resolve_station_coordinates, station)
        places = await asyncio.to_thread(
            serpapi_client.search_nearby_food, coords["lat"], coords["lng"], keyword
        )
    except SerpApiError as e:
        return JSONResponse({"error": str(e)}, status_code=502)

    filtered = [
        p for p in places
        if min_rating <= (p.get("rating") or 0) <= max_rating
    ]
    filtered.sort(key=lambda p: (p.get("rating") or 0, p.get("reviews_count") or 0), reverse=True)

    return {"station": station, "coords": coords, "places": filtered[:limit]}


@app.websocket("/ws/analyze")
async def ws_analyze(websocket: WebSocket):
    await websocket.accept()
    try:
        req = await websocket.receive_json()
        missing = config.missing_keys()
        if missing:
            await websocket.send_json({"stage": "error", "message": f"Missing env vars: {', '.join(missing)}"})
            return

        query = req.get("query", "")
        max_reviews = int(req.get("max_reviews") or config.MAX_REVIEWS_DEFAULT)
        data_id = req.get("data_id") or None
        force_refresh = bool(req.get("force_refresh", False))

        async def progress_cb(event: dict):
            await websocket.send_json(event)

        try:
            result = await analyze_business(
                query, max_reviews, data_id, progress_cb=progress_cb, force_refresh=force_refresh
            )
            await websocket.send_json({"stage": "result", "data": result})
        except Exception as e:
            await websocket.send_json({"stage": "error", "message": str(e)})
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/analyze_batch")
async def ws_analyze_batch(websocket: WebSocket):
    await websocket.accept()
    try:
        req = await websocket.receive_json()
        missing = config.missing_keys()
        if missing:
            await websocket.send_json({"stage": "error", "message": f"Missing env vars: {', '.join(missing)}"})
            return

        places = req.get("places") or []
        businesses = [{"title": p.get("title", ""), "data_id": p.get("data_id")} for p in places if p.get("data_id")]
        max_reviews = int(req.get("max_reviews") or config.MAX_REVIEWS_DEFAULT)

        if not businesses:
            await websocket.send_json({"stage": "error", "message": "沒有可分析的餐廳"})
            return

        async def progress_cb(event: dict):
            await websocket.send_json(event)

        try:
            report = await analyze_multiple(businesses, max_reviews, progress_cb=progress_cb)
            await websocket.send_json({"stage": "batch_result", "data": report})
        except Exception as e:
            await websocket.send_json({"stage": "error", "message": str(e)})
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=True)

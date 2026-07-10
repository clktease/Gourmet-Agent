"""
End-to-end pipeline: resolve business -> fetch Google reviews via SerpAPI ->
classify each review with the LLM -> aggregate into a filtered report.
"""

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import config
import serpapi_client
from cache_store import analysis_cache
from review_classifier import classify_reviews

ProgressCB = Optional[Callable[[dict], Awaitable[None]]]


async def _emit(progress_cb: ProgressCB, event: dict) -> None:
    if progress_cb:
        await progress_cb(event)


async def analyze_business(
    query: str,
    max_reviews: Optional[int] = None,
    data_id: Optional[str] = None,
    progress_cb: ProgressCB = None,
    force_refresh: bool = False,
) -> dict:
    max_reviews = max_reviews or config.MAX_REVIEWS_DEFAULT

    if data_id:
        business = {"data_id": data_id, "title": query}
    else:
        await _emit(progress_cb, {"stage": "search", "message": f"搜尋商家: {query}"})
        business = await asyncio.to_thread(serpapi_client.search_business, query)
        await _emit(
            progress_cb,
            {"stage": "search_done", "message": f"找到商家: {business['title']}", "business": business},
        )

    if not force_refresh:
        cached = analysis_cache.get(business["data_id"])
        if cached:
            await _emit(
                progress_cb,
                {
                    "stage": "cache_hit",
                    "message": f"使用先前的分析結果（{cached.get('cached_at', '')}）",
                    "business": cached["business"],
                },
            )
            await _emit(progress_cb, {"stage": "done", "message": "分析完成（快取）", "summary": cached["summary"]})
            return cached

    await _emit(progress_cb, {"stage": "fetch", "message": f"抓取評論 (最多 {max_reviews} 則)..."})
    reviews = await asyncio.to_thread(
        serpapi_client.fetch_reviews, business["data_id"], max_reviews
    )
    await _emit(
        progress_cb, {"stage": "fetch_done", "message": f"取得 {len(reviews)} 則評論", "total": len(reviews)}
    )

    if not reviews:
        result = {"business": business, "reviews": [], "summary": _empty_summary()}
        _save_to_cache(business, result)
        return result

    async def on_classify_progress(done: int, total: int):
        await _emit(
            progress_cb,
            {"stage": "classify", "message": f"分類中 {done}/{total}", "done": done, "total": total},
        )

    classified = await classify_reviews(reviews, progress_cb=on_classify_progress)

    summary = _summarize(classified)
    await _emit(progress_cb, {"stage": "done", "message": "分析完成", "summary": summary})

    result = {"business": business, "reviews": classified, "summary": summary}
    _save_to_cache(business, result)
    return result


def _save_to_cache(business: dict, result: dict) -> None:
    data_id = business.get("data_id")
    if not data_id:
        return
    cached_at = datetime.now(tz=timezone.utc).isoformat()
    analysis_cache.set(data_id, {**result, "cached_at": cached_at})


async def analyze_multiple(
    businesses: list[dict],
    max_reviews: Optional[int] = None,
    progress_cb: ProgressCB = None,
    business_concurrency: Optional[int] = None,
) -> dict:
    """
    Analyze several businesses (each {"title":..., "data_id":...}) and roll the
    results up into one report. Emits the same per-business progress events as
    analyze_business, tagged with business_index/business_title, plus
    "business_done" after each finishes and "report_done" with the final report.
    """
    business_concurrency = business_concurrency or config.BUSINESS_CONCURRENCY
    semaphore = asyncio.Semaphore(business_concurrency)
    results: list[Optional[dict]] = [None] * len(businesses)
    done_count = 0
    lock = asyncio.Lock()

    async def run_one(idx: int, biz: dict):
        nonlocal done_count

        async def biz_progress_cb(event: dict):
            await _emit(progress_cb, {**event, "business_index": idx, "business_title": biz.get("title")})

        async with semaphore:
            result = await analyze_business(
                biz.get("title", ""), max_reviews, biz.get("data_id"), progress_cb=biz_progress_cb
            )
        results[idx] = result

        async with lock:
            done_count += 1
            await _emit(
                progress_cb,
                {
                    "stage": "business_done",
                    "business_index": idx,
                    "business_title": biz.get("title"),
                    "done": done_count,
                    "total": len(businesses),
                },
            )

    await asyncio.gather(*(run_one(i, b) for i, b in enumerate(businesses)))

    report = _build_report(results)
    await _emit(progress_cb, {"stage": "report_done", "report": report})
    return report


def _build_report(results: list[dict]) -> dict:
    restaurants = []
    for r in results:
        business = r["business"]
        summary = r["summary"]
        restaurants.append(
            {
                "data_id": business.get("data_id"),
                "title": business.get("title"),
                "address": business.get("address"),
                "google_rating": business.get("rating"),
                "summary": summary,
                "reviews": r["reviews"],
            }
        )
    def _sort_key(x):
        score = x["summary"]["trust_score"]
        return (score is None, -(score if score is not None else 0))

    restaurants.sort(key=_sort_key)

    scored = [x["summary"]["trust_score"] for x in restaurants if x["summary"]["trust_score"] is not None]
    avg_trust = round(sum(scored) / len(scored), 3) if scored else None
    flagged = [
        x["title"] for x in restaurants
        if x["summary"]["trust_score"] is not None and x["summary"]["trust_score"] < 0.6
    ]

    return {
        "restaurants": restaurants,
        "overview": {
            "total_restaurants": len(restaurants),
            "avg_trust_score": avg_trust,
            "flagged_count": len(flagged),
            "flagged_titles": flagged,
        },
    }


def _empty_summary() -> dict:
    return {
        "total": 0,
        "genuine": 0,
        "suspicious": 0,
        "fake": 0,
        "unknown": 0,
        "trust_score": None,
    }


def _summarize(classified: list[dict]) -> dict:
    counts = {"genuine": 0, "suspicious": 0, "fake": 0, "unknown": 0}
    for r in classified:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    total = len(classified)
    judged = total - counts["unknown"]
    trust_score = round(counts["genuine"] / judged, 3) if judged else None

    return {
        "total": total,
        "genuine": counts["genuine"],
        "suspicious": counts["suspicious"],
        "fake": counts["fake"],
        "unknown": counts["unknown"],
        "trust_score": trust_score,
    }

"""
Thin wrapper around SerpAPI's Google Maps engines.

  - search_business(query)  -> resolve a free-text query to a single place
                                (data_id + basic metadata) via engine=google_maps
  - fetch_reviews(data_id)  -> paginate engine=google_maps_reviews until
                                max_reviews is hit or SerpAPI runs out of pages
"""

import threading
import time
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

import requests

import config
from cache_store import station_coords_cache

_BASE_URL = "https://serpapi.com/search.json"


class SerpApiError(RuntimeError):
    pass


# Requests run concurrently across threads (asyncio.to_thread), one set of
# pagination calls per restaurant being analyzed. Without a shared throttle,
# a batch of several restaurants fetching pages in parallel easily bursts
# past SerpAPI's rate limit and gets 429'd. This lock enforces a minimum gap
# between ANY two outgoing requests, no matter which thread sends them.
_rate_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    with _rate_lock:
        wait = _last_request_at + config.SERP_MIN_INTERVAL_SEC - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _get(params: dict) -> dict:
    if not config.SERP_API_KEY:
        raise SerpApiError("SERP_API_KEY is not set")
    params = {**params, "api_key": config.SERP_API_KEY}

    backoff = 1.0
    for attempt in range(config.SERP_MAX_RETRIES + 1):
        _throttle()
        resp = requests.get(_BASE_URL, params=params, timeout=30)

        if resp.status_code == 429:
            if attempt == config.SERP_MAX_RETRIES:
                raise SerpApiError("SerpAPI rate limit (429) - retries exhausted")
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else backoff
            time.sleep(delay)
            backoff = min(backoff * 2, 30.0)
            continue

        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise SerpApiError(data["error"])
        return data

    raise SerpApiError("SerpAPI request failed after retries")


def search_business(query: str) -> dict:
    """
    Resolve a free-text query (e.g. "鼎泰豐 信義店") to a single Google Maps
    place. Returns {data_id, title, address, rating, reviews_count, thumbnail}.
    Raises SerpApiError if nothing matched.
    """
    data = _get({"engine": "google_maps", "q": query, "type": "search"})

    place = data.get("place_results")
    if not place:
        local_results = data.get("local_results") or []
        if not local_results:
            raise SerpApiError(f"No Google Maps result found for '{query}'")
        place = local_results[0]

    data_id = place.get("data_id")
    if not data_id:
        raise SerpApiError(f"Matched place has no data_id for '{query}'")

    return {
        "data_id": data_id,
        "title": place.get("title", query),
        "address": place.get("address"),
        "rating": place.get("rating"),
        "reviews_count": place.get("reviews"),
        "thumbnail": place.get("thumbnail"),
        "gps_coordinates": place.get("gps_coordinates"),
    }


def _normalize_review(raw: dict) -> dict:
    user = raw.get("user") or {}
    return {
        "review_id": raw.get("review_id") or raw.get("link"),
        "author": user.get("name", "Unknown"),
        "rating": raw.get("rating"),
        "date": raw.get("date"),
        "text": (raw.get("snippet") or "").strip(),
        "likes": raw.get("likes", 0),
        "owner_reply": ((raw.get("response") or {}).get("snippet")),
    }


def fetch_reviews(
    data_id: str,
    max_reviews: int = 100,
    sort_by: str = "qualityScore",
) -> list[dict]:
    """
    Paginate through engine=google_maps_reviews for the given data_id.
    Stops once max_reviews is reached or SerpAPI has no more pages.
    Skips reviews with empty text (nothing for the LLM to judge).
    Pacing between pages (and between every other SerpAPI call, across
    threads) is handled centrally by _get()'s throttle, not here.
    """
    reviews: list[dict] = []
    next_page_token: Optional[str] = None

    while len(reviews) < max_reviews:
        params = {
            "engine": "google_maps_reviews",
            "data_id": data_id,
            "sort_by": sort_by,
            "hl": "zh-tw",
        }
        if next_page_token:
            params["next_page_token"] = next_page_token

        data = _get(params)
        page_reviews = data.get("reviews") or []
        if not page_reviews:
            break

        for raw in page_reviews:
            normalized = _normalize_review(raw)
            if normalized["text"]:
                reviews.append(normalized)
            if len(reviews) >= max_reviews:
                break

        next_page_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not next_page_token:
            break

    return reviews[:max_reviews]


def resolve_station_coordinates(station_name: str) -> dict:
    """
    Resolve a Taipei Metro station name to {lat, lng, title}, cached to disk
    so repeated lookups don't re-spend SerpAPI quota.
    """
    cached = station_coords_cache.get(station_name)
    if cached:
        return cached

    business = search_business(f"台北捷運{station_name}站")
    gps = business.get("gps_coordinates") or {}
    lat, lng = gps.get("latitude"), gps.get("longitude")
    if lat is None or lng is None:
        raise SerpApiError(f"No coordinates found for station '{station_name}'")

    result = {"lat": lat, "lng": lng, "title": business.get("title", station_name)}
    station_coords_cache.set(station_name, result)
    return result


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _normalize_nearby_place(raw: dict) -> Optional[dict]:
    data_id = raw.get("data_id")
    if not data_id:
        return None
    gps = raw.get("gps_coordinates") or {}
    return {
        "data_id": data_id,
        "title": raw.get("title", ""),
        "rating": raw.get("rating"),
        "reviews_count": raw.get("reviews"),
        "address": raw.get("address"),
        "type": raw.get("type"),
        "price": raw.get("price"),
        "thumbnail": raw.get("thumbnail"),
        "gps_coordinates": gps if gps.get("latitude") is not None else None,
    }


def search_nearby_food(
    lat: float,
    lng: float,
    keyword: str = "美食",
    zoom: int = 16,
    radius_km: Optional[float] = 1.0,
) -> list[dict]:
    """
    Search nearby restaurants/food around a coordinate via engine=google_maps.
    `zoom` narrows Google's own viewport (higher = tighter), but SerpAPI's
    google_maps engine has no hard radius param and can still surface distant
    / famous chains for a generic keyword like "美食" -- so after fetching we
    also drop any result whose own gps_coordinates put it more than
    `radius_km` from the search center (results missing coordinates are kept,
    since we can't verify their distance). Pass radius_km=None to disable
    this post-filter. Adds a "distance_km" field to each place for display.
    Returns normalized places, unsorted (caller applies rating threshold /
    top-N cutoff).
    """
    data = _get(
        {
            "engine": "google_maps",
            "q": keyword,
            "type": "search",
            "ll": f"@{lat},{lng},{zoom}z",
            "hl": "zh-tw",
        }
    )
    local_results = data.get("local_results") or []
    places = [_normalize_nearby_place(r) for r in local_results]
    places = [p for p in places if p]

    for p in places:
        gps = p.get("gps_coordinates")
        p["distance_km"] = (
            round(_haversine_km(lat, lng, gps["latitude"], gps["longitude"]), 2) if gps else None
        )

    if radius_km is not None:
        places = [p for p in places if p["distance_km"] is None or p["distance_km"] <= radius_km]

    return places

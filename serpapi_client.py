"""
Thin wrapper around SerpAPI's Google Maps engines.

  - search_business(query)  -> resolve a free-text query to a single place
                                (data_id + basic metadata) via engine=google_maps
  - fetch_reviews(data_id)  -> paginate engine=google_maps_reviews until
                                max_reviews is hit or SerpAPI runs out of pages
"""

import time
from typing import Optional

import requests

import config
from cache_store import station_coords_cache

_BASE_URL = "https://serpapi.com/search.json"


class SerpApiError(RuntimeError):
    pass


def _get(params: dict) -> dict:
    if not config.SERP_API_KEY:
        raise SerpApiError("SERP_API_KEY is not set")
    params = {**params, "api_key": config.SERP_API_KEY}
    resp = requests.get(_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise SerpApiError(data["error"])
    return data


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
    page_delay_sec: float = 0.3,
) -> list[dict]:
    """
    Paginate through engine=google_maps_reviews for the given data_id.
    Stops once max_reviews is reached or SerpAPI has no more pages.
    Skips reviews with empty text (nothing for the LLM to judge).
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

        time.sleep(page_delay_sec)

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


def _normalize_nearby_place(raw: dict) -> Optional[dict]:
    data_id = raw.get("data_id")
    if not data_id:
        return None
    return {
        "data_id": data_id,
        "title": raw.get("title", ""),
        "rating": raw.get("rating"),
        "reviews_count": raw.get("reviews"),
        "address": raw.get("address"),
        "type": raw.get("type"),
        "price": raw.get("price"),
        "thumbnail": raw.get("thumbnail"),
    }


def search_nearby_food(lat: float, lng: float, keyword: str = "美食", zoom: int = 15) -> list[dict]:
    """
    Search nearby restaurants/food around a coordinate via engine=google_maps.
    Returns SerpAPI's local_results, normalized, unfiltered (caller applies
    rating threshold / top-N cutoff).
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
    return [p for p in places if p]

"""
Persist a per-MRT-station report so batch analyses accumulate over time
instead of being thrown away after each run.

  data/reports/<station>/report.json
    - restaurants merged by data_id (re-analyzing a restaurant refreshes its
      entry in place; analyzing new ones appends to the same file)
    - grouped by category (Google's "type" field, e.g. 日式料理/火鍋店/咖啡廳)
    - a short LLM-written conclusion (overall + one per category), refreshed
      on every save so it reflects the current merged data
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

import config

_REPORTS_DIR = Path(__file__).parent / "data" / "reports"
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

_client: Optional[AsyncOpenAI] = None

_CONCLUSION_SYSTEM_PROMPT = """\
你是美食評論稽核助手。你會收到某個捷運站附近餐廳的洗評論稽核統計，已依類別分組。
請寫一份精簡的中文結論，並針對每個類別各寫一句「介紹」與一句「評估」。

輸出只能是以下 JSON 格式，不要有其他文字：
{
  "overall_conclusion": "整體結論，3~5句話，優先點出可信度偏低／疑似洗評的店家",
  "categories": {
    "類別名稱": {
      "intro": "一句話：這個類別目前有幾間、大致特色",
      "evaluation": "一句話：整體可信度如何、推薦或該留意哪些店"
    }
  }
}
"categories" 的 key 必須完全對應輸入中出現的類別名稱。
"""


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _safe_dirname(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "unknown"


def _report_path(station: str) -> Path:
    d = _REPORTS_DIR / _safe_dirname(station)
    d.mkdir(parents=True, exist_ok=True)
    return d / "report.json"


def load_report(station: str) -> Optional[dict]:
    path = _report_path(station)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _merge_restaurants(existing: list[dict], new: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in existing + new:
        key = r.get("data_id") or r.get("title")
        if key not in by_id:
            order.append(key)
        by_id[key] = r  # later entries (the fresh batch) win
    return [by_id[k] for k in order]


def _trust_sort_key(r: dict):
    score = r["summary"]["trust_score"]
    return (score is None, -(score if score is not None else 0))


def _group_by_category(restaurants: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in restaurants:
        groups.setdefault(r.get("type") or "其他", []).append(r)
    for items in groups.values():
        items.sort(key=_trust_sort_key)
    return groups


def _overview(restaurants: list[dict]) -> dict:
    scored = [r["summary"]["trust_score"] for r in restaurants if r["summary"]["trust_score"] is not None]
    avg_trust = round(sum(scored) / len(scored), 3) if scored else None
    flagged = [r["title"] for r in restaurants if r["summary"]["trust_score"] is not None and r["summary"]["trust_score"] < 0.6]
    return {
        "total_restaurants": len(restaurants),
        "avg_trust_score": avg_trust,
        "flagged_count": len(flagged),
        "flagged_titles": flagged,
    }


def _build_llm_input(station: str, groups: dict[str, list[dict]]) -> str:
    lines = [f"捷運站：{station}"]
    for cat, items in groups.items():
        lines.append(f"\n【類別：{cat}】共 {len(items)} 間")
        for r in items[:15]:
            score = r["summary"]["trust_score"]
            pct = f"{round(score * 100)}%" if score is not None else "-"
            s = r["summary"]
            lines.append(
                f"- {r['title']}：可信度 {pct}，Google {r.get('google_rating', '-')}星，"
                f"真實{s['genuine']}/可疑{s['suspicious']}/疑似洗評{s['fake']}"
            )
    return "\n".join(lines)


async def _generate_conclusion(station: str, groups: dict[str, list[dict]]) -> dict:
    if not config.OPENAI_API_KEY or not groups:
        return {"overall_conclusion": "", "categories": {}}
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=config.CLASSIFIER_MODEL,
            response_format={"type": "json_object"},
            temperature=0.3,
            messages=[
                {"role": "system", "content": _CONCLUSION_SYSTEM_PROMPT},
                {"role": "user", "content": _build_llm_input(station, groups)},
            ],
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"overall_conclusion": f"（結論產生失敗：{e}）", "categories": {}}


async def build_and_save_report(station: str, batch_report: dict) -> dict:
    """
    Merge batch_report's restaurants into the station's persisted report,
    regroup by category, regenerate the LLM conclusion, save to
    data/reports/<station>/report.json, and return the full merged report.
    """
    existing = load_report(station) or {"restaurants": []}
    merged = _merge_restaurants(existing.get("restaurants", []), batch_report.get("restaurants", []))

    groups = _group_by_category(merged)
    conclusion = await _generate_conclusion(station, groups)
    cat_notes = conclusion.get("categories", {})

    report = {
        "station": station,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "overview": _overview(merged),
        "conclusion": conclusion.get("overall_conclusion", ""),
        "categories": [
            {
                "name": cat,
                "count": len(items),
                "intro": cat_notes.get(cat, {}).get("intro", ""),
                "evaluation": cat_notes.get(cat, {}).get("evaluation", ""),
                "restaurants": items,
            }
            for cat, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        ],
        "restaurants": merged,
    }

    _report_path(station).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

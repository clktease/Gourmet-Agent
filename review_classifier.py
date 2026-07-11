"""
LLM-based fake/spam review classifier.

Judges purely on review text (+ its own star rating, which is part of the
review itself) -- no reviewer history or cross-review pattern signals.
Reviews are sent to the model in batches for cost/latency efficiency, with
a bounded number of batches in flight at once.
"""

import asyncio
import json
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

import config

_client: Optional[AsyncOpenAI] = None

VERDICTS = {"genuine", "suspicious", "fake"}

SYSTEM_PROMPT = """\
你是一個專門偵測 Google 商家評論真偽的審查員。你會收到一批評論（含星等與文字），
針對「每一則」評論獨立判斷是否為洗評論。

只判斷一件事：評論內容是否包含「留五星／好評／評論 換取好處」的交換式話術。
常見寫法（不限於這些）：
  "五星送XX"、"五星好評送XX"、"評論送XX"、"留言送XX"、"五星折XX"、"五星換XX"、
  "五星折抵XX"、"截圖五星領XX"、"打卡五星送XX"、"私訊/加賴出示五星領XX"
也就是句子裡有「五星／好評／評論／留言」搭配「送／換／折／領／贈／抵」這種因果／
交換關係，指向留評論可以換到贈品、折扣、免費品項等好處。

- 符合上述交換式話術 → "fake"
- 看不出這種交換關係（就算內容空泛、簡短、語氣浮誇、只寫「五星」兩個字、或提到店家
  單純招待/贈送但跟留評論無關） → "genuine"
- 交換關係寫得模糊、看不太出來是不是在講這件事 → "suspicious"

不要根據內容豐富度、語氣自然與否、星等是否合理等其他因素判斷，只看有沒有「留評論換好處」
這個交換關係。只根據評論文字本身判斷，不要臆測評論者的其他背景資訊。

## 範例
- "五星送50元" → fake
- "評論送小菜一份" → fake
- "私訊加賴出示五星截圖領飲料" → fake
- "五星" (只有兩個字，沒有任何交換文字) → genuine
- "超好吃大推大家都要來吃" (空泛但沒提到留評論換好處) → genuine
- "謝謝老闆招待的小菜，人很親切" (單純道謝，不是留評論換好處) → genuine
- "老闆說留言可以打折" (提到留言與折扣但語意不夠明確) → suspicious

請務必只輸出 JSON，格式如下，不要有任何其他文字：
{
  "results": [
    {"index": 0, "verdict": "genuine|suspicious|fake", "confidence": 0.0-1.0, "reason": "一句話中文理由"},
    ...
  ]
}
"index" 必須對應輸入中每則評論的 index，且每一則輸入評論都要有對應的輸出。
"""


# A review can only ever be judged "fake" by the exchange-for-review rule
# above -- (五星/好評/評論/留言/打卡) paired with (送/換/折/領/贈/抵). Reviews
# that contain none of the trigger words literally cannot match that rule, so
# they're skipped before hitting the LLM (cheaper + faster, same verdicts).
_TRIGGER_TERMS = ("五星", "好評", "評論", "留言", "打卡")
_EXCHANGE_TERMS = ("送", "換", "折", "領", "贈", "抵")


def _needs_llm_check(text: str) -> bool:
    return any(t in text for t in _TRIGGER_TERMS) and any(t in text for t in _EXCHANGE_TERMS)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _build_user_message(batch: list[dict]) -> str:
    lines = []
    for i, review in enumerate(batch):
        rating = review.get("rating", "N/A")
        text = review.get("text", "").replace("\n", " ")
        lines.append(f'[{i}] 星等: {rating}\n內容: "{text}"')
    return "請判斷以下評論：\n\n" + "\n\n".join(lines)


async def _classify_batch(batch: list[dict]) -> list[dict]:
    client = _get_client()
    fallback_reason = "模型回傳格式異常"
    try:
        resp = await client.chat.completions.create(
            model=config.CLASSIFIER_MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(batch)},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content)
        by_index = {int(r["index"]): r for r in parsed.get("results", [])}
    except Exception as e:
        by_index = {}
        fallback_reason = f"分類失敗: {e}"

    out = []
    for i, review in enumerate(batch):
        result = by_index.get(i)
        if result and result.get("verdict") in VERDICTS:
            verdict = result["verdict"]
            confidence = float(result.get("confidence", 0.5))
            reason = result.get("reason", "")
        else:
            verdict = "unknown"
            confidence = 0.0
            reason = fallback_reason
        out.append({**review, "verdict": verdict, "confidence": confidence, "reason": reason})
    return out


async def classify_reviews(
    reviews: list[dict],
    batch_size: Optional[int] = None,
    concurrency: Optional[int] = None,
    progress_cb: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> list[dict]:
    """
    Classify only the reviews that contain a review-for-reward trigger phrase
    (see _needs_llm_check) -- the one thing this classifier ever judges "fake"
    on. Reviews with none of those trigger words are dropped entirely rather
    than kept as "genuine": nobody suspected them in the first place, so
    counting them would dilute the fake-review ratio computed downstream.
    progress_cb(done_count, total_count) is awaited after each batch finishes,
    counted against only the reviews actually sent to the LLM.
    """
    to_check = [r for r in reviews if _needs_llm_check(r.get("text", ""))]
    if not to_check:
        return []

    batch_size = batch_size or config.CLASSIFY_BATCH_SIZE
    concurrency = concurrency or config.CLASSIFY_CONCURRENCY

    batches = [to_check[i : i + batch_size] for i in range(0, len(to_check), batch_size)]
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()
    results: list[Optional[list[dict]]] = [None] * len(batches)

    async def run_one(idx: int, batch: list[dict]):
        nonlocal done
        async with semaphore:
            classified = await _classify_batch(batch)
        results[idx] = classified
        async with lock:
            done += len(batch)
            if progress_cb:
                await progress_cb(done, len(to_check))

    await asyncio.gather(*(run_one(i, b) for i, b in enumerate(batches)))

    flattened: list[dict] = []
    for chunk in results:
        flattened.extend(chunk or [])
    return flattened

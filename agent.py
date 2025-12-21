import os
import requests
from typing import List, Dict, TypedDict
from dotenv import load_dotenv  # 新增：讀取 .env 檔案
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
import streamlit as st

# --- API 配置載入 ---
# 1. 優先從 .env 讀取 (開發環境)
# 2. 同時相容 Streamlit Secrets (部署環境)
load_dotenv()

SERP_API_KEY = os.getenv("SERP_API_KEY") or st.secrets.get("SERP_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY")

# 設定環境變數供 LangChain 使用
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

class AgentState(TypedDict):
    location: str
    restaurants: List[Dict]
    final_report: str

# --- 1. 核心搜尋與過濾邏輯 ---
def get_restaurant_data(location_query: str):
    """使用 SerpApi 進行兩階段搜尋"""
    if not SERP_API_KEY:
        st.error("錯誤：找不到 SERP_API_KEY，請檢查環境變數或 .env 檔案")
        return []

    base_url = "https://serpapi.com/search.json"
    
    search_params = {
        "engine": "google_maps",
        "q": f"餐廳 near {location_query}",
        "type": "search",
        "api_key": SERP_API_KEY,
        "hl": "zh-tw"
    }
    
    try:
        search_res = requests.get(base_url, params=search_params).json()
        places = search_res.get("local_results", [])[:5] 
        
        final_list = []
        target_keywords = ["送", "五星", "評論"]

        for p in places:
            name = p.get("title")
            data_id = p.get("data_id")
            st.write(f"🔍 正在定向檢索：{name}...")

            keyword_buckets = {}
            for kw in target_keywords:
                review_params = {
                    "engine": "google_maps_reviews",
                    "data_id": data_id,
                    "query": kw,
                    "api_key": SERP_API_KEY,
                    "hl": "zh-tw",
                    "num": 5
                }
                rev_res = requests.get(base_url, params=review_params).json()
                snippets = [r.get("snippet") for r in rev_res.get("reviews", []) if r.get("snippet")]
                keyword_buckets[kw] = snippets

            final_list.append({
                "name": name,
                "category": p.get("type", "美食"),
                "rating": p.get("rating"),
                "price": p.get("price", "中價位"),
                "booking": p.get("website", "現場排隊"),
                "keyword_reviews": keyword_buckets
            })
            
        return final_list
    except Exception as e:
        st.error(f"API 請求出錯: {e}")
        return []

# --- 2. LangGraph 節點定義 ---
def search_node(state: AgentState):
    data = get_restaurant_data(state['location'])
    return {"restaurants": data}

def analyze_node(state: AgentState):
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    
    context = ""
    for r in state['restaurants']:
        rev_info = ""
        for kw, texts in r['keyword_reviews'].items():
            rev_info += f"【關鍵字 '{kw}' 樣本】:\n" + ("\n".join([f"- {t}" for t in texts]) if texts else "- (無匹配)") + "\n"
        
        context += f"店名: {r['name']}\n類型: {r['category']}\n評分: {r['rating']}\n{rev_info}\n---\n"

    prompt = f"""
    你是一位專業美食誠信調查員。請分析「{state['location']}」附近的餐廳。
    
    分析資料內容：
    {context}
    
    任務：
    1. 分類呈現：依據「美食種類」對餐廳進行分組。
    2. 誠信判定：分析「送」與「五星」樣本。若出現「五星送小菜」、「打卡送肉」等對價行為，請標註「誠信度：低」。
    3. 綜合介紹：包含價位、特色、以及訂位建議。
    
    請使用繁體中文，以精美的 Markdown 表格與列表呈現。
    """
    response = llm.invoke([HumanMessage(content=prompt)])
    return {"final_report": response.content}

# --- 3. 建構 Graph ---
workflow = StateGraph(AgentState)
workflow.add_node("search", search_node)
workflow.add_node("analyze", analyze_node)
workflow.set_entry_point("search")
workflow.add_edge("search", "analyze")
workflow.add_edge("analyze", END)
app = workflow.compile()

# --- 4. Streamlit UI ---
st.set_page_config(page_title="美食真探 5.0", layout="wide")
st.title("🕵️ 美食真探：定向關鍵字偵察")
st.markdown("---")

if not OPENAI_API_KEY or not SERP_API_KEY:
    st.warning("⚠️ 請在 .env 檔案或環境變數中設定 API Key 以利運行。")

loc = st.text_input("搜尋中心點 (例如：中山捷運站)", value="中山捷運站")

if st.button("啟動深挖探針"):
    with st.spinner("正在執行 5x3 定向過濾分析中..."):
        try:
            result = app.invoke({"location": loc, "restaurants": []})
            st.success(f"🔍 偵察完成！以下是 {loc} 的深度分析報告：")
            st.markdown(result["final_report"])
            
            with st.expander("查看後台 5x3 定向過濾原始數據"):
                st.write(result["restaurants"])
        except Exception as e:
            st.error(f"分析失敗: {e}")
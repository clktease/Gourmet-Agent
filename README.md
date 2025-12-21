# 🕵️ Gourmet-Agent: Integrity-Based Foodie Agent

GourmetIntel AI is an intelligent agentic workflow designed to uncover the truth behind restaurant ratings. Using LLM-powered analysis and targeted web scraping, it identifies promotional bias (e.g., "free appetizers for 5-star reviews") to give you an honest look at the local food scene.

## 🌟 Key Features
- **Targeted Reconnaissance**: Uses a 5x3 scanning matrix to analyze the top 5 nearby restaurants with 3 specific integrity keywords.
- **Integrity Audit**: GPT-4o analyzes review snippets to flag "incentivized ratings" (e.g., "Check-in for free pork").
- **Agentic Workflow**: Built on **LangGraph**, ensuring a robust state-managed pipeline from search to final report.
- **Smart Categorization**: Automatically groups restaurants by cuisine and provides price points and booking advice.
- **Transparent Data**: Users can expand the "Raw Data" section to see the exact review snippets the AI analyzed.

## 🛠️ Tech Stack
- **Orchestration**: [LangGraph](https://github.com/langchain-ai/langgraph)
- **LLM**: OpenAI GPT-4o
- **Search Engine**: [SerpApi](https://serpapi.com/) (Google Maps Search & Reviews API)
- **Interface**: Streamlit
- **Language**: Python 3.9+

## 🚀 Getting Started

### 1. Prerequisites
- An OpenAI API Key
- A SerpApi Key

### 2. Installation
Clone the repository and install the dependencies:
```bash
git clone [https://github.com/yourusername/gourmet-intel-ai.git](https://github.com/yourusername/gourmet-intel-ai.git)
cd gourmet-intel-ai
pip install -r requirements.txt
"""web_search — Tavily. Live, AI-native web search for current tips & ideas."""
from tools.registry import tool
from http_client import request
from config import cfg


@tool("web_search",
      "Search the live web for current tips, events, or recommendations.",
      {"type": "object",
       "properties": {"query": {"type": "string"}},
       "required": ["query"]})
def web_search(query):
    data = request("POST", "https://api.tavily.com/search",
                   json={"api_key": cfg.TAVILY_API_KEY, "query": query,
                         "max_results": 5, "search_depth": "basic",
                         "include_answer": True})
    results = [{"title": r.get("title"), "url": r.get("url"),
                "snippet": (r.get("content") or "")[:180]}
               for r in data.get("results", [])]
    return {"answer": data.get("answer"), "results": results}

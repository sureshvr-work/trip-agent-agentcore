"""geocode_place — Geoapify Geocoding. Turns a place name into coordinates."""
from tools.registry import tool
from http_client import request
from config import cfg


@tool("geocode_place",
      "Resolve a city or place name to coordinates and country. Call this first.",
      {"type": "object",
       "properties": {"query": {"type": "string", "description": "City or place name"}},
       "required": ["query"]})
def geocode_place(query):
    data = request("GET", "https://api.geoapify.com/v1/geocode/search",
                   params={"text": query, "limit": 1, "apiKey": cfg.GEOAPIFY_KEY})
    feats = data.get("features") or []
    if not feats:
        return {"error": f"no location matched '{query}'"}
    p = feats[0]["properties"]
    return {"name": p.get("formatted"), "lat": p.get("lat"),
            "lon": p.get("lon"), "country": p.get("country")}

"""find_places — Geoapify Places. Real hotels / attractions / restaurants nearby."""
from tools.registry import tool
from http_client import request
from config import cfg

# our friendly category -> Geoapify category code
CATEGORIES = {"hotels": "accommodation.hotel",
              "attractions": "tourism.sights",
              "restaurants": "catering.restaurant"}


@tool("find_places",
      "Find real hotels, attractions, or restaurants near coordinates. Optional "
      "radius_m (default 5000). If a search returns nothing, retry once with a "
      "larger radius_m (e.g. 15000) or a different category before giving up.",
      {"type": "object",
       "properties": {"category": {"type": "string", "enum": list(CATEGORIES)},
                      "lat": {"type": "number"}, "lon": {"type": "number"},
                      "radius_m": {"type": "number",
                                   "description": "search radius in metres (default 5000)"}},
       "required": ["category", "lat", "lon"]})
def find_places(category, lat, lon, radius_m=5000):
    code = CATEGORIES.get(category, "tourism.sights")
    data = request("GET", "https://api.geoapify.com/v2/places",
                   params={"categories": code,
                           "filter": f"circle:{lon},{lat},{int(radius_m)}",
                           "limit": 6, "apiKey": cfg.GEOAPIFY_KEY})
    places = []
    for f in data.get("features", []):
        p = f["properties"]
        if p.get("name"):
            places.append({"name": p["name"],
                           "address": p.get("address_line2") or p.get("formatted")})
    return {"category": category, "radius_m": int(radius_m), "places": places}

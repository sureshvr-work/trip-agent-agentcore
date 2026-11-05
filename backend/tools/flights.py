"""
search_flights — AviationStack. Real scheduled flights between two airports.

Honest scope: this is flight *schedule/status* data, not fares or booking — no
free aviation API sells inventory. The agent plans around real schedules.
"""
from tools.registry import tool
from http_client import request
from config import cfg


@tool("search_flights",
      "Look up real scheduled flights between two airports (IATA codes), "
      "optionally on a date (YYYY-MM-DD). Returns schedule/status, not prices.",
      {"type": "object",
       "properties": {"dep_iata": {"type": "string", "description": "e.g. BLR"},
                      "arr_iata": {"type": "string", "description": "e.g. GOI"},
                      "flight_date": {"type": "string", "description": "YYYY-MM-DD"}},
       "required": ["dep_iata", "arr_iata"]})
def search_flights(dep_iata, arr_iata, flight_date=None):
    params = {"access_key": cfg.AVIATIONSTACK_KEY,
              "dep_iata": dep_iata, "arr_iata": arr_iata, "limit": 5}
    if flight_date:
        params["flight_date"] = flight_date
    data = request("GET", "https://api.aviationstack.com/v1/flights", params=params)

    # AviationStack reports its own errors in the body (e.g. quota, bad key).
    if isinstance(data, dict) and data.get("error"):
        return {"error": data["error"].get("message", "aviationstack error")}

    flights = []
    for f in data.get("data", []):
        flights.append({"airline": (f.get("airline") or {}).get("name"),
                        "flight": (f.get("flight") or {}).get("iata"),
                        "departs": (f.get("departure") or {}).get("scheduled"),
                        "arrives": (f.get("arrival") or {}).get("scheduled"),
                        "status": f.get("flight_status")})
    return {"flights": flights}

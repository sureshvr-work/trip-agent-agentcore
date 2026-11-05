"""get_weather — Open-Meteo. 7-day outlook for coordinates. No API key needed."""
from tools.registry import tool
from http_client import request


@tool("get_weather",
      "Get a 7-day weather outlook (highs/lows/rain) for coordinates.",
      {"type": "object",
       "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
       "required": ["lat", "lon"]})
def get_weather(lat, lon):
    data = request("GET", "https://api.open-meteo.com/v1/forecast",
                   params={"latitude": lat, "longitude": lon,
                           "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                           "forecast_days": 7, "timezone": "auto"})
    d = data.get("daily", {})
    days = []
    for i, date in enumerate(d.get("time", [])):
        days.append({"date": date,
                     "hi": d["temperature_2m_max"][i],
                     "lo": d["temperature_2m_min"][i],
                     "rain_pct": d["precipitation_probability_max"][i]})
    return {"days": days}

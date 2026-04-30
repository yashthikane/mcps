# server.py  (exactly the same as before)
import httpx
from fastmcp import FastMCP

mcp = FastMCP("Weather Server")

@mcp.tool()
async def get_weather(city: str) -> str:
    """
    Get the current weather for a city.
    Returns temperature in Celsius and weather condition.
    """
    geo_url = "https://geocoding-api.open-meteo.com/v1/search"
    async with httpx.AsyncClient() as client:
        geo_resp = await client.get(geo_url, params={"name": city, "count": 1})
        geo_data = geo_resp.json()

    if not geo_data.get("results"):
        return f"Could not find city: {city}"

    location = geo_data["results"][0]
    lat, lon = location["latitude"], location["longitude"]
    name = location["name"]

    weather_url = "https://api.open-meteo.com/v1/forecast"
    async with httpx.AsyncClient() as client:
        w_resp = await client.get(weather_url, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weathercode",
            "timezone": "auto"
        })
        w_data = w_resp.json()

    current = w_data["current"]
    temp = current["temperature_2m"]
    code = current["weathercode"]

    conditions = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy",
        3: "Overcast", 45: "Foggy", 61: "Light rain",
        63: "Moderate rain", 80: "Rain showers", 95: "Thunderstorm"
    }
    condition = conditions.get(code, f"Weather code {code}")

    return f"{name}: {temp}°C, {condition}"


@mcp.tool()
def square(n:int)->int:
    """Calculate square of a number."""
    return n*n

if __name__ == "__main__":
    mcp.run()
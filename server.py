# server.py  (exactly the same as before)
import httpx
from fastmcp import FastMCP
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from datetime import datetime, timezone
import os

SCOPES = ["https://www.googleapis.com/auth/calendar"]


# 🔹 AUTH FUNCTION
def get_calendar_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    return service


mcp = FastMCP("Omni Server")

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

@mcp.tool()
async  def get_jokes() -> str:
    """get random jokes"""
    url = "https://official-joke-api.appspot.com/random_joke"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        data = response.json()
    
    setup = data["setup"]
    punchline = data["punchline"]

    return f"{setup} - {punchline}"

@mcp.tool()
def get_events() -> str:
    """Get upcoming 5 calendar events"""

    service = get_calendar_service()
    now = datetime.now(timezone.utc).isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=5,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    if not events:
        return "No upcoming events found."

    result = []
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        result.append(f"{start} - {event['summary']}")

    return "\n".join(result)

@mcp.tool()
def create_event(summary: str, start_time: str, end_time: str) -> str:
    """
    Create a calendar event.
    Time format: YYYY-MM-DDTHH:MM:SS
    Example: 2026-05-01T10:00:00
    """

    service = get_calendar_service()

    event = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end_time, "timeZone": "Asia/Kolkata"},
    }

    event = service.events().insert(
        calendarId="primary",
        body=event
    ).execute()

    return f"Event created: {event.get('htmlLink')}"


if __name__ == "__main__":
    mcp.run()
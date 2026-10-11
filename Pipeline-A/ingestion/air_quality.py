
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def fetch_stations(latitude, longitude, radius=25000):
    api_key = os.getenv("OPENAQ_API_KEY")

    if not api_key:
        raise ValueError("OPENAQ_API_KEY is missing from .env")

    url = "https://api.openaq.org/v3/locations"

    params = {
        "coordinates": f"{latitude},{longitude}",
        "radius": radius,
        "limit": 100,
        "page": 1
    }

    headers = {"X-API-Key": api_key}

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    return response.json().get("results", [])


if __name__ == "__main__":
    stations = fetch_stations(28.6139, 77.2090)

    print("Air-quality monitoring stations")
    print("--------------------------------")
    print("Stations found:", len(stations))

    for station in stations:
        coordinates = station.get("coordinates") or {}

        print("\nStation:", station.get("name"))
        print("Location ID:", station.get("id"))
        print("Latitude:", coordinates.get("latitude"))
        print("Longitude:", coordinates.get("longitude"))

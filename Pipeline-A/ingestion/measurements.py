
import os
import requests
from dotenv import load_dotenv

from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def fetch_measurements(sensor_id, limit=1):
    api_key = os.getenv("OPENAQ_API_KEY")

    if not api_key:
        raise ValueError("OPENAQ_API_KEY is missing from .env")

    url = f"https://api.openaq.org/v3/sensors/{sensor_id}/measurements"

    params = {
        "limit": limit,
        "page": 1
    }

    headers = {
        "X-API-Key": api_key
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    return response.json().get("results", [])


if __name__ == "__main__":
    sensor_ids = {
        "PM2.5": 12237376,
        "PM10": 12237375
    }

    for parameter, sensor_id in sensor_ids.items():
        print(f"\n{parameter} Measurements")
        print("-" * 30)

        try:
            measurements = fetch_measurements(sensor_id)

            if not measurements:
                print("No measurements returned.")
                continue

            for measurement in measurements:
                print("Value:", measurement.get("value"))
                print("Parameter:", parameter)
                print("Units:", measurement.get("units"))
                print("Date:", measurement.get("period"))

        except requests.RequestException as error:
            print(f"API request failed: {error}")


def fetch_latest_for_location(location_id, limit=100):
    api_key = os.getenv("OPENAQ_API_KEY")

    if not api_key:
        raise ValueError("OPENAQ_API_KEY is missing from .env")

    url = f"https://api.openaq.org/v3/locations/{location_id}/latest"

    response = requests.get(
        url,
        params={"limit": limit, "page": 1},
        headers={"X-API-Key": api_key},
        timeout=20
    )

    response.raise_for_status()
    return response.json().get("results", [])

def fetch_sensor_details(sensor_id):
    api_key = os.getenv("OPENAQ_API_KEY")

    if not api_key:
        raise ValueError("OPENAQ_API_KEY is missing from .env")

    url = f"https://api.openaq.org/v3/sensors/{sensor_id}"

    response = requests.get(
        url,
        headers={"X-API-Key": api_key},
        timeout=20
    )

    response.raise_for_status()

    return response.json().get("results", {})

def normalize_latest_measurement(item, parameter, units):
    datetime_info = item.get("datetime") or {}
    timestamp = datetime_info.get("utc")

    if not timestamp:
        return None

    return {
        "value": item.get("value"),
        "units": units,
        "period": {
            "datetimeFrom": {
                "utc": timestamp
            }
        },
        "sensor_id": item.get("sensorsId"),
        "location_id": item.get("locationsId"),
        "coordinates": item.get("coordinates"),
        "rawData": item
    }


def fetch_sensor_details(sensor_id):
    api_key = os.getenv("OPENAQ_API_KEY")

    if not api_key:
        raise ValueError("OPENAQ_API_KEY is missing from .env")

    url = f"https://api.openaq.org/v3/sensors/{sensor_id}"

    response = requests.get(
        url,
        headers={"X-API-Key": api_key},
        timeout=20
    )

    response.raise_for_status()
    results = response.json().get("results", [])

    if not results:
        return None

    return results[0]

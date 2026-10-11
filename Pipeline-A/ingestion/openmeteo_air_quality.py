
import requests
from datetime import datetime, timezone

from processing.validator import validate_measurement


API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def fetch_current_pm25(latitude=28.6139, longitude=77.2090):
    response = requests.get(
        API_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "pm2_5",
            "timezone": "UTC",
        },
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()
    current = data.get("current", {})
    pm25 = current.get("pm2_5")
    timestamp = current.get("time")
    units = data.get("current_units", {}).get("pm2_5")

    if pm25 is None or not timestamp or not units:
        return None

    try:
        observed_at = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )

        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)

        observed_at = observed_at.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

    measurement = {
        "value": pm25,
        "units": units,
        "period": {
            "datetimeFrom": {
                "utc": observed_at.isoformat()
            }
        },
    }

    valid, result = validate_measurement(
        measurement,
        parameter="pm25",
        max_age_hours=24,
    )

    if not valid:
        print("Open-Meteo reading rejected:", result)
        return None

    return {
        "source": "openmeteo",
        "latitude": data["latitude"],
        "longitude": data["longitude"],
        "pm25": result["value"],
        "observedAt": result["observed_at"],
        "unit": result["units"],
        "dataType": "modelled",
    }

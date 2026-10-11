
import requests


def fetch_weather(latitude, longitude):
    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "wind_speed_10m"
        )
    }

    response = requests.get(
        url,
        params=params,
        timeout=15
    )

    response.raise_for_status()

    data = response.json()

    return data["current"]


if __name__ == "__main__":
    weather = fetch_weather(28.6139, 77.2090)

    print("Weather Data")
    print("------------")
    print("Time:", weather["time"])
    print("Temperature:", weather["temperature_2m"], "°C")
    print("Humidity:", weather["relative_humidity_2m"], "%")
    print("Wind Speed:", weather["wind_speed_10m"], "km/h")

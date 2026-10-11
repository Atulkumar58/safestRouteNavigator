
import json
from pathlib import Path

import requests


OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "new_delhi_roads.geojson"
)

OVERPASS_URL = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
# South, West, North, East
BBOX = (28.60, 77.18, 28.64, 77.24)


def fetch_roads():
    south, west, north, east = BBOX

    query = f"""
    [out:json][timeout:60];
    way["highway"]({south},{west},{north},{east});
    out geom;
    """

    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": "SafeRouteNavigator/1.0"},
        timeout=90
    )

    if not response.ok:
        print("HTTP status:", response.status_code)

    response.raise_for_status()

    elements = response.json().get("elements", [])
    features = []

    for element in elements:
        geometry = element.get("geometry", [])

        if len(geometry) < 2:
            continue

        coordinates = [
            [point["lon"], point["lat"]]
            for point in geometry
        ]

        tags = element.get("tags", {})

        features.append({
            "type": "Feature",
            "properties": {
                "segmentId": f"osm-way-{element['id']}",
                "roadName": tags.get("name"),
                "highway": tags.get("highway"),
                "source": "OpenStreetMap",
                "osmWayId": element["id"]
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates
            }
        })

    if not features:
        raise RuntimeError(
            "No road geometries returned. Check the API response."
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    OUTPUT_PATH.write_text(
        json.dumps(geojson),
        encoding="utf-8"
    )

    print("Road features:", len(features))
    print("Saved to:", OUTPUT_PATH)


if __name__ == "__main__":
    try:
        fetch_roads()
    except requests.RequestException as error:
        print("Road API request failed:", error)
        print("Check the HTTP status and server response above.")

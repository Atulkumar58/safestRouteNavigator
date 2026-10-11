
from datetime import datetime, timezone
from math import isfinite


def normalize_pollution_observation(
    station_id,
    coordinates,
    parameter,
    validated_measurement,
    raw_data,
    source="openaq",
    data_type=None,
):
    parameter = parameter.lower()

    supported_parameters = {
        "pm25", "pm10", "no2", "o3", "so2", "co"
    }

    if parameter not in supported_parameters:
        raise ValueError(f"Unsupported pollutant: {parameter}")

    if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
        raise ValueError("Coordinates must be [longitude, latitude].")

    try:
        longitude, latitude = map(float, coordinates)
    except (TypeError, ValueError):
        raise ValueError("Coordinates must be numeric [longitude, latitude].") from None

    if not (
        isfinite(longitude)
        and isfinite(latitude)
        and -180 <= longitude <= 180
        and -90 <= latitude <= 90
    ):
        raise ValueError("Coordinates are outside valid longitude/latitude ranges.")

    now = datetime.now(timezone.utc)

    document = {
        "source": source,
        "stationId": str(station_id),
        "parameter": parameter,
        "observedAt": validated_measurement["observed_at"],
        "location": {
            "type": "Point",
            "coordinates": [longitude, latitude],
        },
        parameter: validated_measurement["value"],
        "units": validated_measurement["units"],
        "rawData": raw_data,
        "createdAt": now,
        "updatedAt": now,
    }

    if data_type:
        document["dataType"] = data_type

    return document

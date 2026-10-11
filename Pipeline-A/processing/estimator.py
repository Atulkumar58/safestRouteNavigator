
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2, isfinite


def build_observation_index(observations, max_distance_km=5):
    """Build a coarse geographic index so each road checks nearby readings only."""
    cell_degrees = max_distance_km / 111.32
    buckets = {}
    for observation in observations:
        location = observation.get("location") or {}
        point = location.get("coordinates") or []
        if len(point) < 2:
            continue
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        if not (isfinite(lon) and isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        cell = (int(lat // cell_degrees), int(lon // cell_degrees))
        buckets.setdefault(cell, []).append(observation)
    return cell_degrees, buckets


def nearby_observations(index, longitude, latitude, max_distance_km):
    cell_degrees, buckets = index
    latitude_radius = max_distance_km / 111.32
    longitude_radius = max_distance_km / max(11.132, 111.32 * abs(cos(radians(latitude))))
    lat_cell, lon_cell = int(latitude // cell_degrees), int(longitude // cell_degrees)
    lat_steps = int(latitude_radius / cell_degrees) + 1
    lon_steps = int(longitude_radius / cell_degrees) + 1
    for lat_offset in range(-lat_steps, lat_steps + 1):
        for lon_offset in range(-lon_steps, lon_steps + 1):
            yield from buckets.get((lat_cell + lat_offset, lon_cell + lon_offset), ())


def distance_km(lon1, lat1, lon2, lat2):
    radius = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    a = max(0.0, min(1.0, a))
    return 2 * radius * atan2(sqrt(a), sqrt(1 - a))


def pm25_aqi(concentration):
    ranges = [
        (0, 30, 0, 50),
        (30, 60, 50, 100),
        (60, 90, 100, 200),
        (90, 120, 200, 300),
        (120, 250, 300, 400),
        (250, 500, 400, 500),
    ]

    value = max(0.0, float(concentration))

    for c_low, c_high, i_low, i_high in ranges:
        if value <= c_high:
            result = (
                (i_high - i_low) / (c_high - c_low)
                * (value - c_low)
                + i_low
            )
            return round(result)

    return 500


def estimate_road_segment(
    feature,
    observations,
    max_distance_km=5,
    max_age_hours=24,
    observation_index=None,
):
    if max_distance_km <= 0 or max_age_hours <= 0:
        return None

    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []

    if len(coordinates) < 2:
        return None

    try:
        longitude, latitude = coordinates[len(coordinates) // 2]
        longitude = float(longitude)
        latitude = float(latitude)

        if not (
            isfinite(longitude)
            and isfinite(latitude)
            and -180 <= longitude <= 180
            and -90 <= latitude <= 90
        ):
            return None

    except (TypeError, ValueError, IndexError):
        return None

    now = datetime.now(timezone.utc)
    nearby = []

    candidates = nearby_observations(
        observation_index or build_observation_index(observations, max_distance_km),
        longitude,
        latitude,
        max_distance_km,
    )
    for observation in candidates:
        pm25 = observation.get("pm25")
        location = observation.get("location") or {}
        point = location.get("coordinates") or []
        observed_at = observation.get("observedAt")

        if pm25 is None or len(point) < 2 or not observed_at:
            continue

        try:
            value = float(pm25)

            if not isfinite(value) or value < 0:
                continue

            point_lon = float(point[0])
            point_lat = float(point[1])

            if not (
                isfinite(point_lon)
                and isfinite(point_lat)
                and -180 <= point_lon <= 180
                and -90 <= point_lat <= 90
            ):
                continue

            if isinstance(observed_at, str):
                observed_at = datetime.fromisoformat(
                    observed_at.replace("Z", "+00:00")
                )

            if not isinstance(observed_at, datetime):
                continue

            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(
                    tzinfo=timezone.utc
                )

            age_hours = (
                now - observed_at.astimezone(timezone.utc)
            ).total_seconds() / 3600

            if age_hours < 0 or age_hours > max_age_hours:
                continue

            distance = distance_km(
                longitude,
                latitude,
                point_lon,
                point_lat,
            )

            if distance <= max_distance_km:
                nearby.append(
                    (
                        value,
                        max(distance, 0.1),
                        age_hours,
                        str(observation.get("source", "unknown")),
                        str(observation.get("dataType", "measured")),
                        observed_at.astimezone(timezone.utc),
                    )
                )

        except (TypeError, ValueError, OverflowError):
            continue

    if not nearby:
        return None

    weights = [
        (1 / (item[1] ** 2)) * (1 / (1 + item[2]))
        for item in nearby
    ]

    total_weight = sum(weights)

    if not isfinite(total_weight) or total_weight <= 0:
        return None

    estimated_pm25 = sum(
        item[0] * weight
        for item, weight in zip(nearby, weights)
    ) / total_weight

    if not isfinite(estimated_pm25):
        return None

    sources = sorted({item[3] for item in nearby})
    data_types = sorted({item[4] for item in nearby})

    properties = feature.get("properties") or {}
    segment_id = properties.get("segmentId") or properties.get("osm_id")

    if segment_id is None:
        return None

    count_factor = min(1.0, len(nearby) / 3)

    distance_factor = max(
        0.0,
        1 - min(item[1] for item in nearby) / max_distance_km,
    )

    freshness_factor = sum(
        max(0.0, 1 - item[2] / max_age_hours)
        for item in nearby
    ) / len(nearby)

    confidence = count_factor * distance_factor * freshness_factor

    if "modelled" in data_types:
        confidence *= 0.5

    confidence = max(0.0, min(1.0, confidence))
    source_observed_at = min(item[5] for item in nearby)

    return {
        "segmentId": str(segment_id),
        "roadName": properties.get("roadName") or properties.get("name"),
        "geometry": geometry,
        "estimatedAt": now,
        "sourceObservedAt": source_observed_at,
        "estimatedPm25": round(estimated_pm25, 2),
        "estimatedAqi": pm25_aqi(estimated_pm25),
        "exposureScore": round(estimated_pm25, 2),
        "confidence": round(confidence, 3),
        "factors": {
            "method": "inverse_distance_weighting",
            "nearbyObservationCount": len(nearby),
            "maxDistanceKm": max_distance_km,
            "maxAgeHours": max_age_hours,
            "sources": sources,
            "dataTypes": data_types,
        },
    }

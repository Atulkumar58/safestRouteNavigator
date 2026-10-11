
import json
import os
import re
import signal
import time
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone, timedelta
from pathlib import Path


from requests.exceptions import HTTPError

from ingestion.openmeteo_air_quality import fetch_current_pm25
from ingestion.weather import fetch_weather
from ingestion.air_quality import fetch_stations
from ingestion.measurements import fetch_latest_for_location
from ingestion.sensor_cache import (
    load_sensor_cache,
    get_sensor_details,
)
from processing.validator import validate_measurement
from processing.normalizer import normalize_pollution_observation
from processing.estimator import estimate_road_segment, build_observation_index
from storage.mongodb import (
    connect_mongodb,
    save_weather_observation,
    save_normalized_pollution_observation,
    save_road_segment_estimate,
    save_road_segment_estimates_bulk,
    acquire_pipeline_lock,
    renew_pipeline_lock,
    release_pipeline_lock,
    mark_pipeline_lock_completed,
    mark_pipeline_lock_failed,
)

LATITUDE = 28.6139
LONGITUDE = 77.2090

MAX_STATIONS_PER_RUN = 5
MAX_AGE_HOURS = 24
ESTIMATION_OBSERVATION_LIMIT = max(100, int(os.getenv("MAX_ESTIMATION_OBSERVATIONS", "20000")))

SUPPORTED_PARAMETERS = {
    "pm25",
    "pm10",
    "no2",
    "o3",
    "so2",
    "co",
}
last_database_success = 0.0


def safe_error(error):
    message = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    message = re.sub(r"mongodb(?:\+srv)?://[^\s]+", "[REDACTED_MONGODB_URI]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(x-api-key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", message)
    message = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", message)
    message = re.sub(r"(?i)([?&]key=)[^&\s]+", r"\1[REDACTED]", message)
    return json.dumps(message, ensure_ascii=False)


def parse_timestamp(timestamp):
    observed_at = datetime.fromisoformat(
        timestamp.replace("Z", "+00:00")
    )

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    return observed_at.astimezone(timezone.utc)


def ingest_weather(database, should_continue=lambda: True):
    print("\n2. Fetching weather data...")

    try:
        weather = fetch_weather(LATITUDE, LONGITUDE)

        print("Temperature:", weather.get("temperature_2m"), "°C")
        print("Humidity:", weather.get("relative_humidity_2m"), "%")
        print("Wind speed:", weather.get("wind_speed_10m"), "km/h")
        print("Weather time:", weather.get("time"))

        if database is None:
            print("Weather not saved: MongoDB unavailable.")
            return False

        if not weather.get("time"):
            raise ValueError("Weather observation timestamp is missing")

        document = {
            "source": "weather_api",
            "observedAt": parse_timestamp(weather["time"]),
            "location": {
                "type": "Point",
                "coordinates": [LONGITUDE, LATITUDE],
            },
            "temperatureC": weather.get("temperature_2m"),
            "humidityPercent": weather.get("relative_humidity_2m"),
            "windSpeedKph": weather.get("wind_speed_10m"),
            "rawData": weather,
            "createdAt": datetime.now(timezone.utc),
            "updatedAt": datetime.now(timezone.utc),
        }

        required_fields = (
            "temperatureC",
            "humidityPercent",
            "windSpeedKph",
        )

        if any(document[key] is None for key in required_fields):
            raise ValueError("Weather data contains missing fields")

        if not should_continue():
            return False
        inserted_id = save_weather_observation(database, document)
        print("Weather record saved. ID:", inserted_id)
        return True

    except Exception as error:
        print("Weather processing/storage failed:", safe_error(error))
        return False


def ingest_pollution(database, should_continue=lambda: True):
    print("\n3. Discovering air-quality stations...")

    try:
        stations = fetch_stations(LATITUDE, LONGITUDE)
        print("Stations found:", len(stations))
    except Exception as error:
        print("Station discovery failed:", safe_error(error))
        return False

    selected_stations = stations[:MAX_STATIONS_PER_RUN]

    print("Stations selected:", len(selected_stations))

    for station in selected_stations:
        print("-", station.get("name"))

    if database is None:
        print("Skipping pollution storage: MongoDB unavailable.")
        return False

    print("\n4. Fetching, validating and storing pollution data...")

    sensor_cache = load_sensor_cache()
    saved_count = 0
    rejected_count = 0
    complete = bool(selected_stations)

    for station_index, station in enumerate(selected_stations):
        if not should_continue():
            return False
        station_id = station.get("id")

        if station_id is None:
            complete = False
            continue

        print(f"\nStation: {station.get('name', station_id)}")

        try:
            measurements = fetch_latest_for_location(station_id)

            for item in measurements:
                if not should_continue():
                    return False
                sensor_id = item.get("sensorsId")

                if sensor_id is None:
                    continue

                details = get_sensor_details(sensor_id, sensor_cache)

                if not details:
                    print("Skipping sensor:", sensor_id, "- metadata unavailable")
                    continue

                parameter_info = details.get("parameter") or {}
                parameter = parameter_info.get("name", "").lower()
                units = parameter_info.get("units")

                if parameter not in SUPPORTED_PARAMETERS:
                    continue

                timestamp = (item.get("datetime") or {}).get("utc")
                coordinates = item.get("coordinates") or {}

                if not timestamp:
                    print(f"{parameter}: REJECTED - Missing timestamp")
                    rejected_count += 1
                    continue

                measurement = {
                    "value": item.get("value"),
                    "units": units,
                    "period": {
                        "datetimeFrom": {"utc": timestamp},
                    },
                }

                valid, result = validate_measurement(
                    measurement,
                    parameter,
                    max_age_hours=MAX_AGE_HOURS,
                )

                if not valid:
                    print(f"{parameter}: REJECTED - {result}")
                    rejected_count += 1
                    continue

                latitude = coordinates.get("latitude")
                longitude = coordinates.get("longitude")

                if latitude is None or longitude is None:
                    print(f"{parameter}: REJECTED - Missing coordinates")
                    rejected_count += 1
                    continue

                try:
                    document = normalize_pollution_observation(
                        station_id=station_id,
                        coordinates=[longitude, latitude],
                        parameter=parameter,
                        validated_measurement=result,
                        raw_data=item,
                    )
                except (TypeError, ValueError) as error:
                    print(f"{parameter}: REJECTED - {error}")
                    rejected_count += 1
                    continue

                inserted_id = save_normalized_pollution_observation(
                    database,
                    document,
                )

                saved_count += 1
                print(
                    f"{parameter}: SAVED | {result['value']} {units} "
                    f"| ID: {inserted_id}"
                )

        except HTTPError as error:
            if (
                error.response is not None
                and error.response.status_code == 429
            ):
                print("OpenAQ rate limit reached. Stopping station requests.")
                complete = False
                break

            print("Station processing failed:", safe_error(error))
            complete = False

        except Exception as error:
            print("Station processing failed:", safe_error(error))
            complete = False

        if station_index < len(selected_stations) - 1:
            time.sleep(2)

    print("\nPollution observations saved:", saved_count)
    print("Pollution observations rejected:", rejected_count)
    return complete




def ingest_openmeteo_air_quality(database, should_continue=lambda: True):
    print("\n4. Fetching modeled PM2.5 across New Delhi...")

    if database is None:
        print("Open-Meteo readings not saved: MongoDB unavailable.")
        return False

    latitudes = [28.40, 28.50, 28.60, 28.70, 28.80]
    longitudes = [76.90, 77.025, 77.15, 77.275, 77.40]

    saved_count = 0
    failed_count = 0
    total_count = len(latitudes) * len(longitudes)

    for latitude in latitudes:
        for longitude in longitudes:
            if not should_continue():
                return False
            try:
                reading = fetch_current_pm25(latitude, longitude)

                if reading is None:
                    failed_count += 1
                    continue

                validated = {
                    "value": reading["pm25"],
                    "units": reading["unit"],
                    "observed_at": reading["observedAt"],
                }

                document = normalize_pollution_observation(
                    station_id=(
                        f"openmeteo-new-delhi-{latitude:.3f}-{longitude:.3f}"
                    ),
                    coordinates=[
                        reading.get("longitude", longitude),
                        reading.get("latitude", latitude),
                    ],
                    parameter="pm25",
                    validated_measurement=validated,
                    raw_data=reading,
                    source=reading["source"],
                    data_type=reading["dataType"],
                )

                inserted_id = save_normalized_pollution_observation(
                    database,
                    document,
                )

                saved_count += 1
                print(
                    f"SAVED | ({latitude}, {longitude}) "
                    f"| PM2.5: {reading['pm25']} {reading['unit']} "
                    f"| ID: {inserted_id}"
                )

            except Exception as error:
                failed_count += 1
                print(
                    f"FAILED | ({latitude}, {longitude}) | {safe_error(error)}"
                )

    print("Open-Meteo observations saved:", saved_count)
    print("Locations that failed or returned no reading:", failed_count)
    return failed_count == 0 and saved_count == total_count



def estimate_road_segments(database, should_continue=lambda: True):
    print("\n5. Estimating pollution for road segments...")

    if database is None:
        print("Skipped: MongoDB unavailable.")
        return False

    try:
        roads_path = Path(__file__).resolve().parent / "data" / "new_delhi_roads.geojson"

        with roads_path.open("r", encoding="utf-8") as file:
            road_data = json.load(file)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=MAX_AGE_HOURS)

        # The bundled road data covers New Delhi. Include a ~6 km margin around
        # its source bounding box so nearby stations remain available to IDW.
        west, south, east, north = 77.18 - 0.06, 28.60 - 0.06, 77.24 + 0.06, 28.64 + 0.06
        observation_cursor = database["pollution_observations"].find(
                {
                    "pm25": {"$gte": 0},
                    "observedAt": {
                        "$gte": cutoff,
                        "$lte": now,
                    },
                    "location": {"$geoWithin": {"$geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [west, south], [east, south], [east, north],
                            [west, north], [west, south],
                        ]],
                    }}},
                },
                {
                    "_id": 0,
                    "pm25": 1,
                    "observedAt": 1,
                    "location": 1,
                    "source": 1,
                    "dataType": 1,
                },
            ).sort("observedAt", -1).limit(ESTIMATION_OBSERVATION_LIMIT).max_time_ms(10_000)
        observations = list(observation_cursor)

        print("Fresh PM2.5 observations:", len(observations))

        if not observations:
            print("No estimates generated: fresh PM2.5 observations unavailable.")
            return False

        batch = []
        batch_size = 500
        estimated_count = 0
        total_roads = len(road_data.get("features", []))
        observation_index = build_observation_index(observations)

        for index, feature in enumerate(road_data.get("features", []), start=1):
            if not should_continue():
                return False
            estimate = estimate_road_segment(
                feature, observations, observation_index=observation_index
            )

            if estimate is not None:
                batch.append(estimate)

            if len(batch) >= batch_size:
                save_road_segment_estimates_bulk(database, batch)
                estimated_count += len(batch)
                batch.clear()
                print(f"Processed {index}/{total_roads} roads")

        if batch:
            save_road_segment_estimates_bulk(database, batch)
            estimated_count += len(batch)

        print("Road-segment estimates processed and saved:", estimated_count)
        return estimated_count > 0

    except Exception as error:
        print("Road estimation/storage failed:", safe_error(error))
        return False



def run_scheduled_pass(database, shutdown=None):
    global last_database_success
    lock_id = "pipeline-a-ingestion"
    owner_token = str(uuid.uuid4())
    lease_seconds = max(60, int(os.getenv("PIPELINE_LOCK_LEASE_SECONDS", "120")))
    interval_seconds = max(60, int(os.getenv("PIPELINE_INTERVAL_SECONDS", "600")))

    try:
        acquired = acquire_pipeline_lock(
            database,
            lock_id,
            owner_token,
            lease_seconds,
        )
        last_database_success = time.monotonic()
    except Exception as error:
        print("Could not acquire Pipeline A run lock:", safe_error(error))
        return

    if not acquired:
        return

    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()

    def renew_lock():
        global last_database_success
        heartbeat_seconds = max(5, min(30, lease_seconds // 3))
        while not stop_heartbeat.wait(heartbeat_seconds):
            try:
                if not renew_pipeline_lock(
                    database, lock_id, owner_token, lease_seconds
                ):
                    print("Pipeline A run lock renewal failed; check MongoDB connectivity.")
                    lease_lost.set()
                    return
                last_database_success = time.monotonic()
            except Exception as error:
                print("Pipeline A run lock renewal failed:", safe_error(error))
                lease_lost.set()
                return

    heartbeat = threading.Thread(
        target=renew_lock, name="pipeline-a-lock-heartbeat", daemon=True
    )
    heartbeat.start()

    try:
        print("\n========== SAFEROUTE PIPELINE A ==========\n")
        should_continue = lambda: not lease_lost.is_set() and not (shutdown and shutdown.is_set())
        stages = (
            ("weather", lambda: ingest_weather(database, should_continue)),
            ("OpenAQ", lambda: ingest_pollution(database, should_continue)),
            ("Open-Meteo air quality", lambda: ingest_openmeteo_air_quality(database, should_continue)),
            ("road estimates", lambda: estimate_road_segments(database, should_continue)),
        )
        outcomes = []
        for stage_name, stage in stages:
            if lease_lost.is_set() or (shutdown and shutdown.is_set()):
                print("Aborting pass because the lease was lost or shutdown was requested.")
                break
            succeeded = stage()
            outcomes.append((stage_name, succeeded))
            if not succeeded:
                print(f"Pipeline stage incomplete: {stage_name}")

        if not lease_lost.is_set() and outcomes and all(ok for _name, ok in outcomes):
            completed = mark_pipeline_lock_completed(
                database, lock_id, owner_token, interval_seconds
            )
            if completed:
                print("\n========== PIPELINE RUN COMPLETE ==========")
            else:
                print("Pipeline pass ended, but could not confirm the completion lease.")
        elif not lease_lost.is_set() and not (shutdown and shutdown.is_set()):
            mark_pipeline_lock_failed(database, lock_id, owner_token, retry_seconds=60)
            print("Pipeline pass was partial; it will retry after the backoff interval.")
        elif lease_lost.is_set():
            print("Pipeline pass stopped after losing lease ownership; it was not marked complete.")
        else:
            print("Pipeline pass stopped for shutdown; it was not marked complete.")
    except Exception as error:
        print("Pipeline A pass failed:", safe_error(error))
        try:
            mark_pipeline_lock_failed(database, lock_id, owner_token, retry_seconds=60)
        except Exception as lock_error:
            print("Could not record failed Pipeline A pass:", safe_error(lock_error))
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=2)
        try:
            release_pipeline_lock(database, lock_id, owner_token)
        except Exception as error:
            print("Could not release Pipeline A run lock:", safe_error(error))


def start_health_server():
    global last_database_success
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/health", "/health/ready"):
                self.send_error(404)
                return
            ready = last_database_success > 0 and time.monotonic() - last_database_success <= 90
            is_readiness = self.path == "/health/ready"
            status = 200 if not is_readiness or ready else 503
            state = "ready" if ready else "not-ready"
            body = json.dumps({
                "status": state if is_readiness else "alive",
                "service": "pipeline-a",
                "database": "connected" if ready else "unknown",
            }).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    port = int(os.getenv("PIPELINE_HEALTH_PORT", "8081"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(
        target=server.serve_forever, name="pipeline-a-health", daemon=True
    ).start()


def main():
    global last_database_success
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: shutdown.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: shutdown.set())
    start_health_server()
    print("1. Connecting to MongoDB...")
    try:
        database = connect_mongodb()
        last_database_success = time.monotonic()
        print("Database ready for storage.")
    except Exception as error:
        print("MongoDB connection failed:", safe_error(error))
        raise

    interval_seconds = max(60, int(os.getenv("PIPELINE_INTERVAL_SECONDS", "600")))
    poll_seconds = max(5, int(os.getenv("PIPELINE_LOCK_POLL_SECONDS", "15")))
    print(
        f"Pipeline A worker active; polling the MongoDB lease every {poll_seconds}s "
        f"and waiting {interval_seconds}s after each completed pass before refreshing."
    )
    while not shutdown.is_set():
        run_scheduled_pass(database, shutdown)
        shutdown.wait(poll_seconds)


if __name__ == "__main__":
    main()

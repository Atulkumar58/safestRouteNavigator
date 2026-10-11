
import json
import os
import time
from pathlib import Path

from ingestion.measurements import fetch_sensor_details

CACHE_PATH = Path(os.getenv("PIPELINE_SENSOR_CACHE_PATH") or (Path(__file__).resolve().parent / "sensor_metadata.json"))
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_sensor_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return {}


def get_sensor_details(sensor_id, cache):
    key = str(sensor_id)

    if key in cache:
        return cache[key]

    # Brief pause between metadata requests.
    time.sleep(1)

    details = fetch_sensor_details(sensor_id)

    if details:
        cache[key] = details
        CACHE_PATH.write_text(
            json.dumps(cache, indent=2),
            encoding="utf-8"
        )

    return details


import os
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient, UpdateOne, ReturnDocument
from pymongo.errors import DuplicateKeyError

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

def connect_mongodb():
    mongo_url = os.getenv("MONGO_URL") or os.getenv("MONGODB_URI")

    if not mongo_url:
        raise ValueError("MONGO_URL or MONGODB_URI is missing from Pipeline-A/.env")

    client = None
    for attempt in range(5):
        client = MongoClient(
            mongo_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=15000,
            maxIdleTimeMS=30000,
            maxPoolSize=10,
            waitQueueTimeoutMS=10000,
        )
        try:
            client.admin.command("ping")
            break
        except Exception as error:
            client.close()
            if attempt == 4:
                raise
            delay = min(2 ** attempt, 15)
            print(f"MongoDB connection attempt {attempt + 1} failed ({type(error).__name__}); retrying in {delay}s")
            time.sleep(delay)
    database = client.get_default_database()

    if database is None:
        client.close()
        raise ValueError("Database name is missing from MONGO_URL")

    print("MongoDB connection successful!")
    return database


def acquire_pipeline_lock(database, lock_id, owner_token, lease_seconds=120):
    """Acquire a renewable lease only when a scheduled ingestion pass is due."""
    now = datetime.now(timezone.utc)
    try:
        lock = database["pipeline_locks"].find_one_and_update(
            {
                "_id": lock_id,
                "$and": [
                    {"$or": [
                        {"expiresAt": {"$lte": now}},
                        {"expiresAt": {"$exists": False}},
                    ]},
                    {"$or": [
                        {"nextRunAt": {"$lte": now}},
                        {"nextRunAt": {"$exists": False}},
                    ]},
                ],
            },
            {
                "$set": {
                    "ownerToken": owner_token,
                    "expiresAt": now + timedelta(seconds=lease_seconds),
                    "lastStartedAt": now,
                    "nextRunAt": now,
                    "updatedAt": now,
                },
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # The lock exists and belongs to a process whose lease is still active.
        return False
    return bool(lock and lock.get("ownerToken") == owner_token)


def renew_pipeline_lock(database, lock_id, owner_token, lease_seconds=120):
    now = datetime.now(timezone.utc)
    result = database["pipeline_locks"].update_one(
        {"_id": lock_id, "ownerToken": owner_token},
        {"$set": {
            "expiresAt": now + timedelta(seconds=lease_seconds),
            "updatedAt": now,
        }},
    )
    return result.modified_count == 1


def release_pipeline_lock(database, lock_id, owner_token):
    now = datetime.now(timezone.utc)
    database["pipeline_locks"].update_one(
        {"_id": lock_id, "ownerToken": owner_token},
        {"$set": {"expiresAt": now, "updatedAt": now}, "$unset": {"ownerToken": ""}},
    )


def mark_pipeline_lock_completed(database, lock_id, owner_token, interval_seconds=600):
    now = datetime.now(timezone.utc)
    result = database["pipeline_locks"].update_one(
        {"_id": lock_id, "ownerToken": owner_token},
        {"$set": {
            "lastCompletedAt": now,
            "nextRunAt": now + timedelta(seconds=max(60, interval_seconds)),
            "updatedAt": now,
            "lastStatus": "completed",
        }},
    )
    return result.modified_count == 1


def mark_pipeline_lock_failed(database, lock_id, owner_token, retry_seconds=60, error_code=None):
    now = datetime.now(timezone.utc)
    result = database["pipeline_locks"].update_one(
        {"_id": lock_id, "ownerToken": owner_token},
        {"$set": {
            "lastFailedAt": now,
            "nextRunAt": now + timedelta(seconds=max(30, retry_seconds)),
            "updatedAt": now,
            "lastStatus": "failed",
            "lastErrorCode": error_code or "PIPELINE_PASS_INCOMPLETE",
        }},
    )
    return result.modified_count == 1


def save_weather_observation(database, document):
    now = datetime.now(timezone.utc)
    document.setdefault("createdAt", now)
    document["updatedAt"] = now
    result = database["weather_observations"].update_one(
        {
            "source": document["source"],
            "observedAt": document["observedAt"],
            "location.coordinates": document["location"]["coordinates"],
        },
        {"$set": {key: value for key, value in document.items() if key != "createdAt"},
         "$setOnInsert": {"createdAt": document["createdAt"]}},
        upsert=True,
    )
    return result.upserted_id or "updated existing observation"


def save_pollution_observation(database, station_id, location, measurement):
    parameter = measurement["parameter"].lower()

    supported_parameters = {
        "pm25", "pm10", "no2", "o3", "so2", "co"
    }

    if parameter not in supported_parameters:
        raise ValueError(f"Unsupported pollution parameter: {parameter}")

    observed_at = measurement["observed_at"]
    if isinstance(observed_at, str):
        observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))

    document = {
        "source": "openaq",
        "stationId": str(station_id),
        "observedAt": observed_at,
        "location": {
            "type": "Point",
            "coordinates": location
        },
        parameter: measurement["value"],
        "units": measurement.get("units"),
        "rawData": measurement,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc)
    }

    return save_normalized_pollution_observation(database, document)

def save_normalized_pollution_observation(database, document):
    pollutant_fields = {"pm25", "pm10", "no2", "o3", "so2", "co"}
    present_fields = pollutant_fields.intersection(document)
    if len(present_fields) != 1:
        raise ValueError("A pollution observation must contain exactly one pollutant field")

    now = datetime.now(timezone.utc)
    document.setdefault("createdAt", now)
    document["updatedAt"] = now
    field = next(iter(present_fields))
    document.setdefault("parameter", field)
    result = database["pollution_observations"].update_one(
        {
            "source": document["source"],
            "stationId": document["stationId"],
            "observedAt": document["observedAt"],
            "parameter": field,
        },
        {"$set": {key: value for key, value in document.items() if key != "createdAt"},
         "$setOnInsert": {"createdAt": document["createdAt"]}},
        upsert=True,
    )
    return result.upserted_id or "updated existing observation"



def save_road_segment_estimate(database, document):
    now = datetime.now(timezone.utc)
    values = dict(document)
    values["updatedAt"] = now
    created_at = values.pop("createdAt", now)
    result = database["road_segment_estimates"].update_one(
        {"segmentId": document["segmentId"]},
        {"$set": values, "$setOnInsert": {"createdAt": created_at}},
        upsert=True,
    )
    return result.upserted_id or "updated existing estimate"


def save_road_segment_estimates_bulk(database, documents):
    if not documents:
        return 0

    now = datetime.now(timezone.utc)
    operations = []
    for document in documents:
        values = dict(document)
        values["updatedAt"] = now
        created_at = values.pop("createdAt", now)
        operations.append(UpdateOne(
            {"segmentId": document["segmentId"]},
            {"$set": values, "$setOnInsert": {"createdAt": created_at}},
            upsert=True,
        ))

    result = database["road_segment_estimates"].bulk_write(
        operations,
        ordered=False,
    )

    return result.upserted_count + result.modified_count


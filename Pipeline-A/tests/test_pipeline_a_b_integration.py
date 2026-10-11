"""Cross-language integration test; requires a disposable MongoDB test URI."""

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pymongo import MongoClient

from processing.estimator import estimate_road_segment
from processing.normalizer import normalize_pollution_observation
from processing.validator import validate_measurement
from storage.mongodb import save_normalized_pollution_observation, save_road_segment_estimates_bulk


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_RUNNER = Path(__file__).with_name("pipeline_b_consumer.js")
TEST_MONGODB_URI = os.getenv("SAFEROUTE_TEST_MONGODB_URI")


@pytest.mark.integration
@pytest.mark.skipif(not TEST_MONGODB_URI, reason="Set SAFEROUTE_TEST_MONGODB_URI to run MongoDB integration")
def test_pipeline_a_estimate_is_consumed_and_persisted_by_pipeline_b():
    database_name = f"saferoute_pipeline_test_{uuid.uuid4().hex}"
    client = MongoClient(TEST_MONGODB_URI, serverSelectionTimeoutMS=5000)
    database = client[database_name]
    feature = {
        "type": "Feature",
        "properties": {"segmentId": "integration-road-1", "roadName": "Integration Road"},
        "geometry": {
            "type": "LineString",
            "coordinates": [[77.209, 28.613], [77.209, 28.614]],
        },
    }

    try:
        now = datetime.now(timezone.utc)
        valid, validated = validate_measurement(
            {
                "value": 17.97,
                "units": "µg/m³",
                "period": {"datetimeFrom": {"utc": now.isoformat()}},
            },
            "pm25",
        )
        assert valid
        raw_observation = normalize_pollution_observation(
            station_id="integration-station-1",
            coordinates=[77.209, 28.6135],
            parameter="pm25",
            validated_measurement=validated,
            raw_data={"value": 17.97, "units": "µg/m³"},
        )
        save_normalized_pollution_observation(database, raw_observation)
        save_normalized_pollution_observation(database, raw_observation)
        assert database["pollution_observations"].count_documents({"stationId": "integration-station-1"}) == 1

        observations = list(database["pollution_observations"].find({"pm25": {"$gte": 0}}))
        estimate = estimate_road_segment(feature, observations)
        assert estimate is not None
        assert estimate["estimatedPm25"] == pytest.approx(17.97)
        assert estimate["estimatedAqi"] is not None
        assert estimate["geometry"]["type"] == "LineString"
        save_road_segment_estimates_bulk(database, [estimate])

        node_env = os.environ.copy()
        node_env.update(
            {
                "MONGODB_URI": TEST_MONGODB_URI,
                "MONGODB_DB": database_name,
                "ROUTING_PROVIDER": "osrm",
                "OSRM_URL": "https://routing.invalid",
            }
        )
        node_env.pop("WEATHER_API_URL", None)
        node_env.pop("WEATHER_API_KEY", None)
        completed = subprocess.run(
            ["node", str(NODE_RUNNER)],
            cwd=PROJECT_ROOT,
            env=node_env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "Pipeline B consumed Pipeline A road estimate and raw observation" in completed.stdout

        stored_request = database["route_requests"].find_one({"status": "completed"})
        stored_result = database["route_results"].find_one({"routeRequest": stored_request["_id"]})
        assert stored_request["travelMode"] == "walking"
        assert stored_result["routes"][0]["averagePm25"] == pytest.approx(17.97)
        assert stored_result["routes"][0]["isRecommended"] is True
    finally:
        client.drop_database(database_name)
        client.close()

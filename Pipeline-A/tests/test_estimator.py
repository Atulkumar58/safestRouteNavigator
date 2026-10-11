
from datetime import datetime, timezone, timedelta

from processing.estimator import estimate_road_segment


NOW = datetime.now(timezone.utc)

FEATURE = {
    "type": "Feature",
    "properties": {
        "segmentId": "test-road-1",
        "roadName": "Test Road",
    },
    "geometry": {
        "type": "LineString",
        "coordinates": [
            [77.2090, 28.6130],
            [77.2090, 28.6140],
        ],
    },
}


def make_observation(
    pm25=40.0,
    lon=77.2090,
    lat=28.6135,
    observed_at=None,
    data_type="measured",
):
    return {
        "pm25": pm25,
        "location": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
        "observedAt": observed_at or NOW,
        "source": "test",
        "dataType": data_type,
    }


def test_valid_observation_generates_estimate():
    result = estimate_road_segment(
        FEATURE,
        [make_observation()],
    )

    assert result is not None
    assert result["segmentId"] == "test-road-1"
    assert result["estimatedPm25"] == 40.0
    assert 0 <= result["confidence"] <= 1


def test_stale_observation_is_rejected():
    observation = make_observation(
        observed_at=NOW - timedelta(hours=25)
    )

    assert estimate_road_segment(FEATURE, [observation]) is None


def test_missing_measurement_is_rejected():
    observation = make_observation()
    observation["pm25"] = None

    assert estimate_road_segment(FEATURE, [observation]) is None


def test_invalid_coordinates_are_rejected():
    observation = make_observation(lon=999)

    assert estimate_road_segment(FEATURE, [observation]) is None


def test_distant_observation_is_rejected():
    observation = make_observation(lon=80, lat=30)

    assert estimate_road_segment(FEATURE, [observation]) is None


def test_modeled_data_has_reduced_confidence():
    measured = estimate_road_segment(
        FEATURE,
        [make_observation(data_type="measured")],
    )

    modeled = estimate_road_segment(
        FEATURE,
        [make_observation(data_type="modelled")],
    )

    assert measured is not None
    assert modeled is not None
    assert modeled["confidence"] < measured["confidence"]

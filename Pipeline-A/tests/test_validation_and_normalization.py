from datetime import datetime, timedelta, timezone

import pytest

from processing.normalizer import normalize_pollution_observation
from processing.validator import validate_measurement


def measurement(value=17.97, units="µg/m³", timestamp=None):
    return {
        "value": value,
        "units": units,
        "period": {
            "datetimeFrom": {
                "utc": (timestamp or datetime.now(timezone.utc)).isoformat(),
            },
        },
    }


def test_accepts_pipeline_a_openaq_pm25_sample():
    valid, result = validate_measurement(measurement(), "pm25")

    assert valid
    assert result["value"] == pytest.approx(17.97)
    assert result["units"] == "µg/m³"
    assert isinstance(result["observed_at"], datetime)


def test_converts_pm25_milligrams_to_micrograms():
    valid, result = validate_measurement(measurement(0.01797, "mg/m³"), "pm25")

    assert valid
    assert result["value"] == pytest.approx(17.97)
    assert result["units"] == "µg/m³"


@pytest.mark.parametrize(
    "bad_measurement,parameter",
    [
        (measurement(-1), "pm25"),
        (measurement(units="ppm"), "pm25"),
        (measurement(timestamp=datetime.now(timezone.utc) + timedelta(hours=1)), "pm25"),
        (measurement(), "unknown"),
    ],
)
def test_rejects_invalid_measurements(bad_measurement, parameter):
    valid, _ = validate_measurement(bad_measurement, parameter)
    assert not valid


def test_normalizer_preserves_geojson_order_and_units():
    valid, result = validate_measurement(measurement(), "pm25")
    assert valid

    document = normalize_pollution_observation(
        station_id=3409361,
        coordinates=[81.578979, 21.258815],
        parameter="pm25",
        validated_measurement=result,
        raw_data={"value": 17.97},
    )

    assert document["source"] == "openaq"
    assert document["stationId"] == "3409361"
    assert document["location"] == {
        "type": "Point",
        "coordinates": [81.578979, 21.258815],
    }
    assert document["pm25"] == pytest.approx(17.97)
    assert document["units"] == "µg/m³"
    assert document["createdAt"].tzinfo is not None
    assert document["updatedAt"].tzinfo is not None


@pytest.mark.parametrize("coordinates", [[181, 0], [0, 91], ["bad", 0], [0]])
def test_normalizer_rejects_invalid_coordinates(coordinates):
    valid, result = validate_measurement(measurement(), "pm25")
    assert valid

    with pytest.raises(ValueError):
        normalize_pollution_observation(
            station_id="bad-coordinate",
            coordinates=coordinates,
            parameter="pm25",
            validated_measurement=result,
            raw_data={},
        )

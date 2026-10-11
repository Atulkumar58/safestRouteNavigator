
from datetime import datetime, timezone
import math

SUPPORTED_PARAMETERS = {"pm25", "pm10", "no2", "o3", "so2", "co"}
PM25_MICROGRAM_UNITS = {
    "µg/m³", "μg/m³", "ug/m³", "µg/m3", "μg/m3", "ug/m3",
    "µg/m^3", "μg/m^3", "ug/m^3",
}
PM25_MILLIGRAM_UNITS = {"mg/m³", "mg/m3", "mg/m^3"}


def validate_measurement(measurement, parameter, max_age_hours=24):
    value = measurement.get("value")
    units = measurement.get("units")
    period = measurement.get("period") or {}

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False, "Invalid or missing numeric value"

    if not math.isfinite(value):
        return False, "Value is not finite"

    if not parameter:
        return False, "Missing parameter"

    parameter = parameter.strip().lower()

    if parameter not in SUPPORTED_PARAMETERS:
        return False, "Unsupported pollutant parameter"

    if value < 0:
        return False, "Pollutant concentration cannot be negative"

    datetime_from = period.get("datetimeFrom") or {}
    timestamp = datetime_from.get("utc")

    if not timestamp:
        return False, "Missing observation timestamp"

    try:
        observed_at = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        )

        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)

        observed_at = observed_at.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return False, "Invalid observation timestamp"

    age_hours = (
        datetime.now(timezone.utc) - observed_at
    ).total_seconds() / 3600

    if age_hours < 0:
        return False, "Observation timestamp is in the future"

    if age_hours > max_age_hours:
        return False, f"Observation is too old ({age_hours:.1f} hours)"

    if not isinstance(units, str) or not units.strip():
        return False, "Missing measurement units"

    if parameter == "pm25":
        normalized_units = units.strip().lower().replace(" ", "")
        if normalized_units in PM25_MICROGRAM_UNITS:
            units = "µg/m³"
        elif normalized_units in PM25_MILLIGRAM_UNITS:
            value *= 1000
            units = "µg/m³"
        else:
            return False, f"Unsupported PM2.5 units: {units}"

    cleaned = {
        "parameter": parameter,
        "value": float(value),
        "units": units,
        "observed_at": observed_at
    }

    return True, cleaned

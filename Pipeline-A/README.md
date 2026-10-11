# Pipeline A: data ingestion worker

Pipeline A fetches weather and air-quality observations, validates and stores them in MongoDB, then estimates pollution for New Delhi road segments. On EC2 it runs as the `pipeline-a` Docker Compose service beside the Node.js backend. Both services use the same MongoDB database.

## Local run

Install dependencies with `python -m pip install -r requirements.txt`, copy `.env.example` to `.env`, set `OPENAQ_API_KEY` and `MONGODB_URI`, and run `python main.py`.

The worker keeps running. It waits 10 minutes after a complete pass (`PIPELINE_INTERVAL_SECONDS=600`) and retries an incomplete pass after 60 seconds. A renewable MongoDB lease prevents two workers from performing the same pass at once. SIGTERM and SIGINT are handled as graceful shutdowns.

The health endpoint is `http://localhost:8081/health/ready` when `PIPELINE_HEALTH_PORT` is 8081. It reports readiness only after a recent database success.

## Data and testing

- `weather_observations`: current weather records.
- `pollution_observations`: pollutant readings with GeoJSON Point coordinates in `[longitude, latitude]` order.
- `road_segment_estimates`: per-road PM2.5, estimated AQI, confidence, and source timestamps.

Install test dependencies from `requirements-dev.txt` and run `python -m pytest -q`. The cross-pipeline integration test requires `SAFEROUTE_TEST_MONGODB_URI` for a disposable test database; it creates and drops a uniquely named database. The test mocks external routing.

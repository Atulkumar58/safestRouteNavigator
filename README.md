# SafeRoute Navigator

![SafeRoute Navigator data flow](./WhatsApp%20Image%202026-10-09%20at%206.24.57%20PM.jpeg)

SafeRoute Navigator is an environmental route-planning system that helps users compare route options using travel time, distance, air-pollution exposure, data freshness, and confidence. The system is designed around a Flutter Android app, a Node.js/Express backend, a scheduled Python ingestion worker, MongoDB Atlas, and external environmental and routing services.

## Project vision

The mobile user selects an origin, destination, travel mode, and route preferences. SafeRoute then generates several route options and ranks them according to the user's priorities:

- **Fastest** route
- **Lower-exposure** route
- **Balanced** route

The result includes route geometry for map display, estimated travel time and distance, pollution exposure, data freshness, and confidence.

## Data flow

The system shown in the architecture diagram has two complementary pipelines.

### Pipeline A: scheduled environmental data ingestion

Pipeline A runs periodically, for example every 5–15 minutes:

1. Fetches the latest data from external sources:
   - CPCB real-time AQI
   - OpenAQ global air-quality data
   - Weather API meteorological data
2. Validates timestamps, units, coordinates, and measurement values.
3. Normalizes valid observations into a consistent format.
4. Stores raw observations in MongoDB Atlas:
   - `pollution_observations`
   - `weather_observations`
5. Estimates pollution levels for road segments using available observations, weather, and other model inputs.
6. Stores estimates in `road_segment_estimates` with a validity period and confidence score.

### Pipeline B: on-demand route processing

Pipeline B runs in the Express API when the mobile app submits a route request:

1. Receives the origin, destination, travel mode, and preferences.
2. Validates the request and authenticates the user.
3. Requests route alternatives from Valhalla/OSRM using OpenStreetMap road data.
4. Reads nearby road pollution estimates from MongoDB.
5. Scores each route using estimated exposure, concentration, and time spent on affected road segments.
6. Ranks the alternatives as fastest, lower exposure, or balanced.
7. Stores the request and computed results in:
   - `route_requests`
   - `route_results`
8. Returns route geometry, travel time, distance, estimated exposure, freshness, and confidence to the app.

## Architecture

```text
Flutter Android App
        |
        | route request / route response
        v
Node.js + Express API (Pipeline B)
        |                         ^
        | route geometry           | road estimates
        v                         |
Valhalla / OSRM            MongoDB Atlas
                                  ^
                                  |
                    Python ingestion worker (Pipeline A)
                                  ^
                                  |
             CPCB + OpenAQ + Weather API
```

### Main components

| Component | Responsibility |
| --- | --- |
| Flutter Android app | Lets users select locations, travel mode, and preferences; displays route alternatives and air-quality information. |
| Pipeline B / Express API | Authenticates requests, obtains routes, calculates exposure, ranks alternatives, and formats responses. |
| Pipeline A / Python worker | Periodically fetches, validates, normalizes, and estimates environmental data. |
| MongoDB Atlas | Stores observations, road-segment estimates, route requests, and route results. |
| Valhalla / OSRM | Generates route alternatives from OpenStreetMap road data. |
| CPCB, OpenAQ, Weather API | Provide air-quality and weather inputs. |
| AWS EC2 + Docker Compose | Runs the API and ingestion worker together in a deployable environment. |

## Repository structure

```text
.
├── src/                         # Node.js / Express route API
│   ├── routes/                  # HTTP endpoints
│   ├── services/                # Routing, scoring, database, and capacity logic
│   └── Models/                  # MongoDB models
├── Pipeline-A/                  # Python ingestion and estimation worker
│   ├── ingestion/               # External data fetchers
│   ├── processing/              # Validation, normalization, and estimation
│   ├── storage/                 # MongoDB persistence
│   ├── data/                    # Road data
│   └── tests/                   # Worker and integration tests
├── scripts/                     # Index, migration, and deployment scripts
├── compose.yaml                 # Backend and Pipeline A services
├── Dockerfile                   # Backend container image
└── .secrets/*.env.example       # Configuration templates
```

> The architecture diagram includes the Flutter Android client. This repository contains the backend and ingestion services that the client connects to; the Flutter application itself is maintained separately.

## Requirements

- Node.js 18 or newer
- Python 3.10 or newer
- Docker Desktop and Docker Compose, or local Node.js/Python runtimes
- MongoDB Atlas or another MongoDB deployment
- An OpenAQ API key
- Access to a routing provider such as Valhalla or OSRM

## Configuration

Never commit real credentials. Create local secret files from the templates:

```powershell
Copy-Item .secrets\backend.env.example .secrets\backend.env
Copy-Item .secrets\pipeline-a.env.example .secrets\pipeline-a.env
```

Set the MongoDB connection string in both files:

```env
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>/<database>
```

Set the Pipeline A key:

```env
OPENAQ_API_KEY=<your-open-aq-api-key>
```

Set a randomly generated Pipeline B token with at least 32 characters:

```env
API_BEARER_TOKEN=<random-secret-at-least-32-characters>
```

The default routing configuration is:

```env
ROUTING_PROVIDER=valhalla
VALHALLA_URL=https://valhalla1.openstreetmap.de
```

Allow the host running the services to connect to MongoDB in Atlas Network Access.

## Run with Docker Compose

Docker Compose starts the Express API and the Python ingestion worker together:

```powershell
docker compose build
docker compose run --rm backend node scripts/ensure-mongodb-indexes.js
docker compose up -d
```

Check the services and logs:

```powershell
docker compose ps
docker compose logs -f --tail=100
```

Endpoints:

- Backend API: `http://localhost:3000`
- Backend liveness: `http://localhost:3000/health`
- Backend readiness: `http://localhost:3000/health/ready`
- Pipeline A readiness: `http://localhost:8081/health/ready`

Stop the services:

```powershell
docker compose down
```

## Run locally

### Start Pipeline B

From the repository root:

```powershell
npm install
npm run db:ensure-indexes
npm start
```

The API listens on port `3000` by default.

### Start Pipeline A

Open a second terminal:

```powershell
cd Pipeline-A
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

Set `MONGODB_URI` and `OPENAQ_API_KEY` in `Pipeline-A/.env` before starting the worker. Pipeline A runs continuously and publishes readiness on port `8081`.

## Route API

### Health

```http
GET /health
GET /health/live
GET /health/ready
```

### Request routes

```http
POST /api/routes
Authorization: Bearer <API_BEARER_TOKEN>
Idempotency-Key: route-request-001
Content-Type: application/json
```

Example request body:

```json
{
  "locations": [
    { "lat": 28.6139, "lon": 77.2090 },
    { "lat": 28.5355, "lon": 77.3910 }
  ]
}
```

The `Idempotency-Key` must be unique for each logical request and may contain letters, numbers, `.`, `_`, `:`, and `-`. Keep the bearer token on a trusted server and never expose it in a public Flutter or browser bundle.

## Testing

Run Pipeline A tests:

```powershell
cd Pipeline-A
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The cross-pipeline integration test requires `SAFEROUTE_TEST_MONGODB_URI` and uses a disposable test database.

## Deployment on AWS

The intended deployment in the architecture diagram is one AWS EC2 instance running both services with Docker Compose:

```sh
bash scripts/deploy-ec2-compose.sh
```

Before deploying:

1. Configure `.secrets/backend.env` and `.secrets/pipeline-a.env`.
2. Allow the EC2 egress address in MongoDB Atlas.
3. Restrict SSH and application ports with the EC2 security group.
4. Place HTTPS and authentication in a trusted reverse proxy before public exposure.
5. Use a routing provider with capacity appropriate for production traffic.

Docker Compose can restart containers on the same host, but it does not provide host-level failover.

## Limitations

- Route quality depends on the freshness, density, and coverage of environmental observations.
- Modeled pollution values are estimates and must not be presented as direct sensor measurements.
- Some roads may have no sufficiently recent estimate.
- Public routing and weather endpoints may have rate limits or availability constraints.
- A single EC2 deployment has a single-host failure boundary.

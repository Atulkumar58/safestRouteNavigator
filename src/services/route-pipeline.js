const { RoadSegmentEstimate, PollutionObservation, RouteRequest, RouteResult, WeatherObservation } = require('../Models');
const { createHash, randomUUID } = require('node:crypto');

const MAX_ROUTES = 3;
const MODES = new Set(['walking', 'cycling']);
const pointCache = new Map();
const POINT_CACHE_TTL_MS = 30_000;
const POINT_CACHE_MAX = 5_000;
const configuredLookupConcurrency = Number.parseInt(process.env.POLLUTION_LOOKUP_CONCURRENCY || '30', 10);
const MAX_LOOKUP_CONCURRENCY = Number.isFinite(configuredLookupConcurrency)
  ? Math.max(1, Math.min(100, configuredLookupConcurrency))
  : 30;
let activeLookups = 0;
const lookupQueue = [];
const upstreamCircuits = new Map();

async function withLookupSlot(operation) {
  if (activeLookups >= MAX_LOOKUP_CONCURRENCY) {
    await new Promise((resolve) => lookupQueue.push(resolve));
  } else {
    activeLookups += 1;
  }
  try {
    return await operation();
  } finally {
    const next = lookupQueue.shift();
    if (next) next();
    else activeLookups -= 1;
  }
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function coordinate(value, name) {
  if (!value || typeof value !== 'object' || value.lat === null || value.lon === null ||
      value.lat === undefined || value.lon === undefined || value.lat === '' || value.lon === '' ||
      typeof value.lat === 'boolean' || typeof value.lon === 'boolean') {
    throw httpError(400, `${name} must contain valid lat and lon values`);
  }
  const lat = Number(value && value.lat);
  const lon = Number(value && value.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    throw httpError(400, `${name} must contain valid lat and lon values`);
  }
  return { lat, lon };
}

function decodePolyline(encoded, precision = 6) {
  let index = 0, lat = 0, lon = 0;
  const factor = 10 ** precision;
  const points = [];
  while (index < encoded.length) {
    const deltas = [];
    for (let axis = 0; axis < 2; axis++) {
      let result = 0, shift = 0, byte;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20 && index <= encoded.length);
      deltas.push((result & 1) ? ~(result >> 1) : (result >> 1));
    }
    lat += deltas[0]; lon += deltas[1];
    points.push([lon / factor, lat / factor]);
  }
  return points;
}

async function fetchJson(url, options, upstream = 'routing') {
  const circuit = upstreamCircuits.get(upstream) || { failures: 0, openUntil: 0 };
  if (circuit.openUntil > Date.now()) {
    const error = httpError(503, `${upstream} provider is temporarily unavailable`);
    error.code = 'UPSTREAM_CIRCUIT_OPEN';
    throw error;
  }
  let lastError;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(url, { ...options, signal: AbortSignal.timeout(10_000) });
      if (!response.ok) {
        const error = httpError(response.status === 429 ? 503 : 502, `${upstream} provider returned HTTP ${response.status}`);
        error.upstreamStatus = response.status;
        error.code = response.status === 429 ? 'UPSTREAM_RATE_LIMITED'
          : response.status >= 500 ? 'UPSTREAM_BAD_RESPONSE' : 'UPSTREAM_REJECTED_REQUEST';
        if (response.status >= 500 && attempt === 0) {
          lastError = error;
          await new Promise((resolve) => setTimeout(resolve, 200));
          continue;
        }
        throw error;
      }
      upstreamCircuits.set(upstream, { failures: 0, openUntil: 0 });
      return await response.json();
    } catch (error) {
      if (error.status) {
        if (error.code === 'UPSTREAM_RATE_LIMITED' || error.upstreamStatus >= 500) {
          circuit.failures += 1;
          if (circuit.failures >= 3) circuit.openUntil = Date.now() + 30_000;
          upstreamCircuits.set(upstream, circuit);
        }
        throw error;
      }
      lastError = error;
      if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  const error = httpError(502, `${upstream} provider is unavailable`);
  error.code = lastError?.cause?.code || lastError?.code || 'UPSTREAM_CONNECTION_FAILED';
  circuit.failures += 1;
  if (circuit.failures >= 3) circuit.openUntil = Date.now() + 30_000;
  upstreamCircuits.set(upstream, circuit);
  console.error(JSON.stringify({ event: 'upstream_request_failed', upstream, code: error.code }));
  throw error;
}

async function getRoutes(origin, destination, mode) {
  const provider = (process.env.ROUTING_PROVIDER || 'valhalla').toLowerCase();
  if (provider === 'osrm') {
    const profile = mode === 'walking' ? 'walking' : 'cycling';
    const base = (process.env.OSRM_URL || 'https://router.project-osrm.org').replace(/\/$/, '');
    const url = `${base}/route/v1/${profile}/${origin.lon},${origin.lat};${destination.lon},${destination.lat}?alternatives=2&overview=full&geometries=geojson&steps=false`;
    const data = await fetchJson(url);
    if (data.code !== 'Ok' || !data.routes?.length) throw httpError(502, 'No route was returned by the routing service');
    return data.routes.slice(0, MAX_ROUTES).map((route, index) => ({
      geometry: route.geometry, distanceMeters: route.distance, durationSeconds: route.duration,
      provider, providerRouteId: String(index + 1),
    }));
  }

  const base = (process.env.VALHALLA_URL || 'https://valhalla1.openstreetmap.de').replace(/\/$/, '');
  const costing = mode === 'walking' ? 'pedestrian' : 'bicycle';
  const data = await fetchJson(`${base}/route`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      locations: [{ lat: origin.lat, lon: origin.lon }, { lat: destination.lat, lon: destination.lon }],
      costing, units: 'kilometers', alternates: MAX_ROUTES - 1, directions_options: { units: 'kilometers' },
    }),
  });
  const trips = [data.trip, ...(data.alternates || []).map((alternate) => alternate.trip)].filter(Boolean);
  if (!trips.length) throw httpError(502, 'No route was returned by the routing service');
  return trips.slice(0, MAX_ROUTES).map((trip, index) => ({
    geometry: { type: 'LineString', coordinates: trip.legs.flatMap((leg) => leg.shape ? decodePolyline(leg.shape, 6) : []) },
    distanceMeters: trip.summary.length * 1000,
    durationSeconds: trip.summary.time,
    provider, providerRouteId: String(index + 1),
  }));
}

function sampleCoordinates(coordinates, maxSamples = (() => {
  const configured = Number.parseInt(process.env.MAX_ROUTE_POLLUTION_SAMPLES || '20', 10);
  return Number.isFinite(configured) ? Math.max(1, Math.min(50, configured)) : 20;
})()) {
  if (!coordinates.length) return [];
  const stride = Math.max(1, Math.ceil(coordinates.length / maxSamples));
  const samples = coordinates.filter((_point, index) => index % stride === 0);
  if (samples[samples.length - 1] !== coordinates[coordinates.length - 1]) samples.push(coordinates[coordinates.length - 1]);
  return samples;
}

async function lookupPollutionAtPoint(lon, lat) {
  const key = `${lon.toFixed(4)}:${lat.toFixed(4)}`;
  const cached = pointCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;
  if (cached) pointCache.delete(key);

  const promise = (async () => {
    const estimateCutoff = new Date(Date.now() - Number(process.env.ESTIMATE_MAX_AGE_MINUTES || 30) * 60_000);
    const observationCutoff = new Date(Date.now() - Number(process.env.OBSERVATION_MAX_AGE_HOURS || 24) * 3_600_000);
    const maxDistance = Number(process.env.ESTIMATE_SEARCH_METERS || 150);
    const [estimate] = await withLookupSlot(() => RoadSegmentEstimate.aggregate([
      { $geoNear: {
        near: { type: 'Point', coordinates: [lon, lat] },
        distanceField: 'distanceMeters',
        maxDistance,
        spherical: true,
        query: { sourceObservedAt: { $gte: estimateCutoff } },
      } },
      { $sort: { estimatedAt: -1 } },
      { $limit: 1 },
    ]).option({ maxTimeMS: 5_000 }));
    if (estimate) return [estimate];

    return withLookupSlot(() => PollutionObservation.aggregate([
      { $geoNear: {
        near: { type: 'Point', coordinates: [lon, lat] },
        distanceField: 'distanceMeters',
        maxDistance,
        spherical: true,
        query: { observedAt: { $gte: observationCutoff, $lte: new Date() } },
      } },
      { $sort: { observedAt: -1 } },
      { $group: { _id: { $ifNull: ['$parameter', 'legacy'] }, observation: { $first: '$$ROOT' } } },
      { $replaceRoot: { newRoot: '$observation' } },
    ]).option({ maxTimeMS: 5_000 }));
  })();

  pointCache.set(key, { expiresAt: Date.now() + POINT_CACHE_TTL_MS, promise });
  if (pointCache.size > POINT_CACHE_MAX) pointCache.delete(pointCache.keys().next().value);
  try {
    return await promise;
  } catch (error) {
    pointCache.delete(key);
    throw error;
  }
}

async function getPollution(route) {
  const points = sampleCoordinates(route.geometry.coordinates);
  const found = await Promise.all(points.map(([lon, lat]) => lookupPollutionAtPoint(lon, lat)));
  const observations = found.flat();
  const estimates = observations;
  const average = (fields) => {
    const values = estimates.map((item) => {
      for (const field of fields) {
        const value = Number(item[field]);
        if (item[field] !== undefined && item[field] !== null && Number.isFinite(value)) return value;
      }
      return null;
    }).filter((value) => value !== null);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  };
  const score = average(['estimatedAqi', 'aqi']);
  const pm25 = average(['estimatedPm25', 'pm25']);
  const pm10 = average(['estimatedPm10', 'pm10']);
  const no2 = average(['estimatedNo2', 'no2']);
  const o3 = average(['estimatedO3', 'o3']);
  const so2 = average(['estimatedSo2', 'so2']);
  const co = average(['estimatedCo', 'co']);
  const exposureScore = average(['exposureScore']);
  const confidence = average(['confidence']);
  const observedAt = estimates.reduce((latest, item) => {
    const timestamp = item.sourceObservedAt || item.observedAt || item.estimatedAt;
    return timestamp && (!latest || new Date(timestamp) < new Date(latest)) ? timestamp : latest;
  }, null);
  const ageMinutes = observedAt ? Math.max(0, (Date.now() - new Date(observedAt).getTime()) / 60000) : null;
  const freshness = ageMinutes === null ? 'unavailable' : ageMinutes <= 30 ? 'fresh' : 'stale';
  const coverage = points.length ? found.filter((items) => items.length > 0).length / points.length : 0;
  return {
    aqi: score === null ? null : Math.round(score * 10) / 10,
    pm25: pm25 === null ? null : Math.round(pm25 * 100) / 100,
    pm10: pm10 === null ? null : Math.round(pm10 * 100) / 100,
    no2: no2 === null ? null : Math.round(no2 * 100) / 100,
    o3: o3 === null ? null : Math.round(o3 * 100) / 100,
    so2: so2 === null ? null : Math.round(so2 * 100) / 100,
    co: co === null ? null : Math.round(co * 100) / 100,
    exposureScore: exposureScore === null ? null : Math.round(exposureScore * 100) / 100,
    freshness, observedAt, coverage,
    confidence: confidence === null ? null : Math.round(confidence * 100) / 100,
  };
}

async function getWeather(origin, mode) {
  const cachedObservation = await WeatherObservation.findOne({
    location: { $near: {
      $geometry: { type: 'Point', coordinates: [origin.lon, origin.lat] },
      $maxDistance: Number(process.env.WEATHER_SEARCH_METERS || 50_000),
    } },
    observedAt: {
      $gte: new Date(Date.now() - Number(process.env.WEATHER_MAX_AGE_HOURS || 3) * 3_600_000),
      $lte: new Date(),
    },
  }).sort({ observedAt: -1 }).maxTimeMS(5_000).lean();
  if (cachedObservation) return cachedObservation;
  if (!process.env.WEATHER_API_URL || !process.env.WEATHER_API_KEY) return null;
  const url = new URL(process.env.WEATHER_API_URL);
  url.searchParams.set('lat', origin.lat);
  url.searchParams.set('lon', origin.lon);
  url.searchParams.set('key', process.env.WEATHER_API_KEY);
  url.searchParams.set('mode', mode);
  let data;
  try {
    data = await fetchJson(url.toString(), undefined, 'weather');
  } catch (error) {
    console.warn(JSON.stringify({ event: 'weather_provider_unavailable', code: error.code || 'UPSTREAM_ERROR' }));
    return null;
  }
  const numeric = (...keys) => {
    for (const key of keys) {
      const value = Number(data?.[key]);
      if (Number.isFinite(value)) return value;
    }
    return undefined;
  };
  const observedAt = data.observedAt || data.time || data.last_updated || new Date();
  await WeatherObservation.findOneAndUpdate({
    source: 'weather_api',
    location: { type: 'Point', coordinates: [origin.lon, origin.lat] },
    observedAt,
  }, {
    $set: {
    source: 'weather_api',
    observedAt,
    location: { type: 'Point', coordinates: [origin.lon, origin.lat] },
    temperatureC: numeric('temperatureC', 'temp_c'),
    humidityPercent: numeric('humidityPercent', 'humidity'),
    windSpeedKph: numeric('windSpeedKph', 'wind_kph'),
    windDirectionDegrees: numeric('windDirectionDegrees', 'wind_degree'),
    precipitationMm: numeric('precipitationMm', 'precip_mm'),
    conditions: typeof data?.conditions === 'string' ? data.conditions : undefined,
    rawData: data,
    },
  }, { upsert: true, new: true, setDefaultsOnInsert: true }).maxTimeMS(5_000);
  return data;
}

async function processRouteRequest(body, { idempotencyKey = randomUUID() } = {}) {
  const origin = coordinate(body.origin, 'origin');
  const destination = coordinate(body.destination, 'destination');
  const mode = String(body.mode || 'walking').toLowerCase();
  if (!MODES.has(mode)) throw httpError(400, 'mode must be walking or cycling');
  if (body.preferences !== undefined && (typeof body.preferences !== 'object' || Array.isArray(body.preferences) || body.preferences === null)) {
    throw httpError(400, 'preferences must be an object');
  }
  if (body.preferences?.prioritizeAirQuality !== undefined && typeof body.preferences.prioritizeAirQuality !== 'boolean') {
    throw httpError(400, 'preferences.prioritizeAirQuality must be a boolean');
  }
  const routePreference = body.preferences?.routePreference || (body.preferences?.prioritizeAirQuality === false ? 'fastest' : 'balanced');
  if (!['fastest', 'lower_exposure', 'balanced'].includes(routePreference)) {
    throw httpError(400, 'preferences.routePreference must be fastest, lower_exposure, or balanced');
  }
  const maxExtraDistancePercent = Number(body.preferences?.maxExtraDistancePercent ?? 20);
  if (!Number.isFinite(maxExtraDistancePercent) || maxExtraDistancePercent < 0 || maxExtraDistancePercent > 100) {
    throw httpError(400, 'preferences.maxExtraDistancePercent must be between 0 and 100');
  }
  const retentionDays = Math.max(1, Math.min(365, Number(process.env.ROUTE_DATA_RETENTION_DAYS || 30)));
  const expiresAt = new Date(Date.now() + retentionDays * 86_400_000);
  const requestHash = createHash('sha256').update(JSON.stringify({
    origin, destination, mode, routePreference, maxExtraDistancePercent,
  })).digest('hex');
  let request;
  try {
    request = await RouteRequest.create({
      idempotencyKey,
      requestHash,
      origin: { type: 'Point', coordinates: [origin.lon, origin.lat] },
      destination: { type: 'Point', coordinates: [destination.lon, destination.lat] },
      travelMode: mode,
      preferences: { prioritizeAirQuality: routePreference !== 'fastest', maxExtraDistancePercent, routePreference },
      status: 'processing',
      processingStartedAt: new Date(),
      expiresAt,
    });
  } catch (error) {
    if (error.code !== 11000) throw error;
    const existing = await RouteRequest.findOne({ idempotencyKey }).maxTimeMS(5_000);
    if (!existing) throw error;
    if (existing.requestHash && existing.requestHash !== requestHash) {
      const conflict = httpError(409, 'Idempotency-Key was already used with a different request');
      conflict.code = 'IDEMPOTENCY_KEY_REUSED';
      throw conflict;
    }
    if (existing.status === 'completed' && existing.response) return existing.response;
    const staleBefore = new Date(Date.now() - Number(process.env.IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS || 180) * 1000);
    if (existing.status === 'processing' && existing.updatedAt > staleBefore) {
      const busy = httpError(409, 'A request with this Idempotency-Key is still processing');
      busy.code = 'IDEMPOTENCY_IN_PROGRESS';
      throw busy;
    }
    const claimed = await RouteRequest.findOneAndUpdate({
      _id: existing._id,
      $or: [{ status: 'failed' }, { status: 'processing', updatedAt: { $lte: staleBefore } }],
    }, {
      $set: { status: 'processing', requestHash, processingStartedAt: new Date(), expiresAt },
      $unset: { response: 1, lastErrorCode: 1 },
    }, { new: true }).maxTimeMS(5_000);
    if (!claimed) {
      const busy = httpError(409, 'A request with this Idempotency-Key is still processing');
      busy.code = 'IDEMPOTENCY_IN_PROGRESS';
      throw busy;
    }
    request = claimed;
  }

  try {
  const routes = await getRoutes(origin, destination, mode);
  const weather = await getWeather(origin, mode);
  const pollution = await Promise.all(routes.map(getPollution));
  let ranked = routes.map((route, index) => ({
    ...route,
    exposure: pollution[index],
    // AQI × travel time is an exposure proxy; concentration-specific estimates
    // can replace AQI once the ingestion pipeline stores them.
    score: pollution[index].freshness === 'fresh' && pollution[index].coverage >= 0.5 && pollution[index].confidence >= Number(process.env.MIN_EXPOSURE_CONFIDENCE || 0.5) && pollution[index].aqi !== null
      ? Math.round(pollution[index].aqi * route.durationSeconds)
      : pollution[index].freshness === 'fresh' && pollution[index].coverage >= 0.5 && pollution[index].confidence >= Number(process.env.MIN_EXPOSURE_CONFIDENCE || 0.5) && pollution[index].pm25 !== null
        ? Math.round(pollution[index].pm25 * route.durationSeconds * 100) / 100
        : null,
  }));
  const preference = routePreference;
  if (preference !== 'fastest' && ranked.length > 1) {
    const fastestDistance = Math.min(...ranked.map((route) => route.distanceMeters));
    const maxDistance = fastestDistance * (1 + maxExtraDistancePercent / 100);
    const withinPreferenceLimit = ranked.filter((route) => route.distanceMeters <= maxDistance);
    if (withinPreferenceLimit.length) ranked = withinPreferenceLimit;
  }
  const times = ranked.map((route) => route.durationSeconds);
  const exposures = ranked.map((route) => route.score).filter(Number.isFinite);
  const range = (values) => values.length ? Math.max(...values) - Math.min(...values) : 0;
  const timeRange = range(times);
  const exposureRange = range(exposures);
  const exposureRankingAvailable = ranked.every((route) => Number.isFinite(route.score));
  const minTime = Math.min(...times);
  const minExposure = exposures.length ? Math.min(...exposures) : 0;
  ranked.sort((a, b) => {
    if (preference === 'fastest') return a.durationSeconds - b.durationSeconds;
    if (!exposureRankingAvailable) return a.durationSeconds - b.durationSeconds;
    if (preference === 'lower_exposure') return a.score - b.score;
    const cost = (route) => 0.5 * (timeRange ? (route.durationSeconds - minTime) / timeRange : 0) +
      0.5 * (exposureRange ? (route.score - minExposure) / exposureRange : 0);
    return cost(a) - cost(b);
  });
  const requestId = request._id.toString();
  const responseRoutes = ranked.map((route, index) => ({
    id: `${requestId}-${index + 1}`,
    geometry: route.geometry,
    distanceMeters: Math.round(route.distanceMeters),
    travelTimeSeconds: Math.round(route.durationSeconds),
    exposure: route.exposure,
    rankingBasis: preference === 'fastest' || !exposureRankingAvailable ? 'travel_time' : preference,
    rank: index + 1,
  }));
  const response = {
    requestId, mode, routes: responseRoutes,
    weather: weather ? { available: true, data: weather } : { available: false },
    dataFreshness: responseRoutes.map((route) => ({ routeId: route.id, ...route.exposure })),
  };
  await RouteResult.findOneAndUpdate({ routeRequest: request._id }, {
    $set: {
    routes: ranked.map((route, index) => ({
      routeId: responseRoutes[index].id,
      geometry: route.geometry,
      distanceMeters: Math.round(route.distanceMeters),
      durationSeconds: Math.round(route.durationSeconds),
      exposureScore: route.score ?? undefined,
      averageAqi: route.exposure.aqi ?? undefined,
      averagePm25: route.exposure.pm25 ?? undefined,
      confidence: route.exposure.confidence,
      freshness: route.exposure.freshness,
      observedAt: route.exposure.observedAt ?? undefined,
      coverage: route.exposure.coverage,
      rank: index + 1,
      isRecommended: index === 0,
    })),
    generatedAt: new Date(),
    expiresAt,
    },
  }, { upsert: true, new: true, setDefaultsOnInsert: true }).maxTimeMS(5_000);
  request.status = 'completed';
  request.response = response;
  await request.save();
  return response;
  } catch (error) {
    await RouteRequest.updateOne({ _id: request._id, status: 'processing' }, {
      $set: { status: 'failed', lastErrorCode: error.code || `HTTP_${error.status || 500}` },
    }).maxTimeMS(5_000).catch((updateError) => console.error(JSON.stringify({
      event: 'route_failure_status_update_failed',
      code: updateError.code || updateError.name || 'DATABASE_ERROR',
    })));
    throw error;
  }
}

module.exports = { processRouteRequest };


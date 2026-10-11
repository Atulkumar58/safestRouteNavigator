const assert = require('node:assert/strict');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '../..');
const models = require(path.join(projectRoot, 'src/Models'));
const { connectDatabase, closeDatabase } = require(path.join(projectRoot, 'src/services/database'));
const { processRouteRequest } = require(path.join(projectRoot, 'src/services/route-pipeline'));

async function main() {
  await connectDatabase();
  const realFetch = global.fetch;
  global.fetch = async () => ({
    ok: true,
    json: async () => ({
      code: 'Ok',
      routes: [{
        geometry: {
          type: 'LineString',
          coordinates: [[77.209, 28.613], [77.209, 28.614]],
        },
        distance: 111,
        duration: 120,
      }],
    }),
  });

  try {
    const requestBody = {
      origin: { lat: 28.613, lon: 77.209 },
      destination: { lat: 28.614, lon: 77.209 },
      mode: 'walking',
      preferences: { routePreference: 'balanced' },
    };
    const estimateResponse = await processRouteRequest(requestBody);
    assert.equal(estimateResponse.routes.length, 1);
    assert.equal(estimateResponse.routes[0].exposure.pm25, 17.97);
    assert.ok(estimateResponse.routes[0].exposure.aqi > 0);
    assert.ok(estimateResponse.routes[0].exposure.coverage > 0);

    const request = await models.RouteRequest.findById(estimateResponse.requestId);
    const result = await models.RouteResult.findOne({ routeRequest: estimateResponse.requestId });
    assert.ok(request);
    assert.ok(result);
    assert.equal(request.travelMode, 'walking');
    assert.equal(request.status, 'completed');
    assert.equal(result.routes[0].averagePm25, 17.97);
    assert.equal(result.routes[0].isRecommended, true);

    await models.RoadSegmentEstimate.deleteMany({ segmentId: 'integration-road-1' });
    const fallbackResponse = await processRouteRequest(requestBody);
    assert.equal(fallbackResponse.routes[0].exposure.aqi, null);
    assert.equal(fallbackResponse.routes[0].exposure.pm25, 17.97);
    const fallbackResult = await models.RouteResult.findOne({ routeRequest: fallbackResponse.requestId });
    assert.equal(fallbackResult.routes[0].averagePm25, 17.97);
    assert.equal(fallbackResult.routes[0].averageAqi, undefined);
    console.log('Pipeline B consumed Pipeline A road estimate and raw observation, then persisted both route results');
  } finally {
    global.fetch = realFetch;
    await closeDatabase();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

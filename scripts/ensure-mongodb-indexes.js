require('dotenv').config();

const mongoose = require('mongoose');
const models = require('../src/Models');

async function replaceLegacyRouteResultIndex(model) {
  let indexes;
  try {
    indexes = await model.collection.indexes();
  } catch (error) {
    if (error.code === 26 || error.codeName === 'NamespaceNotFound') return;
    throw error;
  }

  const oldIndex = indexes.find((index) => {
    const keys = Object.entries(index.key || {});
    return keys.length === 1 && keys[0][0] === 'routeRequest' && keys[0][1] === 1 && index.unique !== true;
  });
  if (!oldIndex) return;

  const duplicates = await model.collection.aggregate([
    { $group: { _id: '$routeRequest', count: { $sum: 1 } } },
    { $match: { count: { $gt: 1 } } },
    { $limit: 1 },
  ]).toArray();
  if (duplicates.length) {
    const error = new Error('Cannot replace the non-unique routeRequest index: route_results contains duplicate routeRequest values. Resolve duplicates before retrying index setup.');
    error.code = 'DUPLICATE_ROUTE_RESULTS';
    throw error;
  }

  await model.collection.dropIndex(oldIndex.name);
  console.log(`Removed legacy non-unique index: ${oldIndex.name}`);
}

async function main() {
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error('MONGODB_URI is required');

  await mongoose.connect(uri, {
    dbName: process.env.MONGODB_DB || undefined,
    autoIndex: false,
    serverSelectionTimeoutMS: 10_000,
  });

  await replaceLegacyRouteResultIndex(models.RouteResult);

  for (const model of Object.values(models)) {
    await model.createIndexes();
    console.log(`Indexes ready: ${model.collection.name}`);
  }
}

main()
  .catch((error) => {
    const message = String(error.message || 'Index setup failed')
      .replace(/mongodb(?:\+srv)?:\/\/[^\s]+/gi, '[REDACTED_MONGODB_URI]');
    console.error(JSON.stringify({
      event: 'mongodb_index_setup_failed',
      code: error.code || error.name || 'INDEX_SETUP_FAILED',
      message,
    }));
    process.exitCode = 1;
  })
  .finally(async () => {
    if (mongoose.connection.readyState) await mongoose.disconnect();
  });

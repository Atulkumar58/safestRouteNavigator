const mongoose = require('mongoose');

async function connectDatabase() {
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error('MONGODB_URI is required');
  const configuredPoolSize = Number.parseInt(process.env.MONGODB_MAX_POOL_SIZE || '40', 10);
  const maxPoolSize = Number.isFinite(configuredPoolSize)
    ? Math.max(5, Math.min(100, configuredPoolSize))
    : 40;
  let lastError;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      await mongoose.connect(uri, {
        dbName: process.env.MONGODB_DB || undefined,
        // Indexes are created once by scripts/ensure-mongodb-indexes.js before
        // Deployments start. Do not let each replica race to create them.
        autoIndex: false,
        maxPoolSize,
        minPoolSize: Math.min(5, maxPoolSize),
        maxConnecting: 5,
        serverSelectionTimeoutMS: 5000,
        waitQueueTimeoutMS: 5000,
        socketTimeoutMS: 15000,
        maxIdleTimeMS: 30000,
      });
      return;
    } catch (error) {
      lastError = error;
      await mongoose.disconnect().catch(() => {});
      if (attempt === 4) break;
      const delayMs = Math.min(1000 * (2 ** attempt), 10_000);
      console.error(JSON.stringify({ event: 'mongodb_connect_retry', attempt: attempt + 1, delayMs, code: error.code || error.name }));
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  const error = new Error('MongoDB is unavailable after five connection attempts');
  error.code = lastError?.code || 'MONGODB_UNAVAILABLE';
  throw error;
}

async function closeDatabase() {
  if (mongoose.connection.readyState) await mongoose.disconnect();
}

module.exports = { connectDatabase, closeDatabase };


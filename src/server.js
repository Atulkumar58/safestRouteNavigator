require('dotenv').config();

const express = require('express');
const mongoose = require('mongoose');
const { randomUUID } = require('node:crypto');
const { connectDatabase, closeDatabase } = require('./services/database');
const routes = require('./routes/routes');

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '32kb' }));
function redactDiagnosticText(value) {
  return String(value || '')
    .replace(/mongodb(?:\+srv)?:\/\/[^\s]+/gi, '[REDACTED_MONGODB_URI]')
    .replace(/([?&]key=)[^&\s]+/gi, '$1[REDACTED]')
    .replace(/(Bearer )[A-Za-z0-9._~-]+/gi, '$1[REDACTED]')
    .replace(/(X-API-Key\s*[:=]\s*)[^\s,;]+/gi, '$1[REDACTED]');
}
app.use((req, res, next) => {
  req.requestId = randomUUID();
  res.set('X-Request-Id', req.requestId);
  next();
});
app.get('/health', (_req, res) => res.json({ status: 'ok', service: 'saferoute-api' }));
app.get('/health/live', (_req, res) => res.json({ status: 'alive' }));
app.get('/health/ready', (_req, res) => {
  if (mongoose.connection.readyState !== 1) {
    return res.status(503).json({ status: 'not-ready', database: 'disconnected' });
  }
  return res.json({ status: 'ready', database: 'connected' });
});
app.use('/api', routes);
app.use((err, _req, res, _next) => {
  const databaseUnavailable = /^Mongo(?:Server|Network|Operation|WaitQueue)/.test(err.name || '') ||
    ['ETIMEDOUT', 'ECONNRESET', 'MONGODB_UNAVAILABLE'].includes(err.code);
  const status = err.status || (databaseUnavailable ? 503 : 500);
  if (status === 503) res.set('Retry-After', '5');
  console.error(JSON.stringify({
    event: 'api_request_failed',
    requestId: _req.requestId,
    method: _req.method,
    path: _req.path,
    status,
    code: err.code || `HTTP_${status}`,
    message: redactDiagnosticText(err.message || 'Unknown error'),
    stack: redactDiagnosticText(err.stack || ''),
  }));
  res.status(status).json({ error: status >= 500 ? 'Internal server error' : err.message, requestId: _req.requestId });
});

const port = Number(process.env.PORT || 3000);
if (!process.env.API_BEARER_TOKEN || process.env.API_BEARER_TOKEN.length < 32) {
  console.error('API_BEARER_TOKEN must be configured with at least 32 characters');
  process.exit(1);
}
let server;
connectDatabase()
  .then(() => {
    server = app.listen(port, () => console.log(`SafeRoute API listening on ${port}`));
  })
  .catch((error) => {
    console.error(JSON.stringify({
      event: 'mongodb_startup_failed',
      code: error.code || error.name || 'MONGODB_UNAVAILABLE',
      message: redactDiagnosticText(error.message || 'MongoDB unavailable'),
      stack: redactDiagnosticText(error.stack || ''),
    }));
    process.exitCode = 1;
  });

async function shutdown() {
  if (server) await new Promise((resolve) => server.close(resolve));
  await closeDatabase();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);


const express = require('express');
const { timingSafeEqual } = require('node:crypto');
const { processRouteRequest } = require('../services/route-pipeline');
const { tryAcquireRouteSlot } = require('../services/route-capacity');

const router = express.Router();

router.use((req, _res, next) => {
  const expected = Buffer.from(`Bearer ${process.env.API_BEARER_TOKEN || ''}`);
  const provided = Buffer.from(req.get('authorization') || '');
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
    const error = new Error('Unauthorized');
    error.status = 401;
    return next(error);
  }
  next();
});

router.post('/routes', async (req, res, next) => {
  const idempotencyKey = req.get('Idempotency-Key');
  if (!idempotencyKey || idempotencyKey.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(idempotencyKey)) {
    return res.status(400).json({ error: 'A valid Idempotency-Key header is required.' });
  }
  const release = tryAcquireRouteSlot();
  if (!release) {
    res.set('Retry-After', '2');
    return res.status(503).json({ error: 'Route service is busy; retry shortly.' });
  }

  try {
    const result = await processRouteRequest(req.body || {}, { idempotencyKey });
    res.json(result);
  } catch (error) {
    if (error.code === 'IDEMPOTENCY_IN_PROGRESS') res.set('Retry-After', '2');
    next(error);
  } finally {
    release();
  }
});

module.exports = router;


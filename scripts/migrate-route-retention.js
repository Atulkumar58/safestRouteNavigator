#!/usr/bin/env node
'use strict';

const mongoose = require('mongoose');

const apply = process.argv.includes('--apply');
const retentionDays = Math.max(1, Math.min(365, Number(process.env.ROUTE_DATA_RETENTION_DAYS || 30)));
const retentionMs = retentionDays * 24 * 60 * 60 * 1000;

async function backfill(collectionName, dateFields) {
  const collection = mongoose.connection.collection(collectionName);
  const cursor = collection.find({ expiresAt: { $exists: false } }).project({ ...Object.fromEntries(dateFields.map((field) => [field, 1])) });
  let pending = [];
  let count = 0;
  for await (const document of cursor) {
    const baseDate = dateFields.map((field) => document[field]).find((value) => value instanceof Date) || new Date();
    pending.push({
      updateOne: {
        filter: { _id: document._id, expiresAt: { $exists: false } },
        update: { $set: { expiresAt: new Date(baseDate.getTime() + retentionMs) } },
      },
    });
    count += 1;
    if (pending.length === 500) {
      if (apply) await collection.bulkWrite(pending, { ordered: false });
      pending = [];
    }
  }
  if (pending.length && apply) await collection.bulkWrite(pending, { ordered: false });
  console.log(`${collectionName}: ${count} documents ${apply ? 'updated' : 'would be updated'}`);
}

async function main() {
  if (!process.env.MONGODB_URI) throw new Error('MONGODB_URI is required');
  await mongoose.connect(process.env.MONGODB_URI, {
    serverSelectionTimeoutMS: 5000,
    waitQueueTimeoutMS: 5000,
  });
  await backfill('route_requests', ['createdAt', 'requestedAt']);
  await backfill('route_results', ['createdAt', 'generatedAt']);
  console.log(apply
    ? 'Backfill complete. MongoDB TTL cleanup will delete expired documents asynchronously.'
    : 'Preview only. Add --apply to set expiry; this can cause old route data to be deleted by TTL cleanup.');
}

main()
  .catch((error) => {
    console.error(`Retention migration failed (${error.code || error.name || 'ERROR'}).`);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (mongoose.connection.readyState) await mongoose.disconnect();
  });

const mongoose = require('mongoose');

const routeResultSchema = new mongoose.Schema(
  {
    routeRequest: {
      type: mongoose.Schema.Types.ObjectId,
      ref: 'RouteRequest',
      required: true,
    },
    routes: [
      {
        routeId: { type: String, required: true },
        geometry: {
          type: {
            type: String,
            enum: ['LineString'],
            required: true,
            default: 'LineString',
          },
          coordinates: { type: [[Number]], required: true },
        },
        distanceMeters: { type: Number, required: true, min: 0 },
        durationSeconds: { type: Number, required: true, min: 0 },
        exposureScore: { type: Number, min: 0 },
        averageAqi: { type: Number, min: 0 },
        averagePm25: { type: Number, min: 0 },
        confidence: { type: Number, min: 0, max: 1 },
        freshness: { type: String, enum: ['fresh', 'stale', 'unavailable'] },
        observedAt: { type: Date },
        coverage: { type: Number, min: 0, max: 1 },
        rank: { type: Number, min: 1 },
        isRecommended: { type: Boolean, default: false },
      },
    ],
    generatedAt: { type: Date, default: Date.now },
    expiresAt: { type: Date },
  },
  { timestamps: true, collection: 'route_results' }
);

routeResultSchema.index({ expiresAt: 1 }, { expireAfterSeconds: 0 });
routeResultSchema.index({ routeRequest: 1 }, { unique: true });

module.exports = mongoose.model('RouteResult', routeResultSchema);

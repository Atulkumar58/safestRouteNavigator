const mongoose = require('mongoose');

const pointSchema = new mongoose.Schema(
  {
    type: { type: String, enum: ['Point'], required: true, default: 'Point' },
    coordinates: { type: [Number], required: true },
  },
  { _id: false }
);

const routeRequestSchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    idempotencyKey: { type: String, trim: true, maxlength: 128 },
    requestHash: { type: String, trim: true, maxlength: 64 },
    response: { type: mongoose.Schema.Types.Mixed },
    lastErrorCode: { type: String, trim: true },
    processingStartedAt: { type: Date },
    expiresAt: { type: Date },
    origin: { type: pointSchema, required: true },
    destination: { type: pointSchema, required: true },
    travelMode: {
      type: String,
      enum: ['walking', 'cycling', 'driving', 'transit'],
      required: true,
      default: 'walking',
    },
    preferences: {
      prioritizeAirQuality: { type: Boolean, default: true },
      maxExtraDistancePercent: { type: Number, min: 0, max: 100, default: 20 },
      routePreference: { type: String, enum: ['fastest', 'lower_exposure', 'balanced'], default: 'balanced' },
    },
    status: {
      type: String,
      enum: ['pending', 'processing', 'completed', 'failed'],
      default: 'pending',
    },
    requestedAt: { type: Date, default: Date.now },
  },
  { timestamps: true, collection: 'route_requests' }
);

routeRequestSchema.index({ 'origin': '2dsphere' });
routeRequestSchema.index({ 'destination': '2dsphere' });
routeRequestSchema.index({ requestedAt: -1 });
routeRequestSchema.index({ idempotencyKey: 1 }, { unique: true, sparse: true });
routeRequestSchema.index({ expiresAt: 1 }, { expireAfterSeconds: 0 });

module.exports = mongoose.model('RouteRequest', routeRequestSchema);

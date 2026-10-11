const mongoose = require('mongoose');

const pollutionObservationSchema = new mongoose.Schema(
  {
    source: {
      type: String,
      required: true,
      enum: ['cpcb', 'openaq', 'openmeteo', 'other'],
      lowercase: true,
      trim: true,
    },
    dataType: { type: String, enum: ['measured', 'modelled'], trim: true },
    stationId: { type: String, trim: true },
    parameter: { type: String, enum: ['pm25', 'pm10', 'no2', 'o3', 'so2', 'co'], trim: true },
    units: { type: String, trim: true },
    observedAt: { type: Date, required: true },
    location: {
      type: {
        type: String,
        enum: ['Point'],
        required: true,
        default: 'Point',
      },
      coordinates: {
        type: [Number],
        required: true,
        validate: {
          validator: (coordinates) => coordinates.length === 2,
          message: 'Location must contain [longitude, latitude].',
        },
      },
    },
    aqi: { type: Number, min: 0 },
    pm25: { type: Number, min: 0 },
    pm10: { type: Number, min: 0 },
    no2: { type: Number, min: 0 },
    o3: { type: Number, min: 0 },
    so2: { type: Number, min: 0 },
    co: { type: Number, min: 0 },
    rawData: { type: mongoose.Schema.Types.Mixed },
  },
  { timestamps: true, collection: 'pollution_observations' }
);

pollutionObservationSchema.index({ location: '2dsphere' });
pollutionObservationSchema.index({ observedAt: -1, source: 1 });
pollutionObservationSchema.index(
  { source: 1, stationId: 1, observedAt: 1, parameter: 1 },
  {
    unique: true,
    partialFilterExpression: { parameter: { $type: 'string' } },
    name: 'uniq_pollution_observation_key',
  }
);
pollutionObservationSchema.index({ observedAt: 1 }, { expireAfterSeconds: 30 * 24 * 60 * 60, name: 'ttl_pollution_observations_30d' });

module.exports = mongoose.model('PollutionObservation', pollutionObservationSchema);

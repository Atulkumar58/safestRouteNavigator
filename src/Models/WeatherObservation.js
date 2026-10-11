const mongoose = require('mongoose');

const weatherObservationSchema = new mongoose.Schema(
  {
    source: { type: String, required: true, default: 'weather_api', trim: true },
    observedAt: { type: Date, required: true },
    location: {
      type: {
        type: String,
        enum: ['Point'],
        required: true,
        default: 'Point',
      },
      coordinates: { type: [Number], required: true },
    },
    temperatureC: { type: Number },
    humidityPercent: { type: Number, min: 0, max: 100 },
    windSpeedKph: { type: Number, min: 0 },
    windDirectionDegrees: { type: Number, min: 0, max: 360 },
    precipitationMm: { type: Number, min: 0 },
    conditions: { type: String, trim: true },
    rawData: { type: mongoose.Schema.Types.Mixed },
  },
  { timestamps: true, collection: 'weather_observations' }
);

weatherObservationSchema.index({ location: '2dsphere' });
weatherObservationSchema.index({ observedAt: -1 });
weatherObservationSchema.index({ observedAt: 1 }, { expireAfterSeconds: 30 * 24 * 60 * 60, name: 'ttl_weather_observations_30d' });

module.exports = mongoose.model('WeatherObservation', weatherObservationSchema);

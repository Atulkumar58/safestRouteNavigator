const mongoose = require('mongoose');

const roadSegmentEstimateSchema = new mongoose.Schema(
  {
    segmentId: { type: String, required: true, trim: true },
    roadName: { type: String, trim: true },
    geometry: {
      type: {
        type: String,
        enum: ['LineString'],
        required: true,
        default: 'LineString',
      },
      coordinates: { type: [[Number]], required: true },
    },
    estimatedAt: { type: Date, required: true },
    sourceObservedAt: { type: Date },
    estimatedAqi: { type: Number, required: true, min: 0 },
    estimatedPm25: { type: Number, min: 0 },
    exposureScore: { type: Number, required: true, min: 0 },
    confidence: { type: Number, required: true, min: 0, max: 1 },
    factors: { type: mongoose.Schema.Types.Mixed },
  },
  { timestamps: true, collection: 'road_segment_estimates' }
);

roadSegmentEstimateSchema.index({ geometry: '2dsphere' });
roadSegmentEstimateSchema.index({ segmentId: 1 }, { unique: true, name: 'uniq_road_segment_id' });
roadSegmentEstimateSchema.index({ segmentId: 1, estimatedAt: -1 });
roadSegmentEstimateSchema.index({ sourceObservedAt: -1 });
roadSegmentEstimateSchema.index({ estimatedAt: 1 }, { expireAfterSeconds: 30 * 24 * 60 * 60, name: 'ttl_road_estimates_30d' });

module.exports = mongoose.model('RoadSegmentEstimate', roadSegmentEstimateSchema);

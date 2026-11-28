const express = require("express");

const {
  getHistory
} = require("../services/dynamodb");

const {
  parseTime,
  formatBangkokTime
} = require("../utils/time");

const requireApiKey = require("../middleware/apiKey");

const router = express.Router();

router.use(requireApiKey);

// Time constants
const SECOND_MS = 1000;
const MINUTE_MS = 60 * SECOND_MS;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const THAILAND_OFFSET_MS = 7 * HOUR_MS;
const EXPECTED_POSTURE_INTERVAL_MS = 5000;

// Validate requested history range
function getRange(query) {
  const fromMs = parseTime(query.from);
  const toMs = parseTime(query.to);

  if (fromMs > toMs) {
    throw new Error("'from' must be before 'to'");
  }

  if (toMs - fromMs > 31 * DAY_MS) {
    throw new Error("Maximum history range is 31 days");
  }

  return { fromMs, toMs };
}

// Calculate bucket start using Thailand time (UTC+7)
function getBucketStart(ts, bucket) {
  const bucketSize = bucket === "day"
    ? DAY_MS
    : HOUR_MS;
  return (
    Math.floor((ts + THAILAND_OFFSET_MS) / bucketSize)
    * bucketSize
    - THAILAND_OFFSET_MS
  );
}

// POSTURE HISTORY
router.get("/posture", async (req, res, next) => {
  try {
    const { fromMs, toMs } = getRange(req.query);

    const items = await getHistory(
      "pi#posture",
      fromMs,
      toMs
    );

    const statuses = items
      .filter(item =>
        item.type === "posture_status" &&
        typeof item.ts === "number"
      )
      .sort((a, b) => a.ts - b.ts);

    const summary = {
      good_s: 0,
      bad_s: 0,
      away_s: 0,
      alerts: items.filter(item =>
        item.type === "posture_state" &&
        item.state === "bad"
      ).length,
      issues_s: {}
    };

    for (let i = 0; i < statuses.length; i++) {
      const current = statuses[i];
      const next = statuses[i + 1];

      // Determine the end of this status interval
      const intervalEnd = next
        ? Math.min(next.ts, current.ts + EXPECTED_POSTURE_INTERVAL_MS)
        : current.ts + EXPECTED_POSTURE_INTERVAL_MS;

      // Clip interval to requested time range
      const intervalStart = Math.max(current.ts, fromMs);
      const clippedEnd = Math.min(intervalEnd, toMs);

      const duration = Math.max(
        0,
        clippedEnd - intervalStart
      );

      const seconds = Math.floor(duration / SECOND_MS);

      if (current.state === "good") {
        summary.good_s += seconds;
      } else if (current.state === "bad") {
        summary.bad_s += seconds;
      } else if (current.state === "away") {
        summary.away_s += seconds;
      }

      // Pi sends issues as an array:
      // ["slouch", "head_down"]
      if (Array.isArray(current.issues)) {
        for (const issue of current.issues) {
          if (typeof issue !== "string") continue;

          summary.issues_s[issue] =
            (summary.issues_s[issue] || 0) + seconds;
        }
      }
    }

    res.json(summary);

  } catch (error) {
    next(error);
  }
});

// ENVIRONMENT HISTORY
router.get("/env", async (req, res, next) => {
  try {
    const { fromMs, toMs } = getRange(req.query);

    const metric = req.query.metric;
    const bucket = req.query.bucket || "hour";

    const allowedMetrics = [
      "temperature_c",
      "humidity_pct",
      "light_lux",
      "dust_pm25"
    ];

    if (!allowedMetrics.includes(metric)) {
      return res.status(400).json({
        error: "Unsupported metric"
      });
    }

    if (!["hour", "day"].includes(bucket)) {
      return res.status(400).json({
        error: "Bucket must be hour or day"
      });
    }

    const items = await getHistory(
      "esp#env",
      fromMs,
      toMs
    );

    const values = items.filter(item =>
      typeof item[metric] === "number" &&
      typeof item.ts === "number"
    );

    const buckets = new Map();

    for (const item of values) {
      const start = getBucketStart(item.ts, bucket);

      if (!buckets.has(start)) {
        buckets.set(start, []);
      }

      buckets.get(start).push(item[metric]);
    }

    const points = [...buckets.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([start, data]) => ({
        t: formatBangkokTime(start),
        avg: Number(
          (data.reduce((sum, value) => sum + value, 0) / data.length).toFixed(2)
        ),
        min: Math.min(...data),
        max: Math.max(...data),
        count: data.length
      }));

    res.json({
      metric,
      points
    });

  } catch (error) {
    next(error);
  }
});

module.exports = router;

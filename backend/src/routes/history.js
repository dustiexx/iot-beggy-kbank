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

function getRange(query) {
  const fromMs = parseTime(query.from);
  const toMs = parseTime(query.to);

  if (fromMs > toMs) {
    throw new Error("'from' must be before 'to'");
  }

  if (toMs - fromMs > 31 * 24 * 60 * 60 * 1000) {
    throw new Error("Maximum history range is 31 days");
  }

  return { fromMs, toMs };
}

router.get("/posture", async (req, res, next) => {
  try {
    const { fromMs, toMs } = getRange(req.query);

    const items = await getHistory(
      "pi#posture",
      fromMs,
      toMs
    );

    const statuses = items
      .filter(item => item.type === "posture_status")
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

    const expectedIntervalMs = 5000;

    for (let i = 0; i < statuses.length; i++) {
      const current = statuses[i];
      const next = statuses[i + 1];

      const intervalMs = next
        ? Math.max(0, next.ts - current.ts)
        : expectedIntervalMs;

      const duration = Math.min(
        intervalMs,
        expectedIntervalMs
      );

      const seconds = Math.floor(duration / 1000);

      if (current.state === "good") {
        summary.good_s += seconds;
      } else if (current.state === "bad") {
        summary.bad_s += seconds;
      } else if (current.state === "away") {
        summary.away_s += seconds;
      }

      if (current.issues && typeof current.issues === "object") {
        for (const [issue, active] of Object.entries(current.issues)) {
          if (active) {
            summary.issues_s[issue] =
              (summary.issues_s[issue] || 0) + seconds;
          }
        }
      }
    }

    res.json(summary);
  } catch (error) {
    next(error);
  }
});

router.get("/env", async (req, res, next) => {
  try {
    const { fromMs, toMs } = getRange(req.query);

    const metric = req.query.metric;
    const bucket = req.query.bucket || "hour";

    const allowedMetrics = [
      "temperature",
      "humidity",
      "light",
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
      typeof item[metric] === "number"
    );

    const buckets = new Map();

    const bucketSize = bucket === "hour"
      ? 60 * 60 * 1000
      : 24 * 60 * 60 * 1000;

    for (const item of values) {
      const start = Math.floor(item.ts / bucketSize) * bucketSize;

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
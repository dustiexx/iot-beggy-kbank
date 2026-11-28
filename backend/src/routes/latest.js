const express = require("express");
const { getLatestByPartition } = require("../services/dynamodb");

const router = express.Router();

const partitions = [
  "esp#env",
  "pi#posture",
  "pi#battery",
  "pi#voice",
  "pi#head",
  "pi#calendar",
  "pi#status"
];

router.get("/", async (req, res, next) => {
  try {
    const results = await Promise.all(
      partitions.map(pk => getLatestByPartition(pk))
    );

    const data = {};

    for (const item of results) {
      if (item) {
        data[item.pk] = item;
      }
    }

    res.json({
      data,
      fetched_at: new Date().toISOString()
    });
  } catch (error) {
    next(error);
  }
});

module.exports = router;
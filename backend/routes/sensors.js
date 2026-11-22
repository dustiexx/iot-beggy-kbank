const express = require("express");
const router = express.Router();

const { saveSensorData } =
  require("../services/dynamodb");

router.post("/", async (req, res) => {
  try {
    const {
      sensor_id,
      timestamp,
      sensor_type,
      value,
      unit
    } = req.body;

    if (
      !sensor_id ||
      !timestamp ||
      !sensor_type ||
      typeof value !== "number"
    ) {
      return res.status(400).json({
        error: "Invalid sensor data"
      });
    }

    await saveSensorData({
      sensor_id,
      timestamp,
      sensor_type,
      value,
      unit,
      received_at: new Date().toISOString()
    });

    res.status(201).json({
      message: "Data saved successfully"
    });

  } catch (error) {
    console.error(error);

    res.status(500).json({
      error: "Internal server error"
    });
  }
});

module.exports = router;
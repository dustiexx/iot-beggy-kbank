const express = require("express");
const cors = require("cors");
const helmet = require("helmet");

const { broadcast, addClient } = require("./services/sse");

const latestRoutes = require("./routes/latest");
const settingsRoutes = require("./routes/settings");
const historyRoutes = require("./routes/history");

const app = express();

app.use(helmet());

app.use(cors({
  origin: process.env.DASHBOARD_ORIGIN,
  credentials: true
}));

app.use(express.json({ limit: "100kb" }));

app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    timestamp: new Date().toISOString()
  });
});

app.use("/api/latest", latestRoutes);
app.use("/api/settings", settingsRoutes);
app.use("/history", historyRoutes);

app.get("/api/events", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");

  res.flushHeaders();

  addClient(res);

  res.write("retry: 5000\n\n");

  res.write(`event: connected\n`);
  res.write(`data: ${JSON.stringify({
    message: "SSE connected"
  })}\n\n`);

  const heartbeat = setInterval(() => {
    res.write(": heartbeat\n\n");
  }, 20000);

  res.on("close", () => {
    clearInterval(heartbeat);
  });
});

app.use((req, res) => {
  res.status(404).json({
    error: "Route not found"
  });
});

app.use((error, req, res, next) => {
  console.error(error);

  res.status(500).json({
    error: "Internal server error"
  });
});

module.exports = app;
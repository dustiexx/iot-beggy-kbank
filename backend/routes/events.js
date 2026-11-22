const express = require("express");
const router = express.Router();

const clients = new Set();

function publishEvent(data) {
  const message = JSON.stringify(data);

  for (const client of clients) {
    client.write(`event: sensor-data\n`);
    client.write(`data: ${message}\n\n`);
  }
}

router.get("/", (req, res) => {
  res.set({
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no"
  });

  res.flushHeaders();

  clients.add(res);

  res.write(": connected\n\n");

  const heartbeat = setInterval(() => {
    res.write(": ping\n\n");
  }, 20000);

  req.on("close", () => {
    clearInterval(heartbeat);
    clients.delete(res);
  });
});

module.exports = {
  router,
  publishEvent
};
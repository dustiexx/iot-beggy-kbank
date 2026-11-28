require("dotenv").config();

const app = require("./app");
const { connectMQTT } = require("./services/mqtt");

const PORT = process.env.PORT || 3000;

async function start() {
  connectMQTT();

  const server = app.listen(PORT, "0.0.0.0", () => {
    console.log(`Backend listening on port ${PORT}`);
  });

  function shutdown() {
    console.log("Shutting down...");

    server.close(() => {
      process.exit(0);
    });

    setTimeout(() => process.exit(1), 10000).unref();
  }

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

start().catch(error => {
  console.error("Backend startup failed:", error);
  process.exit(1);
});
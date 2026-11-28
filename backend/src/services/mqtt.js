const mqtt = require("mqtt");
const fs = require("fs");
const { broadcast } = require("./sse");

let client;

const certificate = process.env.IOT_CERT_PEM
  ? Buffer.from(process.env.IOT_CERT_PEM)
  : fs.readFileSync(process.env.IOT_CERT_PATH);

const privateKey = process.env.IOT_KEY_PEM
  ? Buffer.from(process.env.IOT_KEY_PEM)
  : fs.readFileSync(process.env.IOT_KEY_PATH);

function connectMQTT() {
  const options = {
    clientId: process.env.IOT_CLIENT_ID,
    protocol: "mqtts",
    port: 8883,
    ca: fs.readFileSync(process.env.IOT_CA_PATH),
    cert: certificate,
    key: privateKey,
    clean: true,
    reconnectPeriod: 5000,
    connectTimeout: 30000
  };

  client = mqtt.connect(
    `mqtts://${process.env.IOT_ENDPOINT}:8883`,
    options
  );

  client.on("connect", () => {
    console.log("Connected to AWS IoT Core");

    client.subscribe("beggy/#", { qos: 1 }, (error) => {
      if (error) {
        console.error("MQTT subscribe error:", error);
      } else {
        console.log("Subscribed to beggy/#");
      }
    });
  });

  client.on("message", (topic, message) => {
    try {
      const payload = JSON.parse(message.toString());

      broadcast("mqtt", {
        topic,
        payload,
        received_at: new Date().toISOString()
      });

      processNotification(topic, payload);
    } catch (error) {
      console.error("Invalid MQTT message:", error.message);
    }
  });

  client.on("reconnect", () => {
    console.log("Reconnecting to AWS IoT Core");
  });

  client.on("error", (error) => {
    console.error("MQTT error:", error.message);
  });

  client.on("close", () => {
    console.log("MQTT connection closed");
  });

  return client;
}

function publishSettings(settings) {
  return new Promise((resolve, reject) => {
    if (!client || !client.connected) {
      return reject(new Error("MQTT is not connected"));
    }

    const message = JSON.stringify(settings);

    client.publish(
      "beggy/pi/settings",
      message,
      { qos: 1, retain: true },
      (error) => {
        if (error) reject(error);
        else resolve();
      }
    );
  });
}

function processNotification(topic, payload) {
  if (
    topic === "beggy/pi/battery" &&
    payload.type === "battery_low"
  ) {
    broadcast("notification", {
      category: "battery",
      payload
    });
  }

  if (
    topic === "beggy/pi/posture" &&
    payload.type === "posture_state" &&
    payload.state === "bad"
  ) {
    broadcast("notification", {
      category: "posture",
      payload
    });
  }

  if (
    topic === "beggy/pi/calendar" &&
    payload.type === "calendar_error" &&
    payload.reason === "login_expired"
  ) {
    broadcast("notification", {
      category: "calendar",
      payload
    });
  }

  if (
    topic === "beggy/esp/env" &&
    typeof payload.dust_pm25 === "number" &&
    payload.dust_pm25 > 37.5
  ) {
    broadcast("notification", {
      category: "dust",
      payload
    });
  }
}

module.exports = {
  connectMQTT,
  publishSettings
};
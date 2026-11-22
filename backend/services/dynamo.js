const { DynamoDBClient } =
  require("@aws-sdk/client-dynamodb");

const {
  DynamoDBDocumentClient,
  PutCommand
} = require("@aws-sdk/lib-dynamodb");

const client = new DynamoDBClient({
  region: process.env.AWS_REGION
});

const db = DynamoDBDocumentClient.from(client);

async function saveSensorData(data) {
  await db.send(new PutCommand({
    TableName: "SensorTelemetry",
    Item: data
  }));
}

module.exports = { saveSensorData };
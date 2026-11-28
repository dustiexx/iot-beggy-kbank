const { DynamoDBClient } = require("@aws-sdk/client-dynamodb");

const {
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand
} = require("@aws-sdk/lib-dynamodb");

const client = new DynamoDBClient({
  region: process.env.AWS_REGION || "ap-southeast-1"
});

const db = DynamoDBDocumentClient.from(client, {
  marshallOptions: {
    removeUndefinedValues: true
  }
});

const READINGS_TABLE = process.env.READINGS_TABLE;
const SETTINGS_TABLE = process.env.SETTINGS_TABLE;
const NOTIFICATION_TABLE = process.env.NOTIFICATION_TABLE;

async function queryAll(params) {
  const items = [];
  let lastKey;

  do {
    const result = await db.send(new QueryCommand({
      ...params,
      ExclusiveStartKey: lastKey
    }));

    items.push(...(result.Items || []));
    lastKey = result.LastEvaluatedKey;
  } while (lastKey);

  return items;
}

async function getLatestByPartition(pk) {
  const result = await db.send(new QueryCommand({
    TableName: READINGS_TABLE,
    KeyConditionExpression: "pk = :pk",
    ExpressionAttributeValues: {
      ":pk": pk
    },
    ScanIndexForward: false,
    Limit: 1
  }));

  return result.Items?.[0] || null;
}

async function getHistory(pk, fromMs, toMs) {
  return queryAll({
    TableName: READINGS_TABLE,
    KeyConditionExpression: "pk = :pk AND ts BETWEEN :from AND :to",
    ExpressionAttributeValues: {
      ":pk": pk,
      ":from": fromMs,
      ":to": toMs
    },
    ScanIndexForward: true
  });
}

  async function getSettings(deviceId = "beggy-pi") {
    const result = await db.send(new GetCommand({
      TableName: SETTINGS_TABLE,
      Key: {
        device_id: deviceId
      }
    }));
    
    return result.Item || null;
  }

async function saveSettings(deviceId, settings) {
  const item = {
    device_id: deviceId,
    settings,
    updated_at: new Date().toISOString()
  };

  await db.send(new PutCommand({
    TableName: SETTINGS_TABLE,
    Item: item
  }));

  return item;
}

async function markNotification(notificationId, expiresAt) {
  try {
    await db.send(new PutCommand({
      TableName: NOTIFICATION_TABLE,
      Item: {
        notification_id: notificationId,
        notified_at: Date.now(),
        expires_at: expiresAt
      },
      ConditionExpression: "attribute_not_exists(notification_id)"
    }));

    return true;
  } catch (error) {
    if (error.name === "ConditionalCheckFailedException") {
      return false;
    }

    throw error;
  }
}

module.exports = {
  getLatestByPartition,
  getHistory,
  getSettings,
  saveSettings,
  markNotification
};
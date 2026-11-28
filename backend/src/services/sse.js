const clients = new Set();

function addClient(res) {
  clients.add(res);

  res.on("close", () => {
    clients.delete(res);
  });
}

function sendEvent(res, event, data) {
  res.write(`event: ${event}\n`);
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function broadcast(event, data) {
  for (const client of clients) {
    try {
      sendEvent(client, event, data);
    } catch {
      clients.delete(client);
    }
  }
}

function getClientCount() {
  return clients.size;
}

module.exports = {
  addClient,
  sendEvent,
  broadcast,
  getClientCount
};
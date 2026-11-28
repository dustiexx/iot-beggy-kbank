function parseTime(value) {
  const time = Date.parse(value);

  if (!value || !Number.isFinite(time)) {
    throw new Error("Invalid ISO 8601 time");
  }

  return time;
}

function formatBangkokTime(ms) {
  const value = new Date(ms).toLocaleString("sv-SE", {
    timeZone: "Asia/Bangkok"
  });

  return value.replace(" ", "T") + "+07:00";
}

module.exports = {
  parseTime,
  formatBangkokTime
};
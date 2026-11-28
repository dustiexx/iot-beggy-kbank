function requireApiKey(req, res, next) {
  const key = req.headers["x-api-key"];

  if (!key || key !== process.env.BACKEND_KEY) {
    return res.status(401).json({
      error: "Unauthorized"
    });
  }

  next();
}

module.exports = requireApiKey;
import pino from "pino";

export const logger = pino({
  level: process.env.LOG_LEVEL || "info",
  base: { service: "nutria-whatsapp-gateway" },
  redact: {
    paths: ["config.auth", "req.headers.authorization"],
    censor: "[REDACTED]",
  },
});


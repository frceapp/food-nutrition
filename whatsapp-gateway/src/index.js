import "dotenv/config";

import { createApiClient } from "./apiClient.js";
import { logger } from "./logger.js";
import { startWhatsApp } from "./whatsapp.js";


const config = {
  backendUrl: process.env.BACKEND_URL || "http://localhost:8000",
  sessionPath: process.env.SESSION_PATH || "./session",
  timeoutMs: Number.parseInt(process.env.BACKEND_TIMEOUT_MS || "60000", 10),
};

if (!Number.isFinite(config.timeoutMs) || config.timeoutMs <= 0) {
  throw new Error("BACKEND_TIMEOUT_MS harus berupa angka positif");
}

const apiClient = createApiClient({
  backendUrl: config.backendUrl,
  timeoutMs: config.timeoutMs,
});

startWhatsApp({
  sessionPath: config.sessionPath,
  apiClient,
  logger,
}).catch((error) => {
  logger.fatal({ err: error }, "Gateway gagal dijalankan");
  process.exitCode = 1;
});


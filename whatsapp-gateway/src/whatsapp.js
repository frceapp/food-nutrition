import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  isHostedLidUser,
  isHostedPnUser,
  isLidUser,
  isPnUser,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import qrcode from "qrcode-terminal";

import {
  BackendResponseError,
  BackendUnavailableError,
} from "./apiClient.js";


const TEXT_INSTRUCTION =
  "Halo Bunda 👋\nKirim foto makanan dari atas piring ya, nanti NUTRIA bantu cek estimasi nutrisinya.\n\nKetik *cek hari ini* untuk melihat total estimasi gizi makanan hari ini.";
const DOWNLOAD_ERROR =
  "Maaf Bunda, gambar belum berhasil diproses. Coba kirim ulang fotonya ya.";
const SYSTEM_BUSY =
  "Maaf Bunda, sistem NUTRIA sedang sibuk sebentar. Coba lagi beberapa saat ya.";
const IDENTITY_ERROR =
  "Maaf, nomor WhatsApp belum berhasil dikenali. Coba kirim pesan lagi beberapa saat lagi.";


function unwrapMessage(content) {
  if (!content) return null;
  if (content.ephemeralMessage?.message) {
    return unwrapMessage(content.ephemeralMessage.message);
  }
  if (content.viewOnceMessage?.message) {
    return unwrapMessage(content.viewOnceMessage.message);
  }
  if (content.viewOnceMessageV2?.message) {
    return unwrapMessage(content.viewOnceMessageV2.message);
  }
  return content;
}


function phoneFromJid(jid) {
  return jid.split("@")[0].split(":")[0].replace(/\D/g, "");
}


function isPhoneJid(jid) {
  return Boolean(
    jid && (isPnUser(jid) || isHostedPnUser(jid) || jid.endsWith("@c.us")),
  );
}


function isLidJid(jid) {
  return Boolean(jid && (isLidUser(jid) || isHostedLidUser(jid)));
}


export async function phoneFromMessage(message, socket, logger) {
  const key = message?.key || {};
  const candidates = [key.remoteJidAlt, key.participantAlt, key.remoteJid];
  const phoneJid = candidates.find(isPhoneJid);
  if (phoneJid) return phoneFromJid(phoneJid);

  const lidJid = candidates.find(isLidJid);
  if (!lidJid) return null;

  try {
    const mappedJid = await socket.signalRepository?.lidMapping?.getPNForLID(
      lidJid,
    );
    if (isPhoneJid(mappedJid)) return phoneFromJid(mappedJid);
  } catch (error) {
    logger.warn({ err: error }, "Gagal membaca mapping LID WhatsApp");
  }
  return null;
}


function incomingText(content) {
  return (
    content?.conversation ||
    content?.extendedTextMessage?.text ||
    content?.buttonsResponseMessage?.selectedDisplayText ||
    content?.listResponseMessage?.title ||
    ""
  );
}


function extensionForMime(mimeType) {
  const extensions = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
  };
  return extensions[mimeType] || "jpg";
}


export async function startWhatsApp({ sessionPath, apiClient, logger }) {
  const { state, saveCreds } = await useMultiFileAuthState(sessionPath);
  const processedIds = new Set();

  const socket = makeWASocket({
    auth: state,
    logger,
    browser: ["NUTRIA", "Chrome", "1.0.0"],
    markOnlineOnConnect: false,
    syncFullHistory: false,
  });

  socket.ev.on("creds.update", saveCreds);

  socket.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      logger.info("Pindai QR berikut dari WhatsApp > Perangkat tertaut");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "open") {
      logger.info("WhatsApp terhubung");
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      logger.warn({ statusCode, shouldReconnect }, "Koneksi WhatsApp terputus");
      if (shouldReconnect) {
        setTimeout(
          () => startWhatsApp({ sessionPath, apiClient, logger }),
          3000,
        );
      } else {
        logger.error("Session logout. Hapus folder session lalu pindai QR kembali.");
      }
    }
  });

  socket.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const message of messages) {
      const jid = message.key.remoteJid;
      const messageId = message.key.id;
      if (
        !jid ||
        !messageId ||
        message.key.fromMe ||
        jid === "status@broadcast" ||
        jid.endsWith("@g.us") ||
        processedIds.has(messageId)
      ) {
        continue;
      }

      processedIds.add(messageId);
      if (processedIds.size > 1000) {
        processedIds.delete(processedIds.values().next().value);
      }

      const content = unwrapMessage(message.message);
      const imageMessage = content?.imageMessage;
      const phoneNumber = await phoneFromMessage(message, socket, logger);
      if (!phoneNumber) {
        logger.warn({ messageId }, "Nomor telepon untuk WhatsApp LID tidak ditemukan");
        await socket.sendMessage(
          jid,
          { text: IDENTITY_ERROR },
          { quoted: message },
        );
        continue;
      }
      if (!imageMessage) {
        const text = incomingText(content).trim();
        if (text) {
          try {
            const replyMessage = await apiClient.chat({
              phoneNumber,
              userName: message.pushName || undefined,
              message: text,
            });
            await socket.sendMessage(
              jid,
              { text: replyMessage },
              { quoted: message },
            );
          } catch (error) {
            const reply =
              error instanceof BackendResponseError
                ? error.userMessage
                : SYSTEM_BUSY;
            logger.error({ err: error, messageId }, "Gagal memproses chat");
            await socket.sendMessage(jid, { text: reply }, { quoted: message });
          }
          continue;
        }
        await socket.sendMessage(jid, { text: TEXT_INSTRUCTION }, { quoted: message });
        continue;
      }

      let imageBuffer;
      try {
        imageBuffer = await downloadMediaMessage(
          message,
          "buffer",
          {},
          {
            logger,
            reuploadRequest: socket.updateMediaMessage,
          },
        );
      } catch (error) {
        logger.error({ err: error, messageId }, "Gagal mengunduh gambar WhatsApp");
        await socket.sendMessage(jid, { text: DOWNLOAD_ERROR }, { quoted: message });
        continue;
      }

      try {
        const mimeType = imageMessage.mimetype || "image/jpeg";
        const formattedMessage = await apiClient.analyzeFood({
          phoneNumber,
          userName: message.pushName || undefined,
          imageBuffer,
          mimeType,
          fileName: `meal-${messageId}.${extensionForMime(mimeType)}`,
        });
        await socket.sendMessage(
          jid,
          { text: formattedMessage },
          { quoted: message },
        );
      } catch (error) {
        if (error instanceof BackendResponseError) {
          logger.warn({ messageId, err: error.message }, "Backend menolak analisis");
          await socket.sendMessage(
            jid,
            { text: error.userMessage },
            { quoted: message },
          );
        } else {
          const logMessage =
            error instanceof BackendUnavailableError
              ? "Backend tidak tersedia"
              : "Gateway gagal memproses gambar";
          logger.error({ err: error, messageId }, logMessage);
          await socket.sendMessage(jid, { text: SYSTEM_BUSY }, { quoted: message });
        }
      }
    }
  });

  return socket;
}

import assert from "node:assert/strict";
import test from "node:test";

import { phoneFromMessage } from "../src/whatsapp.js";


const logger = { warn() {} };


test("uses the alternate PN instead of the remote LID", async () => {
  const message = {
    key: {
      remoteJid: "110432249499730@lid",
      remoteJidAlt: "6285643061598@s.whatsapp.net",
    },
  };
  const socket = {};

  assert.equal(
    await phoneFromMessage(message, socket, logger),
    "6285643061598",
  );
});


test("resolves a LID through the Baileys mapping store", async () => {
  const message = { key: { remoteJid: "110432249499730@lid" } };
  const socket = {
    signalRepository: {
      lidMapping: {
        async getPNForLID(lid) {
          assert.equal(lid, "110432249499730@lid");
          return "6285643061598@s.whatsapp.net";
        },
      },
    },
  };

  assert.equal(
    await phoneFromMessage(message, socket, logger),
    "6285643061598",
  );
});


test("does not send an unresolved LID as a phone number", async () => {
  const message = { key: { remoteJid: "110432249499730@lid" } };
  const socket = {
    signalRepository: {
      lidMapping: { async getPNForLID() { return null; } },
    },
  };

  assert.equal(await phoneFromMessage(message, socket, logger), null);
});

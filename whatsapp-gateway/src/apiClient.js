import axios from "axios";
import FormData from "form-data";


export class BackendUnavailableError extends Error {}

export class BackendResponseError extends Error {
  constructor(message) {
    super(message);
    this.userMessage = message;
  }
}


export function createApiClient({ backendUrl, timeoutMs }) {
  const client = axios.create({
    baseURL: backendUrl,
    timeout: timeoutMs,
    maxBodyLength: Infinity,
    maxContentLength: Infinity,
    validateStatus: () => true,
  });

  return {
    async analyzeFood({ phoneNumber, userName, imageBuffer, mimeType, fileName }) {
      const form = new FormData();
      form.append("phone_number", phoneNumber);
      if (userName) form.append("user_name", userName);
      form.append("image", imageBuffer, {
        filename: fileName,
        contentType: mimeType,
        knownLength: imageBuffer.length,
      });

      let response;
      try {
        response = await client.post("/analyze-food", form, {
          headers: form.getHeaders(),
        });
      } catch (error) {
        throw new BackendUnavailableError("Backend NUTRIA tidak dapat dihubungi", {
          cause: error,
        });
      }

      const body = response.data;
      if (response.status >= 200 && response.status < 300 && body?.success) {
        return body.formatted_message;
      }

      if (typeof body?.error_message === "string" && body.error_message) {
        throw new BackendResponseError(body.error_message);
      }
      throw new BackendUnavailableError(`Backend mengembalikan status ${response.status}`);
    },

    async chat({ phoneNumber, userName, message }) {
      let response;
      try {
        response = await client.post("/chat", {
          phone_number: phoneNumber,
          user_name: userName || null,
          message,
        });
      } catch (error) {
        throw new BackendUnavailableError("Backend NUTRIA tidak dapat dihubungi", {
          cause: error,
        });
      }

      const body = response.data;
      if (response.status >= 200 && response.status < 300 && body?.success) {
        return body.formatted_message;
      }
      if (typeof body?.formatted_message === "string" && body.formatted_message) {
        throw new BackendResponseError(body.formatted_message);
      }
      if (typeof body?.error_message === "string" && body.error_message) {
        throw new BackendResponseError(body.error_message);
      }
      throw new BackendUnavailableError(`Backend mengembalikan status ${response.status}`);
    },
  };
}

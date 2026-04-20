const BASE_URL = "";

class ApiError extends Error {
  constructor(error, message, status) {
    super(message);
    this.name = "ApiError";
    this.error = error;
    this.message = message;
    this.status = status;
  }
}

async function handleResponse(response) {
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      body = { error: response.statusText, message: response.statusText };
    }
    throw new ApiError(
      body.error || response.statusText,
      body.message || response.statusText,
      response.status
    );
  }
  const text = await response.text();
  if (!text) return null;
  return JSON.parse(text);
}

function buildUrl(path) {
  return `${BASE_URL}${path}`;
}

const api = {
  async get(path) {
    const response = await fetch(buildUrl(path), {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    return handleResponse(response);
  },

  async post(path, body) {
    const response = await fetch(buildUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  async put(path, body) {
    const response = await fetch(buildUrl(path), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  async patch(path, body) {
    const response = await fetch(buildUrl(path), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  async delete(path) {
    const response = await fetch(buildUrl(path), {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
    });
    return handleResponse(response);
  },

  async upload(path, file) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(buildUrl(path), {
      method: "POST",
      body: formData,
    });
    return handleResponse(response);
  },

  async stream(path, body, onEvent) {
    const response = await fetch(buildUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      let errorBody;
      try {
        errorBody = await response.json();
      } catch {
        errorBody = { error: response.statusText, message: response.statusText };
      }
      throw new ApiError(
        errorBody.error || response.statusText,
        errorBody.message || response.statusText,
        response.status
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("data:")) {
          const data = trimmed.slice(5).trim();
          if (data === "[DONE]") return;
          try {
            onEvent(JSON.parse(data));
          } catch {
            onEvent(data);
          }
        }
      }
    }

    if (buffer.trim().startsWith("data:")) {
      const data = buffer.trim().slice(5).trim();
      if (data !== "[DONE]") {
        try {
          onEvent(JSON.parse(data));
        } catch {
          onEvent(data);
        }
      }
    }
  },
};

export default api;

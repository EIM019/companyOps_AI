const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000/api";

export function getToken() {
  return localStorage.getItem("companyops_token") || "";
}

export function setToken(token) {
  localStorage.setItem("companyops_token", token);
}

export function clearToken() {
  localStorage.removeItem("companyops_token");
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  return parseResponse(response);
}

export async function streamChat(threadId, content, onToken) {
  const response = await fetch(`${API_BASE}/chat/threads/${threadId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${getToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });

  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || "Chat request failed");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = { pending_actions: [] };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "token") onToken(event.content);
      if (event.type === "done") final = event;
    }
  }

  return final;
}

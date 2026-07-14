const API_URL =
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

async function parseResponse(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const message =
      data?.detail ||
      data?.message ||
      "An unexpected backend error occurred.";

    throw new Error(message);
  }

  return data;
}

export async function sendChatMessage({
  question,
  agent,
  conversationId,
}) {
  const response = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      agent,
      conversation_id: conversationId,
    }),
  });

  return parseResponse(response);
}

export async function uploadFiles({ files, agent }) {
  const formData = new FormData();

  formData.append("agent", agent);

  files.forEach((file) => {
    formData.append("files", file);
  });

  const response = await fetch(`${API_URL}/api/files`, {
    method: "POST",
    body: formData,
  });

  return parseResponse(response);
}

export async function getConversations() {
  const response = await fetch(`${API_URL}/api/conversations`);
  return parseResponse(response);
}

export async function createConversation({ title, agent }) {
  const response = await fetch(`${API_URL}/api/conversations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title,
      agent,
    }),
  });

  return parseResponse(response);
}

export async function deleteConversation(conversationId) {
  const response = await fetch(
    `${API_URL}/api/conversations/${conversationId}`,
    {
      method: "DELETE",
    },
  );

  if (!response.ok) {
    return parseResponse(response);
  }

  return null;
}

export async function getConversationMessages(conversationId) {
  const response = await fetch(
    `${API_URL}/api/conversations/${conversationId}/messages`,
  );

  return parseResponse(response);
}

export async function createMessage({
  conversationId,
  role,
  content = "",
  structured = null,
  attachments = [],
}) {
  const response = await fetch(
    `${API_URL}/api/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        role,
        content,
        structured,
        attachments,
      }),
    },
  );

  return parseResponse(response);
}
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
  referencedDocumentIds = [],
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
      referenced_document_ids: referencedDocumentIds,
    }),
  });

  return parseResponse(response);
}

export async function uploadFiles({
  files,
  agent,
  conversationId,
}) {
  const formData = new FormData();

  formData.append("conversation_id", conversationId);
  formData.append("agent", agent);

  files.forEach((file) => {
    formData.append("files", file);
  });

  const response = await fetch(
    `${API_URL}/api/documents/upload`,
    {
      method: "POST",
      body: formData,
    },
  );

  return parseResponse(response);
}

export async function getDocuments({
  agent = null,
  status = null,
} = {}) {
  const params = new URLSearchParams();

  if (agent) {
    params.set("agent", agent);
  }

  if (status) {
    params.set("status", status);
  }

  const query = params.toString();
  const response = await fetch(
    `${API_URL}/api/documents${query ? `?${query}` : ""}`,
  );

  return parseResponse(response);
}

export async function getConversationDocuments(conversationId) {
  const response = await fetch(
    `${API_URL}/api/documents/conversation/${conversationId}`,
  );

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

export async function updateConversation(conversationId, updates) {
  const response = await fetch(`${API_URL}/api/conversations/${conversationId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(updates),
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

export async function getAdminAnalysisBlocks() {
  const response = await fetch(`${API_URL}/api/admin/analysis-blocks`);
  return parseResponse(response);
}

export async function getAdminDocuments(filters = {}) {
  const params = new URLSearchParams();

  Object.entries(filters).forEach(([key, value]) => {
    if (value) {
      params.set(key, value);
    }
  });

  const query = params.toString();
  const response = await fetch(
    `${API_URL}/api/admin/documents${query ? `?${query}` : ""}`,
  );

  return parseResponse(response);
}

export async function getAdminDocumentDetail(documentId) {
  const response = await fetch(
    `${API_URL}/api/admin/documents/${documentId}`,
  );

  return parseResponse(response);
}

export async function getAdminLogStory(documentId, { failedOnly = false } = {}) {
  const params = new URLSearchParams();

  if (failedOnly) {
    params.set("failed_only", "true");
  }

  const response = await fetch(
    `${API_URL}/api/admin/documents/${documentId}/log-story?${params.toString()}`,
  );

  return parseResponse(response);
}

// export async function reindexAdminDocument(documentId) {
//   const response = await fetch(
//     `${API_URL}/api/admin/documents/${documentId}/reindex`,
//     {
//       method: "POST",
//     },
//   );
//
//   return parseResponse(response);
// }

export async function deleteAdminDocument(documentId) {
  const response = await fetch(
    `${API_URL}/api/admin/documents/${documentId}`,
    {
      method: "DELETE",
    },
  );

  return parseResponse(response);
}

export function getAdminDocumentDownloadUrl(documentId) {
  return `${API_URL}/api/admin/documents/${documentId}/download`;
}

export function getAdminDocumentViewUrl(documentId) {
  return `${API_URL}/api/admin/documents/${documentId}/view`;
}

export function getAdminDocumentPagePreviewUrl(documentId, { page, search } = {}) {
  const params = new URLSearchParams();

  if (page !== undefined && page !== null) {
    params.set("page", page);
  }

  if (search) {
    params.set("search", search);
  }

  const query = params.toString();

  return `${API_URL}/api/admin/documents/${documentId}/page-preview${query ? `?${query}` : ""}`;
}

export function getAdminDocumentHighlightedViewUrl(documentId, { page, search } = {}) {
  const params = new URLSearchParams();

  if (page !== undefined && page !== null) {
    params.set("page", page);
  }

  if (search) {
    params.set("search", search);
  }

  const query = params.toString();

  return `${API_URL}/api/admin/documents/${documentId}/highlighted-view${query ? `?${query}` : ""}`;
}

export function getAdminDocumentPreviewUrl(documentId) {
  return `${API_URL}/api/admin/documents/${documentId}/preview`;
}

export function getAdminLogStoryPdfUrl(documentId, { failedOnly = false } = {}) {
  const params = new URLSearchParams();

  if (failedOnly) {
    params.set("failed_only", "true");
  }

  const query = params.toString();
  return `${API_URL}/api/admin/documents/${documentId}/log-story/pdf${query ? `?${query}` : ""}`;
}

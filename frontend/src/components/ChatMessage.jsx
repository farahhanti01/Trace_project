import React, { useRef, useState } from 'react'
import AIResponseCard from './AIResponseCard'
// import { User, Sparkles } from "lucide-react";
import {
  Check,
  Copy,
  Download,
  FileText,
  Pencil,
  RotateCcw,
  ThumbsDown,
  ThumbsUp,
  User,
} from "lucide-react";
import { getAdminDocumentViewUrl } from "../services/api";

export default function ChatMessage({
  message,
  onEditPrompt,
  onDocumentPreview,
  onRetry,
  onSuggestedPrompt,
}) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const assistantResponseRef = useRef(null);
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  const imageAttachments = attachments.filter((file) => attachmentImageUrl(file));
  const documentAttachments = attachments.filter((file) => !attachmentImageUrl(file));
  const structured = normalizeStructuredPayload(message.structured);
  const messageContent = displayText(message.content);

  async function copyText(content) {
    try {
      await navigator.clipboard.writeText(content);
    } catch (_error) {
      const textarea = document.createElement("textarea");
      textarea.value = content;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }

    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  async function handleCopyPrompt() {
    await copyText(messageContent);
  }

  async function handleCopyResponse() {
    const renderedContent = assistantResponseRef.current?.innerText?.trim();
    const fallbackContent = messageContent.trim()
      || (structured ? JSON.stringify(structured, null, 2) : "");

    await copyText((renderedContent || fallbackContent).replace(/\n{3,}/g, "\n\n"));
  }

  function handleExportJson() {
    const payload = structured ?? {
      role: message.role,
      content: messageContent,
    };
    const blob = new Blob(
      [JSON.stringify(payload, null, 2)],
      { type: "application/json;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `trace-response-${message.id ?? Date.now()}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

    if (message.role === "user") {
    return (
      <div className="chat-message chat-message--user">
        <div className="chat-message__user-content">
          {documentAttachments.length > 0 && (
            <button
              type="button"
              className="chat-message__attachments-toggle"
              onClick={() => setAttachmentsOpen((value) => !value)}
              aria-expanded={attachmentsOpen}
              title="Afficher les fichiers uploades"
              aria-label="Afficher les fichiers uploades"
            >
              <FileText className="chat-message__attachments-toggle-icon" />
              <span className="chat-message__attachments-count">
                {documentAttachments.length}
              </span>
            </button>
          )}

          {attachmentsOpen && documentAttachments.length > 0 && (
            <div className="chat-message__attachments">
              {documentAttachments.map((file, index) => (
                <div
                  key={`${file.name}-${index}`}
                  className="chat-attachment"
                >
                  <FileText className="chat-attachment__icon" />

                  <div className="chat-attachment__details">
                    <span className="chat-attachment__name">
                      {file.name}
                    </span>

                    <span className="chat-attachment__size">
                      {formatFileSize(file.size)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {imageAttachments.length > 0 && (
            <div className="chat-message__image-attachments">
              {imageAttachments.map((file, index) => (
                <a
                  key={`${file.document_id ?? file.name}-${index}`}
                  className="chat-image-attachment"
                  href={attachmentImageUrl(file)}
                  target="_blank"
                  rel="noreferrer"
                  title={file.name}
                >
                  <img
                    src={attachmentImageUrl(file)}
                    alt={file.name || "Capture uploadée"}
                    className="chat-image-attachment__image"
                    loading="lazy"
                  />

                  <span className="chat-image-attachment__caption">
                    {file.name}
                  </span>
                </a>
              ))}
            </div>
          )}

          <div className="chat-message__bubble chat-message__bubble--user">
            {messageContent}
          </div>
          <div className="chat-message__actions">
            <button
              type="button"
              className="chat-message__action"
              onClick={handleCopyPrompt}
              title="Copier le prompt"
              aria-label="Copier le prompt"
            >
              {copied ? (
                <Check className="chat-message__action-icon" />
              ) : (
                <Copy className="chat-message__action-icon" />
              )}
              {/* <span>{copied ? "Copie" : }</span> */}
            </button>

            <button
              type="button"
              className="chat-message__action"
              onClick={() => onEditPrompt?.(messageContent)}
              title="Editer le prompt"
              aria-label="Editer le prompt"
            >
              <Pencil className="chat-message__action-icon" />
              {/* <span>Editer</span> */}
            </button>
          </div>
        </div>

        <div className="chat-message__avatar chat-message__avatar--user">
          <User className="chat-message__icon" />
        </div>
      </div>
    );
  }

  function formatFileSize(size) {
  if (!size) return "";

  if (size < 1024) {
    return `${size} B`;
  }

  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }

  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

  const isWideAssistantResponse = Array.isArray(structured?.transactions)
    && structured.transactions.length > 0
  const suggestedPrompts = buildSuggestedPrompts(structured, messageContent);

  return (
    <div className={`chat-message chat-message--assistant${isWideAssistantResponse ? ' chat-message--wide' : ''}`}>
      <div className="chat-message__content"> 
        <div ref={assistantResponseRef} className="chat-message__assistant-response">
          {messageContent.trim() && (
            <p className="chat-message__text">
                {messageContent}
            </p>
        )}
          {structured && (
            <ResponseErrorBoundary>
              <AIResponseCard
                data={structured}
                onDocumentPreview={onDocumentPreview}
              />
            </ResponseErrorBoundary>
          )}
        </div>

        {(messageContent.trim() || structured) && (
          <div className="chat-message__assistant-followup">
            <div className="chat-message__assistant-toolbar">
              <div className="chat-message__assistant-tools">
                {/* <button
                  type="button"
                  className="chat-message__assistant-tool"
                  onClick={handleCopyResponse}
                >
                  {copied ? (
                    <Check className="chat-message__assistant-tool-icon" />
                  ) : (
                    <Copy className="chat-message__assistant-tool-icon" />
                  )}
                  <span>{copied ? "Copié" : "Copier"}</span>
                </button> */}

                {/* <button
                  type="button"
                  className="chat-message__assistant-tool"
                  onClick={handleExportJson}
                > */}
                  {/* <Download className="chat-message__assistant-tool-icon" />
                  <span>Exporter JSON</span>
                </button> */}

                {/* {onRetry && (
                  <button
                    type="button"
                    className="chat-message__assistant-tool"
                    onClick={onRetry}
                  >
                    <RotateCcw className="chat-message__assistant-tool-icon" />
                    <span>Relancer</span>
                  </button>
                )} */}
              </div>

              <div className="chat-message__assistant-feedback">
                {/* <button
                  type="button"
                  className={`chat-message__feedback-button${feedback === "up" ? " chat-message__feedback-button--active" : ""}`}
                  onClick={() => setFeedback((value) => value === "up" ? null : "up")}
                  aria-label="Réponse utile"
                  title="Réponse utile"
                >
                  <ThumbsUp className="chat-message__assistant-tool-icon" />
                </button> */}

                {/* <button
                  type="button"
                  className={`chat-message__feedback-button${feedback === "down" ? " chat-message__feedback-button--active" : ""}`}
                  onClick={() => setFeedback((value) => value === "down" ? null : "down")}
                  aria-label="Réponse à améliorer"
                  title="Réponse à améliorer"
                >
                  <ThumbsDown className="chat-message__assistant-tool-icon" />
                </button> */}
              </div>
            </div>

            {suggestedPrompts.length > 0 && (
              <div className="chat-message__suggestions">
                {suggestedPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="chat-message__suggestion"
                    onClick={() => onSuggestedPrompt?.(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function buildSuggestedPrompts(structured, content) {
  const text = `${content ?? ""} ${JSON.stringify(structured ?? {})}`.toLowerCase();

  if (Array.isArray(structured?.transactions) && structured.transactions.length > 0) {
    return [
      "Explique les erreurs détectées",
      "Analyse uniquement les traitements HSM",
      "Compare avec une autre trace",
    ];
  }

  if (text.includes("field 039") || text.includes("response code")) {
    return [
      "Donne tous les codes du Field 039",
      "Explique le code réponse 039",
      "Compare les codes 00, 51 et 55",
    ];
  }

  if (text.includes("hsm") || text.includes("ed01") || text.includes("pvv")) {
    return [
      "Explique le code HSM détecté",
      "Montre la séquence HSM complète",
      "Pourquoi la vérification a échoué ?",
    ];
  }

  if (Array.isArray(structured?.sections) && structured.sections.length > 0) {
    return [
      "Donne un exemple concret",
      "Résume en points clés",
      "Cite les références exactes",
    ];
  }

  return [
    "Résume les points clés",
    "Donne plus de détails",
    "Quelles questions poser ensuite ?",
  ];
}

function normalizeStructuredPayload(value) {
  if (!value) return null;

  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (_error) {
      return null;
    }
  }

  return typeof value === "object" ? value : null;
}

function displayText(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);

  try {
    return JSON.stringify(value, null, 2);
  } catch (_error) {
    return String(value);
  }
}

function isImageAttachment(file) {
  const type = String(file?.type ?? "").toLowerCase();
  const name = String(file?.name ?? "").toLowerCase();

  return (
    type.startsWith("image/")
    || /\.(png|jpe?g|webp|bmp|tiff?)$/.test(name)
  );
}

function attachmentImageUrl(file) {
  const documentId = file?.document_id ?? file?.documentId ?? file?.id;

  if (!documentId || !isImageAttachment(file)) {
    return "";
  }

  return getAdminDocumentViewUrl(documentId);
}

class ResponseErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error) {
    console.error("Failed to render historical assistant response:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="response-card">
          <div className="response-section">
            <p className="response-card__paragraph">
              Cette ancienne reponse ne peut pas etre affichee avec le format actuel.
            </p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

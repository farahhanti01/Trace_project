import React, { useRef, useState } from 'react'
import AIResponseCard from './AIResponseCard'
// import { User, Sparkles } from "lucide-react";
import {
  Check,
  Copy,
  FileText,
  Pencil,
  Sparkles,
  User,
} from "lucide-react";

export default function ChatMessage({ message, onEditPrompt }) {
  const [copied, setCopied] = useState(false);
  const assistantResponseRef = useRef(null);

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
    await copyText(message.content ?? "");
  }

  async function handleCopyResponse() {
    const renderedContent = assistantResponseRef.current?.innerText?.trim();
    const fallbackContent = message.content?.trim()
      || (message.structured ? JSON.stringify(message.structured, null, 2) : "");

    await copyText((renderedContent || fallbackContent).replace(/\n{3,}/g, "\n\n"));
  }

    if (message.role === "user") {
    return (
      <div className="chat-message chat-message--user">
        <div className="chat-message__user-content">
          {message.attachments?.length > 0 && (
            <div className="chat-message__attachments">
              {message.attachments.map((file, index) => (
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

          <div className="chat-message__bubble chat-message__bubble--user">
            {message.content}
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
              onClick={() => onEditPrompt?.(message.content ?? "")}
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

  return (
    <div className="chat-message chat-message--assistant">
      <div className="chat-message__avatar chat-message__avatar--assistant"><Sparkles className="chat-message__icon" /></div>
      <div className="chat-message__content"> 
        <div ref={assistantResponseRef} className="chat-message__assistant-response">
          {message.content?.trim() && (
            <p className="chat-message__text">
                {message.content}
            </p>
        )}
          {message.structured && <AIResponseCard data={message.structured} />}
        </div>

        {(message.content?.trim() || message.structured) && (
          <div className="chat-message__assistant-actions">
            <button
              type="button"
              className="chat-message__action chat-message__action--assistant"
              onClick={handleCopyResponse}
              title="Copier la reponse"
              aria-label="Copier la reponse"
            >
              {copied ? (
                <Check className="chat-message__action-icon" />
              ) : (
                <Copy className="chat-message__action-icon" />
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

import React from 'react'
import AIResponseCard from './AIResponseCard'
// import { User, Sparkles } from "lucide-react";
import {
  FileText,
  Sparkles,
  User,
} from "lucide-react";

export default function ChatMessage({ message }) {
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
        {message.content?.trim() && (
          <p className="chat-message__text">
              {message.content}
          </p>
      )}
        {message.structured && <AIResponseCard data={message.structured} />}
      </div>
    </div>
  )
}

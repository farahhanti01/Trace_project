import React from 'react'
import { Sparkles, User } from 'lucide-react'
import AIResponseCard from './AIResponseCard'
// import { User, Sparkles } from "lucide-react";

export default function ChatMessage({ message }) {
  if (message.role === 'user') {
    return (
      <div className="chat-message chat-message--user">
        <div className="chat-message__bubble chat-message__bubble--user">{message.content}</div>
        <div className="chat-message__avatar chat-message__avatar--user"><User className="chat-message__icon" /></div>
      </div>
    )
  }

  return (
    <div className="chat-message chat-message--assistant">
      <div className="chat-message__avatar chat-message__avatar--assistant"><Sparkles className="chat-message__icon" /></div>
      <div className="chat-message__content">
        {message.content && <p className="chat-message__text">{message.content}</p>}
        {message.structured && <AIResponseCard data={message.structured} />}
      </div>
    </div>
  )
}

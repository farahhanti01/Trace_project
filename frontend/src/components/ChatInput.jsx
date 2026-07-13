import React, { forwardRef, useRef } from 'react'
import { Paperclip, ArrowUp } from 'lucide-react'
import { Button } from './ui/Button'

export const MessageInput = forwardRef(function MessageInput({ value, onChange, onSubmit, onAttach, disabled, placeholder = 'Ask about a specification, transaction flow, or log…' }, ref) {
  const fileRef = useRef(null)

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!disabled && value.trim()) onSubmit()
    }
  }

  return (
    <div className="composer">
      <div className="composer__box">
        <button type="button" onClick={() => fileRef.current?.click()} className="composer__attach">
          <Paperclip className="composer__attach-icon" />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.log"
          className="composer__file-input"
          onChange={(e) => {
            if (e.target.files && onAttach) onAttach(e.target.files)
            e.target.value = ''
          }}
        />
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          placeholder={placeholder}
          className="composer__input"
          style={{ minHeight: 36 }}
        />
        <Button
          type="button"
          size="icon"
          disabled={disabled || !value.trim()}
          onClick={onSubmit}
          className="composer__send"
        >
          <ArrowUp className="composer__send-icon" strokeWidth={2.5} />
        </Button>
      </div>
      <div className="composer__help">TRACE AI can make mistakes. Verify critical outputs against source documentation.</div>
    </div>
  )
})

export default MessageInput

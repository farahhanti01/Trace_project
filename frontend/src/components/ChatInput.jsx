import React, {
  forwardRef,
  useRef,
} from "react";

import {
  ArrowUp,
  FileText,
  Paperclip,
  X,
} from "lucide-react";

import { Button } from "./ui/Button";


export const MessageInput = forwardRef(
  function MessageInput(
    {
      value,
      onChange,
      onSubmit,
      onAttach,
      onRemoveFile,
      disabled,
      files = [],
      placeholder =
        "Ask about a specification, transaction flow, or log…",
    },
    ref,
  ) {
    const fileInputRef = useRef(null);

    function handleKeyDown(event) {
      if (
        event.key === "Enter" &&
        !event.shiftKey
      ) {
        event.preventDefault();

        if (!disabled && value.trim()) {
          onSubmit();
        }
      }
    }

    function handleFileChange(event) {
      const selectedFiles = event.target.files;

      if (selectedFiles?.length) {
        onAttach(selectedFiles);
      }

      /*
       * Allows selecting the same file again.
       */
      event.target.value = "";
    }

    function openFilePicker() {
      fileInputRef.current?.click();
    }

    return (
      <div className="composer">
        {files.length > 0 && (
          <div className="composer__pending-files">
            {files.map((file, index) => (
              <div
                key={`${file.name}-${file.lastModified}-${index}`}
                className="composer__pending-file"
              >
                <FileText className="composer__pending-file-icon" />

                <div className="composer__pending-file-details">
                  <span
                    className="composer__pending-file-name"
                    title={file.name}
                  >
                    {file.name}
                  </span>

                  <span className="composer__pending-file-size">
                    {formatFileSize(file.size)}
                  </span>
                </div>

                {onRemoveFile && (
                  <button
                    type="button"
                    className="composer__pending-file-remove"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => onRemoveFile(index)}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="composer__box">
          <button
            type="button"
            className="composer__attach"
            aria-label="Attach files"
            title="Attach files"
            disabled={disabled}
            onClick={openFilePicker}
          >
            <Paperclip className="composer__attach-icon" />
          </button>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.log"
            className="composer__file-input"
            onChange={handleFileChange}
          />

          <textarea
            ref={ref}
            value={value}
            rows={1}
            placeholder={placeholder}
            className="composer__input"
            disabled={disabled}
            onChange={(event) =>
              onChange(event.target.value)
            }
            onKeyDown={handleKeyDown}
          />

          <Button
            type="button"
            size="icon"
            className="composer__send"
            disabled={disabled || !value.trim()}
            onClick={onSubmit}
          >
            <ArrowUp
              className="composer__send-icon"
              strokeWidth={2.5}
            />
          </Button>
        </div>

        <div className="composer__help">
          TRACE AI can make mistakes. Verify critical outputs
          against source documentation.
        </div>
      </div>
    );
  },
);


function formatFileSize(sizeInBytes) {
  if (!Number.isFinite(sizeInBytes)) {
    return "";
  }

  if (sizeInBytes < 1024) {
    return `${sizeInBytes} B`;
  }

  if (sizeInBytes < 1024 * 1024) {
    return `${(sizeInBytes / 1024).toFixed(1)} KB`;
  }

  return `${(
    sizeInBytes /
    1024 /
    1024
  ).toFixed(2)} MB`;
}


export default MessageInput;
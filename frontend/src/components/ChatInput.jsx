import React, {
  forwardRef,
  useMemo,
  useRef,
  useState,
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
      onReferenceSelect,
      onRemoveReference,
      disabled,
      files = [],
      referenceDocuments = [],
      selectedReferences = [],
      enableReferences = false,
      placeholder =
        "Ask about a specification, transaction flow, or log...",
    },
    ref,
  ) {
    const fileInputRef = useRef(null);
    const [referenceQuery, setReferenceQuery] = useState("");
    const [showReferences, setShowReferences] = useState(false);

    const referenceMatches = useMemo(() => {
      const query = referenceQuery.toLowerCase();

      return referenceDocuments
        .filter((document) => {
          const alreadySelected = selectedReferences.some(
            (reference) => reference.id === document.id,
          );

          if (alreadySelected) {
            return false;
          }

          return document.original_filename
            .toLowerCase()
            .includes(query);
        })
        .slice(0, 8);
    }, [
      referenceDocuments,
      referenceQuery,
      selectedReferences,
    ]);

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

      event.target.value = "";
    }

    function openFilePicker() {
      fileInputRef.current?.click();
    }

    function handleInputChange(event) {
      const nextValue = event.target.value;
      onChange(nextValue);

      if (!enableReferences) {
        setShowReferences(false);
        return;
      }

      const match = nextValue.match(/(?:^|\s)#([^\s#]*)$/);

      if (match) {
        setReferenceQuery(match[1] ?? "");
        setShowReferences(true);
      } else {
        setShowReferences(false);
      }
    }

    function selectReference(document) {
      const nextValue = value.replace(
        /(?:^|\s)#([^\s#]*)$/,
        (match) => {
          const prefix = match.startsWith(" ") ? " " : "";
          return `${prefix}#${document.original_filename} `;
        },
      );

      onChange(nextValue);
      onReferenceSelect?.(document);
      setShowReferences(false);
      setReferenceQuery("");
    }

    return (
      <div className="composer">
        {selectedReferences.length > 0 && (
          <div className="composer__reference-files">
            {selectedReferences.map((document) => (
              <div
                key={document.id}
                className="composer__reference-file"
              >
                <FileText className="composer__pending-file-icon" />

                <span
                  className="composer__reference-file-name"
                  title={document.original_filename}
                >
                  #{document.original_filename}
                </span>

                {onRemoveReference && (
                  <button
                    type="button"
                    className="composer__pending-file-remove"
                    aria-label={`Remove ${document.original_filename}`}
                    onClick={() => onRemoveReference(document.id)}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

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

        <div className="composer__box-wrapper">
          {enableReferences && showReferences && (
            <div className="composer__reference-menu">
              {referenceMatches.length > 0 ? (
                referenceMatches.map((document) => (
                  <button
                    key={document.id}
                    type="button"
                    className="composer__reference-option"
                    onClick={() => selectReference(document)}
                  >
                    <FileText className="composer__reference-option-icon" />

                    <span className="composer__reference-option-name">
                      {document.original_filename}
                    </span>
                  </button>
                ))
              ) : (
                <div className="composer__reference-empty">
                  No documentation file found
                </div>
              )}
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
              accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.log,.trc,.trc019,.trc068,text/plain"
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
              onChange={handleInputChange}
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

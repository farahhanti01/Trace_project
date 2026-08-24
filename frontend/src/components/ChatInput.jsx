import React, {
  forwardRef,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ArrowUp,
  ChevronDown,
  FileText,
  Paperclip,
  X,
} from "lucide-react";

import { Button } from "./ui/Button";


function isImageFile(file) {
  return String(file?.type ?? "").startsWith("image/");
}


function documentName(document) {
  return String(
    document?.original_filename
    ?? document?.name
    ?? document?.stored_filename
    ?? "Document",
  );
}


function extensionFromMimeType(mimeType) {
  const normalized = String(mimeType ?? "").toLowerCase();

  if (normalized === "image/jpeg") return "jpg";
  if (normalized === "image/webp") return "webp";
  if (normalized === "image/bmp") return "bmp";
  if (normalized === "image/tiff") return "tiff";

  return "png";
}


function pastedImageName(file, index) {
  const extension = extensionFromMimeType(file.type);
  const timestamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..+$/, "");

  return `screenshot-${timestamp}-${index + 1}.${extension}`;
}


function PendingImagePreview({ file }) {
  const [previewUrl, setPreviewUrl] = useState("");

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  if (!previewUrl) {
    return <FileText className="composer__pending-file-icon" />;
  }

  return (
    <img
      src={previewUrl}
      alt=""
      className="composer__pending-image"
    />
  );
}


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
      agents = {},
      activeAgent = "",
      onAgentChange,
      placeholder =
        "Pose une question sur une spécification, un flux ou une trace...",
    },
    ref,
  ) {
    const fileInputRef = useRef(null);
    const [referenceQuery, setReferenceQuery] = useState("");
    const [showReferences, setShowReferences] = useState(false);

    const referenceMatches = useMemo(() => {
      const query = referenceQuery.toLowerCase();

      return referenceDocuments.filter((document) => {
          const name = documentName(document);
          const alreadySelected = selectedReferences.some(
            (reference) => reference.id === document.id,
          );

          if (alreadySelected) {
            return false;
          }

          return name
            .toLowerCase()
            .includes(query);
        });
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

        if (
          !disabled
          && (
            value.trim()
            || files.length > 0
            || selectedReferences.length > 0
          )
        ) {
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

    function handlePaste(event) {
      if (disabled) return;

      const items = Array.from(event.clipboardData?.items ?? []);
      const pastedImages = items
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter((file) => file && isImageFile(file))
        .map((file, index) => new File(
          [file],
          file.name || pastedImageName(file, index),
          {
            type: file.type || "image/png",
            lastModified: Date.now(),
          },
        ));

      if (pastedImages.length === 0) {
        return;
      }

      event.preventDefault();
      onAttach(pastedImages);
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

      const match = nextValue.match(/(?:^|\s)@([^\s@]*)$/);

      if (match) {
        setReferenceQuery(match[1] ?? "");
        setShowReferences(true);
      } else {
        setShowReferences(false);
      }
    }

    function selectReference(document) {
      const nextValue = value.replace(
        /(?:^|\s)@([^\s@]*)$/,
        (match) => {
          const prefix = match.startsWith(" ") ? " " : "";
          return prefix;
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
                  title={documentName(document)}
                >
                  @{documentName(document)}
                </span>

                {onRemoveReference && (
                  <button
                    type="button"
                    className="composer__pending-file-remove"
                    aria-label={`Remove ${documentName(document)}`}
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
                {isImageFile(file) ? (
                  <PendingImagePreview file={file} />
                ) : (
                  <FileText className="composer__pending-file-icon" />
                )}

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
                      {documentName(document)}
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
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.log,.trc,.trc019,.trc068,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,text/plain,image/*"
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
              onPaste={handlePaste}
            />

            <div className="composer__actions">
              <div className="composer__actions-left">
                <button
                  type="button"
                  className="composer__attach"
                  aria-label="Attach files"
                  title="Attach files"
                  disabled={disabled}
                  onClick={openFilePicker}
                >
                  <Paperclip className="composer__attach-icon" />
                  <span>Joindre un document</span>
                </button>

                {Object.keys(agents).length > 0 && (
                  <label className="composer__agent-select">
                    <span>
                      {agents[activeAgent]?.name ?? "Agent"}
                    </span>
                    <ChevronDown className="composer__agent-select-icon" />
                    <select
                      value={activeAgent}
                      onChange={(event) =>
                        onAgentChange?.(event.target.value)
                      }
                      disabled={disabled}
                      aria-label="Choisir un agent"
                    >
                      {Object.entries(agents).map(([id, agent]) => (
                        <option key={id} value={id}>
                          {agent.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>

              <Button
                type="button"
                size="icon"
                className="composer__send"
                disabled={
                  disabled
                  || (
                    !value.trim()
                    && files.length === 0
                    && selectedReferences.length === 0
                  )
                }
                onClick={onSubmit}
              >
                <ArrowUp
                  className="composer__send-icon"
                  strokeWidth={2.5}
                />
              </Button>
            </div>
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

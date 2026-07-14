import React, {
  useCallback,
  useRef,
  useState,
} from "react";

import {
  FileText,
  Upload,
  X,
} from "lucide-react";

import { Button } from "./ui/Button";


const ACCEPT =
  ".pdf,.doc,.docx,.xls,.xlsx,.txt,.log";

const MAX_FILE_SIZE = 20 * 1024 * 1024;


export default function UploadArea({
  files = [],
  onFilesChange = () => {},
}) {
  const inputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState("");


  const addFiles = useCallback(
    (fileList) => {
      if (!fileList) return;

      const selectedFiles = Array.from(fileList);

      const acceptedFiles = [];
      const rejectedFiles = [];

      selectedFiles.forEach((file) => {
        if (file.size > MAX_FILE_SIZE) {
          rejectedFiles.push(file.name);
          return;
        }

        acceptedFiles.push(file);
      });

      if (rejectedFiles.length > 0) {
        setError(
          `The following files exceed 20 MB: ${rejectedFiles.join(", ")}`,
        );
      } else {
        setError("");
      }

      onFilesChange([
        ...files,
        ...acceptedFiles,
      ]);
    },
    [files, onFilesChange],
  );


  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();

    setDragOver(false);

    addFiles(event.dataTransfer.files);
  }


  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();

    setDragOver(true);
  }


  function handleDragLeave(event) {
    event.preventDefault();
    event.stopPropagation();

    setDragOver(false);
  }


  function handleInputChange(event) {
    addFiles(event.target.files);

    /*
     * Reset the input so the same file can be selected again.
     */
    event.target.value = "";
  }


  function removeFile(indexToRemove) {
    const updatedFiles = files.filter(
      (_, index) => index !== indexToRemove,
    );

    onFilesChange(updatedFiles);

    if (updatedFiles.length === 0) {
      setError("");
    }
  }


  function openFilePicker() {
    inputRef.current?.click();
  }


  function formatFileSize(sizeInBytes) {
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


  return (
    <section className="panel panel--upload">
      <div className="panel__header">
        <div className="panel__label">
          Documents & Logs
        </div>

        <div className="panel__description">
          PDF, Word, Excel, TXT, LOG — up to 20 MB each.
        </div>
      </div>

      <div
        role="button"
        tabIndex={0}
        className={
          dragOver
            ? "upload-dropzone upload-dropzone--active"
            : "upload-dropzone"
        }
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={openFilePicker}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" ||
            event.key === " "
          ) {
            event.preventDefault();
            openFilePicker();
          }
        }}
      >
        <div className="upload-dropzone__icon">
          <Upload size={24} />
        </div>

        <div className="upload-dropzone__title">
          Drag & drop files here
        </div>

        <div className="upload-dropzone__subtitle">
          or click to browse from your computer
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="upload-dropzone__button"
          onClick={(event) => {
            event.stopPropagation();
            openFilePicker();
          }}
        >
          Browse Files
        </Button>

        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="upload-input"
          onChange={handleInputChange}
        />
      </div>

      {error && (
        <p
          className="upload-error"
          role="alert"
        >
          {error}
        </p>
      )}

      {files.length > 0 && (
        <ul className="upload-file-list">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${file.lastModified}-${index}`}
              className="upload-file-item"
            >
              <div className="upload-file-meta">
                <FileText className="upload-file-icon" />

                <span
                  className="upload-file-name"
                  title={file.name}
                >
                  {file.name}
                </span>

                <span className="upload-file-size">
                  {formatFileSize(file.size)}
                </span>
              </div>

              <button
                type="button"
                className="upload-file-remove"
                aria-label={`Remove ${file.name}`}
                title="Remove file"
                onClick={(event) => {
                  event.stopPropagation();
                  removeFile(index);
                }}
              >
                <X size={18} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
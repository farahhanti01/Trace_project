import React, { useRef, useState, useCallback } from 'react'
import { Upload, FileText, X } from 'lucide-react'
import { cn } from '../lib/utils'
import { Button } from './ui/Button'

const ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.txt,.log'

export default function UploadArea({ files, setFiles }) {
  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)

  const addFiles = (list) => {
    if (!list) return
    setFiles([...files, ...Array.from(list)])
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    addFiles(e.dataTransfer.files)
  }

  const remove = (idx) => {
    const next = [...files]
    next.splice(idx, 1)
    setFiles(next)
  }

  return (
    <section className="panel panel--upload">
      <div className="panel__header">
        <div className="panel__label">Documents & Logs</div>
        <div className="panel__description">PDF, Word, Excel, TXT, LOG — up to 20 MB each.</div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={dragOver ? 'upload-dropzone upload-dropzone--active' : 'upload-dropzone'}
      >
        <div className="upload-dropzone__icon"><Upload /></div>
        <div className="upload-dropzone__title">Drag & drop files here</div>
        <div className="upload-dropzone__subtitle">or click to browse from your computer</div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="upload-dropzone__button"
          onClick={(e) => { e.stopPropagation(); inputRef.current?.click() }}
        >
          Browse Files
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="upload-input"
          onChange={(e) => { addFiles(e.target.files); e.target.value = '' }}
        />
      </div>

      {files.length > 0 && (
        <ul className="upload-file-list">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="upload-file-item">
              <div className="upload-file-meta">
                <FileText className="upload-file-icon" />
                <span className="upload-file-name">{f.name}</span>
                <span className="upload-file-size">{(f.size / 1024).toFixed(1)} KB</span>
              </div>
              <button onClick={() => remove(i)} className="upload-file-remove" aria-label="Remove file">
                <X />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

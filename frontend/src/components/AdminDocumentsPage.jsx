import React, { useEffect, useMemo, useState } from "react";
import {
  Download,
  Eye,
  FileText,
  X,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";

import {
  deleteAdminDocument,
  getAdminAnalysisBlocks,
  getAdminDocumentDownloadUrl,
  getAdminDocumentPreviewUrl,
  getAdminDocumentViewUrl,
  getAdminDocuments,
  getAdminLogStoryPdfUrl,
  // reindexAdminDocument,
} from "../services/api";


const LOG_EXTENSIONS = new Set([".txt", ".log"]);


function formatDate(value) {
  if (!value) return "N/A";

  return new Intl.DateTimeFormat("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}


function formatSize(size) {
  if (!Number.isFinite(size)) return "N/A";

  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;

  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}


function isTrace(document) {
  return LOG_EXTENSIONS.has(document?.extension);
}


export default function AdminDocumentsPage({
  onBack,
}) {
  const [tab, setTab] = useState("blocks");
  const [blocks, setBlocks] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);

  async function loadAdminData() {
    setLoading(true);
    setError("");

    try {
      const [blocksData, documentsData] = await Promise.all([
        getAdminAnalysisBlocks(),
        getAdminDocuments(typeFilter ? { type: typeFilter } : {}),
      ]);

      setBlocks(blocksData);
      setDocuments(documentsData);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load admin documents.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAdminData();
  }, [typeFilter]);

  const filteredBlocks = useMemo(() => {
    const needle = query.trim().toLowerCase();

    if (!needle) return blocks;

    return blocks.filter((block) => (
      block.title?.toLowerCase().includes(needle)
      || block.documents?.some((document) =>
        document.original_filename?.toLowerCase().includes(needle),
      )
    ));
  }, [blocks, query]);

  const filteredDocuments = useMemo(() => {
    const needle = query.trim().toLowerCase();

    if (!needle) return documents;

    return documents.filter((document) => (
      document.original_filename?.toLowerCase().includes(needle)
      || document.extension?.toLowerCase().includes(needle)
      || document.agent?.toLowerCase().includes(needle)
    ));
  }, [documents, query]);

  function openDocument(document) {
    const viewUrl = (
      document.extension === ".pdf" || isTrace(document)
        ? getAdminDocumentViewUrl(document.id)
        : getAdminDocumentPreviewUrl(document.id)
    );

    setPreview({
      title: document.original_filename,
      url: viewUrl,
    });
  }

  function openLogStory(document, failedOnly = true) {
    window.open(
      getAdminLogStoryPdfUrl(document.id, { failedOnly }),
      "_blank",
      "noopener,noreferrer",
    );
  }

  // async function handleReindex(document) {
  //   await reindexAdminDocument(document.id);
  //   await loadAdminData();
  // }

  async function handleDelete(document) {
    const confirmed = window.confirm(
      `Delete ${document.original_filename}?`,
    );

    if (!confirmed) return;

    await deleteAdminDocument(document.id);
    await loadAdminData();
  }

  return (
    <section className="admin-page">
      <div className="admin-page__header">
        <div>
          <h1>Documents</h1>
          <p>Consultez les fichiers uploades, les blocs d'analyse et les Log Stories.</p>
        </div>

        <div className="admin-page__actions">
          <button type="button" onClick={loadAdminData}>
            <RefreshCw size={16} />
            Refresh
          </button>
          <button type="button" onClick={onBack}>
            Back to chat
          </button>
        </div>
      </div>

      <div className="admin-toolbar">
        <div className="admin-search">
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search file, trace, PDF, XLSX..."
          />
        </div>

        <select
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
        >
          <option value="">All types</option>
          <option value="trace">Traces</option>
          <option value="pdf">PDF</option>
          <option value="xlsx">XLSX</option>
          <option value="docx">DOCX</option>
        </select>
      </div>

      <div className="admin-tabs">
        <button
          type="button"
          className={tab === "blocks" ? "admin-tabs__button admin-tabs__button--active" : "admin-tabs__button"}
          onClick={() => setTab("blocks")}
        >
          Analysis Blocks
        </button>
        <button
          type="button"
          className={tab === "documents" ? "admin-tabs__button admin-tabs__button--active" : "admin-tabs__button"}
          onClick={() => setTab("documents")}
        >
          All Documents
        </button>
      </div>

      {error && <div className="admin-error">{error}</div>}
      {loading && <div className="admin-empty">Loading...</div>}

      {!loading && (
        <div className="admin-layout admin-layout--single">
          <div className="admin-list">
            {tab === "blocks" ? (
              <AnalysisBlocks
                blocks={filteredBlocks}
                onViewDocument={openDocument}
                onLogStory={openLogStory}
              />
            ) : (
              <DocumentsTable
                documents={filteredDocuments}
                onView={openDocument}
                onLogStory={openLogStory}
                /* onReindex={handleReindex} */
                onDelete={handleDelete}
              />
            )}
          </div>
        </div>
      )}

      {preview && (
        <div className="admin-preview-modal" role="dialog" aria-modal="true">
          <div className="admin-preview-modal__panel">
            <div className="admin-preview-modal__header">
              <h2>{preview.title}</h2>
              <button
                type="button"
                onClick={() => setPreview(null)}
                aria-label="Close preview"
              >
                <X size={18} />
              </button>
            </div>
            <iframe
              title={preview.title}
              src={preview.url}
              className="admin-preview-modal__frame"
            />
          </div>
        </div>
      )}
    </section>
  );
}


function AnalysisBlocks({
  blocks,
  onViewDocument,
  onLogStory,
}) {
  if (blocks.length === 0) {
    return <div className="admin-empty">No analysis blocks found.</div>;
  }

  return (
    <div className="analysis-blocks">
      {blocks.map((block) => (
        <article key={block.id} className="analysis-block">
          <div className="analysis-block__header">
            <div>
              <h3>{block.title}</h3>
              <p>{formatDate(block.updated_at ?? block.created_at)}</p>
            </div>
            <span className={`admin-status admin-status--${block.status}`}>
              {block.status}
            </span>
          </div>

          <div className="analysis-block__stats">
            <span>{block.trace_count} trace(s)</span>
            <span>{block.reference_count} reference(s)</span>
            <span>{block.documents_count} file(s)</span>
          </div>

          <div className="analysis-block__files">
            {block.documents.map((document) => (
              <DocumentChip
                key={document.id}
                document={document}
                onView={() => onViewDocument(document)}
                onLogStory={
                  isTrace(document)
                    ? () => onLogStory(document, true)
                    : null
                }
              />
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}


function DocumentChip({
  document,
  onView,
  onLogStory,
}) {
  return (
    <div className="document-chip">
      <FileText size={15} />
      <span>{document.original_filename}</span>
      <button type="button" onClick={onView}>View</button>
      {onLogStory && (
        <button type="button" onClick={onLogStory}>Log Story</button>
      )}
    </div>
  );
}


function DocumentsTable({
  documents,
  onView,
  onLogStory,
  // onReindex,
  onDelete,
}) {
  if (documents.length === 0) {
    return <div className="admin-empty">No documents found.</div>;
  }

  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Type</th>
            <th>Agent</th>
            <th>Status</th>
            <th>Size</th>
            <th>Uploaded</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((document) => (
            <tr key={document.id}>
              <td>{document.original_filename}</td>
              <td>{document.extension}</td>
              <td>{document.agent}</td>
              <td>
                <span className={`admin-status admin-status--${document.status}`}>
                  {document.status}
                </span>
              </td>
              <td>{formatSize(document.size)}</td>
              <td>{formatDate(document.created_at)}</td>
              <td>
                <div className="admin-row-actions">
                  <button type="button" onClick={() => onView(document)} title="View">
                    <Eye size={14} />
                  </button>
                  {isTrace(document) && (
                    <button type="button" onClick={() => onLogStory(document, true)} title="Log Story">
                      Log Story
                    </button>
                  )}
                  <a href={getAdminDocumentDownloadUrl(document.id)} title="Download">
                    <Download size={14} />
                  </a>
                  {/* <button type="button" onClick={() => onReindex(document)} title="Reindex">
                    <RefreshCw size={14} />
                  </button> */}
                  <button type="button" onClick={() => onDelete(document)} title="Delete">
                    <Trash2 size={14} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

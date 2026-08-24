import React, { useEffect, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";

import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import AgentSelector, { AGENTS } from "./components/AgentSelector";
import FileUpload from "./components/UploadArea";
import MessageInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import { DocumentPreviewPanel } from "./components/AIResponseCard";
import AdminDocumentsPage from "./components/AdminDocumentsPage";


import {
  createConversation,
  createMessage,
  deleteConversation,
  getConversations,
  getConversationDocuments,
  getConversationMessages,
  getDocuments,
  sendChatMessage,
  updateConversation,
  uploadFiles,
} from "./services/api";


const SIDEBAR_WIDTH_STORAGE_KEY = "trace.sidebar.width";
const SIDEBAR_DEFAULT_WIDTH = 340;
const SIDEBAR_MIN_WIDTH = 280;
const SIDEBAR_MAX_WIDTH = 560;


function clampSidebarWidth(value) {
  return Math.min(
    SIDEBAR_MAX_WIDTH,
    Math.max(SIDEBAR_MIN_WIDTH, Math.round(value)),
  );
}


function initialSidebarWidth() {
  if (typeof window === "undefined") {
    return SIDEBAR_DEFAULT_WIDTH;
  }

  const savedWidth = Number(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY));

  return Number.isFinite(savedWidth)
    ? clampSidebarWidth(savedWidth)
    : SIDEBAR_DEFAULT_WIDTH;
}


function makeId() {
  return Math.random().toString(36).slice(2, 10);
}

function displayMessageContent(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);

  try {
    return JSON.stringify(value, null, 2);
  } catch (_error) {
    return String(value);
  }
}

function normalizeConversationTitleSource(text) {
  return text
    .replace(/#[^\s]+/g, " ")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function truncateTitle(title, maxLength = 34) {
  if (title.length <= maxLength) return title;

  const shortened = title.slice(0, maxLength).replace(/\s+\S*$/, "");
  return `${shortened || title.slice(0, maxLength)}...`;
}

function documentDisplayName(document) {
  return String(document?.name ?? document?.original_filename ?? "").trim();
}

function isTraceFilename(filename) {
  return /\.(?:trc\d*|log|txt)$/i.test(filename);
}

function traceTitleSuffix(files = [], references = []) {
  const traceNames = [
    ...files.map((file) => file?.name ?? ""),
    ...references.map(documentDisplayName),
  ]
    .filter(Boolean)
    .filter(isTraceFilename);

  if (traceNames.length === 0) return "";

  const firstTrace = traceNames[0].replace(/\.[^.]+$/, "");
  const suffix = traceNames.length > 1
    ? `${firstTrace} +${traceNames.length - 1}`
    : firstTrace;

  return truncateTitle(suffix, 28);
}

function withTraceSuffix(title, files = [], references = []) {
  const suffix = traceTitleSuffix(files, references);

  if (!suffix) return title;

  return truncateTitle(`${title} - ${suffix}`, 58);
}

function buildConversationTitle(prompt, agent, files = [], references = []) {
  const cleaned = normalizeConversationTitleSource(prompt);
  const normalized = cleaned.toLowerCase();
  const fieldMatch = normalized.match(/\b(?:field|fld|champ)\s*\(?0*(\d+(?:\.\d+)?)\)?\b/);

  if (normalized.includes("hsm") || normalized.includes("hsmresultcode")) {
    return withTraceSuffix("Analyse HSM", files, references);
  }

  if (fieldMatch) {
    const fieldNumber = fieldMatch[1].includes(".")
      ? fieldMatch[1]
      : fieldMatch[1].padStart(3, "0");

    if (
      normalized.includes("code")
      || normalized.includes("valeur")
      || normalized.includes("signification")
      || normalized.includes("tableau")
    ) {
      return withTraceSuffix(`Codes Field ${fieldNumber}`, files, references);
    }

    return withTraceSuffix(`Field ${fieldNumber}`, files, references);
  }

  if (
    normalized.includes("analyse")
    && (
      normalized.includes("trace")
      || normalized.includes("log")
      || normalized.includes("transaction")
    )
  ) {
    if (
      normalized.includes("echec")
      || normalized.includes("failed")
      || normalized.includes("erreur")
      || normalized.includes("error")
    ) {
      return withTraceSuffix("Transactions en echec", files, references);
    }

    return withTraceSuffix("Analyse de trace", files, references);
  }

  if (
    normalized.includes("resume")
    || normalized.includes("resumer")
    || normalized.includes("presente")
    || normalized.includes("document")
    || normalized.includes("pdf")
  ) {
    return withTraceSuffix(
      agent === "log" ? "Analyse documentaire" : "Resume document",
      files,
      references,
    );
  }

  const words = cleaned
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((word) => word.length > 2)
    .slice(0, 5);

  return withTraceSuffix(
    truncateTitle(words.join(" ") || "Nouvelle discussion"),
    files,
    references,
  );
}

function sortConversations(conversations) {
  return [...conversations].sort((a, b) => {
    if (Boolean(a.pinned) !== Boolean(b.pinned)) {
      return a.pinned ? -1 : 1;
    }

    return new Date(b.updated_at ?? 0) - new Date(a.updated_at ?? 0);
  });
}


export default function App() {
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(initialSidebarWidth);
  const [viewMode, setViewMode] = useState("chat");

  const [messagesByConv, setMessagesByConv] = useState({});
  const [loadingMessagesByConv, setLoadingMessagesByConv] = useState({});
  const [agentByConv, setAgentByConv] = useState({});
  const [filesByConv, setFilesByConv] = useState({});
  const [referencesByConv, setReferencesByConv] = useState({});
  const [referenceDocuments, setReferenceDocuments] = useState([]);

  const [draftAgent, setDraftAgent] = useState("documentation");
  const [draftFiles, setDraftFiles] = useState([]);
  const [draftReferences, setDraftReferences] = useState([]);

  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] =
    useState(false);
  const [documentPreview, setDocumentPreview] = useState(null);
  const [loadingConversations, setLoadingConversations] =
    useState(true);

  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const sidebarResizeRef = useRef({
    dragging: false,
    startX: 0,
    startWidth: SIDEBAR_DEFAULT_WIDTH,
    lastWidth: SIDEBAR_DEFAULT_WIDTH,
  });

  const activeMessages = activeId
    ? messagesByConv[activeId] ?? []
    : [];
  const loadingActiveMessages = Boolean(
    activeId && loadingMessagesByConv[activeId],
  );

  const activeAgent = activeId
    ? agentByConv[activeId] ?? "documentation"
    : draftAgent;

  const activeFiles = activeId
    ? filesByConv[activeId] ?? []
    : draftFiles;

  const activeReferences = activeId
    ? referencesByConv[activeId] ?? []
    : draftReferences;

  const showEmpty = !activeId;

  useEffect(() => {
    function handleSidebarResizeMove(event) {
      if (!sidebarResizeRef.current.dragging) return;

      const nextWidth = clampSidebarWidth(
        sidebarResizeRef.current.startWidth
        + event.clientX
        - sidebarResizeRef.current.startX,
      );

      sidebarResizeRef.current.lastWidth = nextWidth;
      setSidebarWidth(nextWidth);
    }

    function handleSidebarResizeEnd() {
      if (!sidebarResizeRef.current.dragging) return;

      sidebarResizeRef.current.dragging = false;
      document.body.classList.remove("sidebar-resizing");
      window.localStorage.setItem(
        SIDEBAR_WIDTH_STORAGE_KEY,
        String(sidebarResizeRef.current.lastWidth),
      );
    }

    window.addEventListener("mousemove", handleSidebarResizeMove);
    window.addEventListener("mouseup", handleSidebarResizeEnd);

    return () => {
      window.removeEventListener("mousemove", handleSidebarResizeMove);
      window.removeEventListener("mouseup", handleSidebarResizeEnd);
      document.body.classList.remove("sidebar-resizing");
    };
  }, []);

  function handleSidebarResizeStart(event) {
    event.preventDefault();
    sidebarResizeRef.current = {
      dragging: true,
      startX: event.clientX,
      startWidth: sidebarWidth,
      lastWidth: sidebarWidth,
    };
    document.body.classList.add("sidebar-resizing");
  }

  function handleSidebarResizeReset() {
    sidebarResizeRef.current.lastWidth = SIDEBAR_DEFAULT_WIDTH;
    setSidebarWidth(SIDEBAR_DEFAULT_WIDTH);
    window.localStorage.setItem(
      SIDEBAR_WIDTH_STORAGE_KEY,
      String(SIDEBAR_DEFAULT_WIDTH),
    );
  }


  /*
   * Load conversations from MongoDB when the page opens.
   */
  useEffect(() => {
    async function loadConversations() {
      try {
        setLoadingConversations(true);

        const data = await getConversations();

        setConversations(sortConversations(data));

        const agents = {};

        data.forEach((conversation) => {
          agents[conversation.id] =
            conversation.agent ?? "documentation";
        });

        setAgentByConv(agents);
      } catch (error) {
        console.error(
          "Failed to load conversations:",
          error,
        );
      } finally {
        setLoadingConversations(false);
      }
    }

    loadConversations();
  }, []);


  /*
   * Load extracted files that can be referenced with @ mentions.
   */
  async function loadReferenceDocuments() {
    try {
      const globalDocuments = (
        await getDocuments({
          status: "extracted",
        })
      ).filter(isGlobalReferenceDocument);
      const conversationDocuments = activeId
        ? await getConversationDocuments(activeId)
        : [];

      setReferenceDocuments(uniqueDocumentsByFilename(
        [
          ...conversationDocuments,
          ...globalDocuments,
        ].filter((document) => document.status === "extracted"),
      ));
    } catch (error) {
      console.error(
        "Failed to load reference documents:",
        error,
      );
    }
  }

function isGlobalReferenceDocument(document) {
    const extension = String(document.extension ?? "").toLowerCase();
    const imageExtensions = new Set([
      ".png",
      ".jpg",
      ".jpeg",
      ".webp",
      ".bmp",
      ".tif",
      ".tiff",
    ]);

    return (
      extension === ".pdf"
      || extension === ".xlsx"
      || extension === ".docx"
      || imageExtensions.has(extension)
    );
  }

  function uniqueDocumentsByFilename(documents = []) {
    const seen = new Set();

    return documents.filter((document) => {
      const key = String(document.original_filename ?? "")
        .trim()
        .toLowerCase();

      if (!key || seen.has(key)) {
        return false;
      }

      seen.add(key);
      return true;
    });
  }


  useEffect(() => {
    loadReferenceDocuments();
  }, [activeId]);


  /*
   * Focus the textarea when changing conversations.
   */
  useEffect(() => {
    textareaRef.current?.focus();
  }, [activeId]);

  useEffect(() => {
    setDocumentPreview(null);
  }, [activeId, viewMode]);


  /*
   * Scroll to the last message.
   */
  function scrollToBottom(behavior = "smooth") {
    if (!scrollRef.current) return;

    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior,
    });
  }


  function handleEditPrompt(prompt) {
    setInput(prompt);

    setTimeout(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        prompt.length,
        prompt.length,
      );
    }, 0);
  }


  useEffect(() => {
    scrollToBottom("smooth");
  }, [activeMessages.length, sending]);


  useEffect(() => {
    const scroller = scrollRef.current;

    if (!scroller) return undefined;

    function handleScroll() {
      const distanceFromBottom =
        scroller.scrollHeight
        - scroller.scrollTop
        - scroller.clientHeight;

      setShowScrollToBottom(distanceFromBottom > 360);
    }

    handleScroll();
    scroller.addEventListener("scroll", handleScroll);

    return () => {
      scroller.removeEventListener("scroll", handleScroll);
    };
  }, [activeId, viewMode]);


  function startNew() {
    setViewMode("chat");
    setActiveId(null);
    setInput("");
    setDraftFiles([]);
    setDraftReferences([]);

    setTimeout(() => {
      textareaRef.current?.focus();
    }, 0);
  }


  function setActiveAgent(id) {
    if (activeId) {
      setAgentByConv((previous) => ({
        ...previous,
        [activeId]: id,
      }));
    } else {
      setDraftAgent(id);
    }
  }


  function setActiveFiles(files) {
    if (activeId) {
      setFilesByConv((previous) => ({
        ...previous,
        [activeId]: files,
      }));
    } else {
      setDraftFiles(files);
    }
  }


  /*
   * Load messages when the user selects a conversation.
   */
  async function handleSelectConversation(id) {
    setViewMode("chat");
    setActiveId(id);
    setDocumentPreview(null);
    setLoadingMessagesByConv((previous) => ({
      ...previous,
      [id]: true,
    }));

    const selectedConversation = conversations.find(
      (conversation) => conversation.id === id,
    );

    if (selectedConversation) {
      setAgentByConv((previous) => ({
        ...previous,
        [id]:
          selectedConversation.agent ?? "documentation",
      }));
    }

    try {
      const messages =
        await getConversationMessages(id);

      setMessagesByConv((previous) => ({
        ...previous,
        [id]: messages,
      }));
    } catch (error) {
      console.error(
        "Failed to load conversation messages:",
        error,
      );
    } finally {
      setLoadingMessagesByConv((previous) => ({
        ...previous,
        [id]: false,
      }));
    }
  }


  /*
   * Delete a conversation from MongoDB.
   */
  async function handleDeleteConversation(id) {
    const confirmed = window.confirm(
      "Are you sure you want to delete this conversation?",
    );

    if (!confirmed) return;

    try {
      await deleteConversation(id);

      setConversations((current) =>
        current.filter(
          (conversation) => conversation.id !== id,
        ),
      );

      setMessagesByConv((current) => {
        const updated = { ...current };
        delete updated[id];
        return updated;
      });

      setAgentByConv((current) => {
        const updated = { ...current };
        delete updated[id];
        return updated;
      });

      setFilesByConv((current) => {
        const updated = { ...current };
        delete updated[id];
        return updated;
      });

      setReferencesByConv((current) => {
        const updated = { ...current };
        delete updated[id];
        return updated;
      });

      if (activeId === id) {
        setActiveId(null);
        setInput("");
        setDraftFiles([]);
        setDraftReferences([]);
      }
    } catch (error) {
      console.error(
        "Failed to delete conversation:",
        error,
      );

      window.alert(
        error instanceof Error
          ? error.message
          : "Unable to delete the conversation.",
      );
    }
  }

  async function handleTogglePinConversation(id) {
    const conversation = conversations.find(
      (item) => item.id === id,
    );

    if (!conversation) return;

    const nextPinned = !conversation.pinned;

    setConversations((previous) => sortConversations(
      previous.map((item) => (
        item.id === id
          ? { ...item, pinned: nextPinned }
          : item
      )),
    ));

    try {
      const updatedConversation = await updateConversation(
        id,
        { pinned: nextPinned },
      );

      setConversations((previous) => sortConversations(
        previous.map((item) => (
          item.id === id
            ? updatedConversation
            : item
        )),
      ));
    } catch (error) {
      console.error(
        "Failed to pin conversation:",
        error,
      );

      setConversations((previous) => sortConversations(
        previous.map((item) => (
          item.id === id
            ? { ...item, pinned: conversation.pinned }
            : item
        )),
      ));
    }
  }


  /*
   * Send the user message, create the conversation when needed,
   * upload files, call the chat API and save the AI response.
   */
  async function handleSend() {
    const text = input.trim();

    const hasPendingFiles = activeFiles.length > 0 || draftFiles.length > 0;
    const hasPendingReferences = (
      activeReferences.length > 0
      || draftReferences.length > 0
    );

    if (
      sending
      || (
        !text
        && !hasPendingFiles
        && !hasPendingReferences
      )
    ) return;

    setSending(true);
    setInput("");

    let convId = activeId;
    let selectedAgent = activeAgent;
    let selectedFiles = activeFiles;
    let selectedReferences = activeReferences;

    try {
      /*
       * Create a MongoDB conversation when this is a new chat.
       */
      if (!convId) {
        const title = buildConversationTitle(
          text,
          draftAgent,
          draftFiles,
          draftReferences,
        );

        const createdConversation =
          await createConversation({
            title,
            agent: draftAgent,
          });

        convId = createdConversation.id;
        selectedAgent = draftAgent;
        selectedFiles = draftFiles;
        selectedReferences = draftReferences;

        setConversations((previous) => sortConversations([
          createdConversation,
          ...previous,
        ]));

        setAgentByConv((previous) => ({
          ...previous,
          [convId]: selectedAgent,
        }));

        setFilesByConv((previous) => ({
          ...previous,
          [convId]: selectedFiles,
        }));

        setReferencesByConv((previous) => ({
          ...previous,
          [convId]: selectedReferences,
        }));

        setActiveId(convId);
      }

      
      /*
       * Display the user message immediately.
       */
      // const localUserMessage = {
      //   id: makeId(),
      //   role: "user",
      //   content: text,
      //   structured: null,
      //   attachments: selectedFiles.map((file) => ({
      //     name: file.name,
      //     size: file.size,
      //     type: file.type,
      //   })),
      // };

      // const attachments = selectedFiles.map((file) => ({
      //   name: file.name,
      //   size: file.size,
      //   type: file.type,
      // }));

      /*
 * Upload the selected files and retrieve
 * their real MongoDB document identifiers.
 */
      let attachments = [];

      if (selectedFiles.length > 0) {
        const uploadResult = await uploadFiles({
          files: selectedFiles,
          agent: selectedAgent,
          conversationId: convId,
        });

        attachments = uploadResult.documents.map(
          (document) => ({
            document_id: document.id,
            name: document.original_filename,
            size: document.size,
            type: document.content_type,
            status: document.status,
          }),
        );

        if (selectedAgent === "documentation") {
          await loadReferenceDocuments();
        }
      }

      const referencedAttachments = selectedReferences.map(
        (document) => ({
          document_id: document.id,
          name: document.original_filename,
          size: document.size,
          type: document.content_type,
          status: "referenced",
        }),
      );

      /*
       * Display the user message.
       */
      const messageText = text || (
        selectedFiles.length > 0
          ? "Analyse les fichiers joints."
          : "Analyse les documents references."
      );

      const localUserMessage = {
        id: makeId(),
        role: "user",
        content: messageText,
        structured: null,
        attachments: [
          ...attachments,
          ...referencedAttachments,
        ],
      };

      setMessagesByConv((previous) => ({
        ...previous,
        [convId]: [
          ...(previous[convId] ?? []),
          localUserMessage,
        ],
      }));

      /*
      * Save the user message in MongoDB.
      */
      await createMessage({
        conversationId: convId,
        role: "user",
        content: messageText,
        structured: null,
        attachments: [
          ...attachments,
          ...referencedAttachments,
        ],
      });

      /*
      * Clear pending files.
      */
      setFilesByConv((previous) => ({
        ...previous,
        [convId]: [],
      }));

      setDraftFiles([]);
      setReferencesByConv((previous) => ({
        ...previous,
        [convId]: [],
      }));

      setDraftReferences([]);

      /*
       * Ask the backend chat endpoint.
       */
      const response = await sendChatMessage({
        question: messageText,
        agent: selectedAgent,
        conversationId: convId,
        referencedDocumentIds: [
          ...attachments.map((document) => document.document_id),
          ...selectedReferences.map((document) => document.id),
        ].filter(Boolean),
      });

      const localAssistantMessage = {
        id: makeId(),
        role: "assistant",
        content: "",
        structured: response.answer,
      };

      /*
       * Display the assistant response.
       */
      setMessagesByConv((previous) => ({
        ...previous,
        [convId]: [
          ...(previous[convId] ?? []),
          localAssistantMessage,
        ],
      }));

      setFilesByConv((previous) => ({
        ...previous,
        [convId]: [],
      }));

      setDraftFiles([]);

      /*
       * Save the assistant response in MongoDB.
       */
      // await createMessage({
      //   conversationId: convId,
      //   role: "assistant",
      //   content: "",
      //   structured: response.answer,
      // });
      // await createMessage({
      //   conversationId: convId,
      //   role: "user",
      //   content: text,
      //   structured: null,
      //   attachments,
      // });

      await createMessage({
        conversationId: convId,
        role: "assistant",
        content: "",
        structured: response.answer,
        attachments: [],
      });
    } catch (error) {
      console.error(
        "TRACE AI request failed:",
        error,
      );

      /*
       * Only display an error message when a conversation
       * was successfully created.
       */
      if (convId) {
        const errorMessage = {
          id: makeId(),
          role: "assistant",
          content:
            error instanceof Error
              ? error.message
              : "The backend request failed.",
          structured: null,
        };

        setMessagesByConv((previous) => ({
          ...previous,
          [convId]: [
            ...(previous[convId] ?? []),
            errorMessage,
          ],
        }));
      } else {
        window.alert(
          error instanceof Error
            ? error.message
            : "Unable to create the conversation.",
        );
      }
    } finally {
      setSending(false);

      setTimeout(() => {
        textareaRef.current?.focus();
      }, 0);
    }
  }


  return (
    <div
      className={sidebarOpen
        ? "app-shell"
        : "app-shell sidebar-hidden"}
      style={{ "--sidebar-width": `${sidebarWidth}px` }}
    >

        <Sidebar
          open={sidebarOpen}
          conversations={conversations}
          activeId={activeId}
          onSelect={handleSelectConversation}
          onNew={startNew}
          onDelete={handleDeleteConversation}
          onTogglePin={handleTogglePinConversation}
          onAdmin={() => setViewMode("admin")}
          adminActive={viewMode === "admin"}
          onResizeStart={handleSidebarResizeStart}
          onResizeReset={handleSidebarResizeReset}
      /> 

      {/* <div className={sidebarOpen ? "app-shell" : "app-shell sidebar-hidden"}>
        
      </div> */}
      <div className="main-shell">
        <Header
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() =>
            setSidebarOpen((previous) => !previous)
          }
        />

        <div className={`chat-workspace${documentPreview && viewMode !== "admin" ? " chat-workspace--with-preview" : ""}`}>
          <div className="chat-workspace__chat">
        <main
          ref={scrollRef}
          className="content-area"
        >
          {viewMode === "admin" ? (
            <AdminDocumentsPage
              onBack={() => setViewMode("chat")}
            />
          ) : loadingConversations ? (
            <div className="chat-loading">
              <div className="chat-loading__avatar">
                ★
              </div>

              <div className="chat-loading__bubble">
                Loading conversations…
              </div>
            </div>
          ) : loadingActiveMessages ? (
            <div className="chat-loading">
              <div className="chat-loading__avatar">
                â˜…
              </div>

              <div className="chat-loading__bubble">
                Chargement de la conversationâ€¦
              </div>
            </div>
          ) : showEmpty ? (
            <div className="workspace">
              <AgentSelector
                value={draftAgent}
                onChange={setActiveAgent}
              />

              <FileUpload
                files={draftFiles}
                onFilesChange={setActiveFiles}
              />
            </div>
          ) : (
            <section className="conversation-panel">
              {activeMessages.map((message, index) => {
                const previousUserMessage = activeMessages
                  .slice(0, index)
                  .reverse()
                  .find((item) => item.role === "user");

                return (
                <ChatMessage
                  key={message.id}
                  message={message}
                  onEditPrompt={handleEditPrompt}
                  onDocumentPreview={setDocumentPreview}
                  onRetry={
                    message.role === "assistant" && previousUserMessage
                      ? () => handleEditPrompt(displayMessageContent(previousUserMessage.content))
                      : undefined
                  }
                  onSuggestedPrompt={handleEditPrompt}
                />
                );
              })}

              {sending && (
                <div className="chat-loading">
                  <div className="chat-loading__avatar">
                    ★
                  </div>

                  <div className="chat-loading__bubble">
                    Typing…
                  </div>
                </div>
              )}
            </section>
          )}
        </main>

        {viewMode !== "admin" && showScrollToBottom && (
          <button
            type="button"
            className="scroll-to-bottom"
            onClick={() => scrollToBottom("smooth")}
            aria-label="Descendre en bas de la conversation"
            title="Descendre en bas"
          >
            <ArrowDown className="scroll-to-bottom__icon" />
          </button>
        )}


        {viewMode !== "admin" && (
        <footer className="composer-footer">
          <MessageInput
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSend}
            agents={AGENTS}
            activeAgent={activeAgent}
            onAgentChange={setActiveAgent}
            files={activeFiles}
            referenceDocuments={referenceDocuments}
            selectedReferences={activeReferences}
            enableReferences={["documentation", "log"].includes(activeAgent)}
            disabled={sending}
            onReferenceSelect={(document) => {
              if (activeId) {
                setReferencesByConv((previous) => ({
                  ...previous,
                  [activeId]: [
                    ...(previous[activeId] ?? []),
                    document,
                  ],
                }));
              } else {
                setDraftReferences((previous) => [
                  ...previous,
                  document,
                ]);
              }
            }}
            onRemoveReference={(documentId) => {
              if (activeId) {
                setReferencesByConv((previous) => ({
                  ...previous,
                  [activeId]: (
                    previous[activeId] ?? []
                  ).filter(
                    (document) =>
                      document.id !== documentId,
                  ),
                }));
              } else {
                setDraftReferences((previous) =>
                  previous.filter(
                    (document) =>
                      document.id !== documentId,
                  ),
                );
              }
            }}
            onAttach={(fileList) => {
              const newFiles = Array.from(fileList);

              if (activeId) {
                setFilesByConv((previous) => ({
                  ...previous,
                  [activeId]: [
                    ...(previous[activeId] ?? []),
                    ...newFiles,
                  ],
                }));
              } else {
                setDraftFiles((previous) => [
                  ...previous,
                  ...newFiles,
                ]);
              }
            }}
            onRemoveFile={(indexToRemove) => {
              if (activeId) {
                setFilesByConv((previous) => ({
                  ...previous,
                  [activeId]: (
                    previous[activeId] ?? []
                  ).filter(
                    (_, index) =>
                      index !== indexToRemove,
                  ),
                }));
              } else {
                setDraftFiles((previous) =>
                  previous.filter(
                    (_, index) =>
                      index !== indexToRemove,
                  ),
                );
              }
            }}
          />
        </footer>
        )}
          </div>

          {viewMode !== "admin" && documentPreview && (
            <aside className="chat-workspace__preview">
              <DocumentPreviewPanel
                preview={documentPreview}
                onClose={() => setDocumentPreview(null)}
              />
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

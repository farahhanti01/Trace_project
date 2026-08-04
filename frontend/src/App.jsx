import React, { useEffect, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";

import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import AgentSelector, { AGENTS } from "./components/AgentSelector";
import FileUpload from "./components/UploadArea";
import MessageInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import AdminDocumentsPage from "./components/AdminDocumentsPage";


import {
  createConversation,
  createMessage,
  deleteConversation,
  getConversations,
  getConversationMessages,
  getDocuments,
  sendChatMessage,
  uploadFiles,
} from "./services/api";


function makeId() {
  return Math.random().toString(36).slice(2, 10);
}


export default function App() {
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [viewMode, setViewMode] = useState("chat");

  const [messagesByConv, setMessagesByConv] = useState({});
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
  const [loadingConversations, setLoadingConversations] =
    useState(true);

  const scrollRef = useRef(null);
  const textareaRef = useRef(null);

  const activeMessages = activeId
    ? messagesByConv[activeId] ?? []
    : [];

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

  

  /*
   * Load conversations from MongoDB when the page opens.
   */
  useEffect(() => {
    async function loadConversations() {
      try {
        setLoadingConversations(true);

        const data = await getConversations();

        setConversations(data);

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
   * Load extracted Documentation Agent files used by # mentions.
   */
  async function loadReferenceDocuments() {
    try {
      const documents = await getDocuments({
        agent: "documentation",
        status: "extracted",
      });

      setReferenceDocuments(documents);
    } catch (error) {
      console.error(
        "Failed to load reference documents:",
        error,
      );
    }
  }


  useEffect(() => {
    loadReferenceDocuments();
  }, []);


  /*
   * Focus the textarea when changing conversations.
   */
  useEffect(() => {
    textareaRef.current?.focus();
  }, [activeId]);


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


  /*
   * Send the user message, create the conversation when needed,
   * upload files, call the chat API and save the AI response.
   */
  async function handleSend() {
    const text = input.trim();

    if (!text || sending) return;

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
        const title =
          text.length > 40
            ? `${text.slice(0, 40)}…`
            : text;

        const createdConversation =
          await createConversation({
            title,
            agent: draftAgent,
          });

        convId = createdConversation.id;
        selectedAgent = draftAgent;
        selectedFiles = draftFiles;
        selectedReferences = draftReferences;

        setConversations((previous) => [
          createdConversation,
          ...previous,
        ]);

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
      const localUserMessage = {
        id: makeId(),
        role: "user",
        content: text,
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
        content: text,
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
        question: text,
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
    >

        <Sidebar
          open={sidebarOpen}
          conversations={conversations}
          activeId={activeId}
          onSelect={handleSelectConversation}
          onNew={startNew}
          onDelete={handleDeleteConversation}
          onAdmin={() => setViewMode("admin")}
          adminActive={viewMode === "admin"}
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
              {activeMessages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  onEditPrompt={handleEditPrompt}
                />
              ))}

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
          {activeId && (
            <div className="composer-agent-switch">
              {Object.entries(AGENTS).map(([id, agent]) => {
                const Icon = agent.icon;
                const active = activeAgent === id;

                return (
                  <button
                    key={id}
                    type="button"
                    className={
                      active
                        ? "composer-agent-switch__button composer-agent-switch__button--active"
                        : "composer-agent-switch__button"
                    }
                    onClick={() => setActiveAgent(id)}
                  >
                    <Icon className="composer-agent-switch__icon" />
                    <span>{agent.name}</span>
                  </button>
                );
              })}
            </div>
          )}

          <MessageInput
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSend}
            files={activeFiles}
            referenceDocuments={referenceDocuments}
            selectedReferences={activeReferences}
            enableReferences={activeAgent === "log"}
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
    </div>
  );
}

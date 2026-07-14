import React, { useEffect, useRef, useState } from "react";

import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import AgentSelector from "./components/AgentSelector";
import FileUpload from "./components/UploadArea";
import MessageInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";


import {
  createConversation,
  createMessage,
  deleteConversation,
  getConversations,
  getConversationMessages,
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

  const [messagesByConv, setMessagesByConv] = useState({});
  const [agentByConv, setAgentByConv] = useState({});
  const [filesByConv, setFilesByConv] = useState({});

  const [draftAgent, setDraftAgent] = useState("documentation");
  const [draftFiles, setDraftFiles] = useState([]);

  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
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
   * Focus the textarea when changing conversations.
   */
  useEffect(() => {
    textareaRef.current?.focus();
  }, [activeId]);


  /*
   * Scroll to the last message.
   */
  useEffect(() => {
    if (!scrollRef.current) return;

    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [activeMessages.length, sending]);


  function startNew() {
    setActiveId(null);
    setInput("");
    setDraftFiles([]);

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

      if (activeId === id) {
        setActiveId(null);
        setInput("");
        setDraftFiles([]);
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

      const attachments = selectedFiles.map((file) => ({
        name: file.name,
        size: file.size,
        type: file.type,
      }));

      const localUserMessage = {
        id: makeId(),
        role: "user",
        content: text,
        structured: null,
        attachments,
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
        attachments,
      });

      if (activeId) {
        setFilesByConv((previous) => ({
          ...previous,
          [convId]: [],
        }));
      } else {
        setDraftFiles([]);
      }

      /*
       * Upload attached documents or logs.
       */
      if (selectedFiles.length > 0) {
        await uploadFiles({
          files: selectedFiles,
          agent: selectedAgent,
        });
      }

      /*
       * Ask the backend chat endpoint.
       */
      const response = await sendChatMessage({
        question: text,
        agent: selectedAgent,
        conversationId: convId,
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
          {loadingConversations ? (
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


        <footer className="composer-footer">
          <MessageInput
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSend}
            files={activeFiles}
            disabled={sending}
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
      </div>
    </div>
  );
}
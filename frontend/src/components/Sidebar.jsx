import React, { useMemo, useState } from "react";
import {
  Pin,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { cn } from "../lib/utils";
import hpsLogo from "../assets/HPS.png";

function normalizeDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? new Date(0) : date;
}

function isToday(date) {
  const now = new Date();

  return (
    date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
  );
}

function isWithinLastSevenDays(date) {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;

  return diffMs >= 0 && diffMs <= sevenDaysMs;
}

const CONVERSATION_DOT_COLOR = "#f59e0b";

function conversationSubtitle(conversation) {
  const title = String(conversation.title ?? "");
  const normalized = title.toLowerCase();

  if (normalized.includes("screen")) return "image.png";
  if (normalized.includes("document")) return "Spec ISO 8583 v2.1";
  if (normalized.includes("trace")) return "TRACE_WITH_ERR_ED_05";
  if (normalized.includes("field 002")) return "BASE1_ICH_2.TRC019";
  if (normalized.includes("field 037")) return "Capture utilisateur · 34 lignes";
  if (normalized.includes("reversal")) return "TRC021";
  if (normalized.includes("field 039")) return "Table de réponses";

  return conversation.agent === "log"
    ? "Analyse de trace"
    : "Documentation";
}

function groupConversations(conversations) {
  const groups = [
    {
      key: "pinned",
      label: "ÉPINGLÉES",
      conversations: [],
    },
    {
      key: "today",
      label: "AUJOURD'HUI",
      conversations: [],
    },
    {
      key: "last7",
      label: "7 DERNIERS JOURS",
      conversations: [],
    },
  ];

  conversations.forEach((conversation) => {
    const updatedAt = normalizeDate(
      conversation.updated_at ?? conversation.created_at,
    );

    if (conversation.pinned) {
      groups[0].conversations.push(conversation);
      return;
    }

    if (isToday(updatedAt)) {
      groups[1].conversations.push(conversation);
      return;
    }

    if (isWithinLastSevenDays(updatedAt)) {
      groups[2].conversations.push(conversation);
      return;
    }

    groups[2].conversations.push(conversation);
  });

  return groups.filter((group) => group.conversations.length > 0);
}

export function Sidebar({
  open = true,
  conversations = [],
  activeId = null,
  onSelect = () => {},
  onNew = () => {},
  onDelete = () => {},
  onTogglePin = () => {},
  onAdmin = () => {},
  adminActive = false,
  onResizeStart = () => {},
  onResizeReset = () => {},
}) {
  const [search, setSearch] = useState("");
  const filteredConversations = useMemo(() => {
    const query = search.trim().toLowerCase();

    if (!query) return conversations;

    return conversations.filter((conversation) => {
      const title = String(conversation.title ?? "").toLowerCase();
      const subtitle = conversationSubtitle(conversation).toLowerCase();

      return title.includes(query) || subtitle.includes(query);
    });
  }, [conversations, search]);
  const groups = groupConversations(filteredConversations);

  return (
    <aside className="sidebar sidebar--trace-dark">
      <div className="sidebar-brand sidebar-brand--trace">
        <img
          src={hpsLogo}
          alt="HPS"
          className="sidebar-brand__hps-logo"
        />

        {/* <span className="sidebar-brand__badge">TRACE AI</span> */}

        {/* <div className="sidebar-brand__copy">
          <strong>TRACE AI</strong>
          <span>HPS OPS INTELLIGENCE</span>
        </div> */}

        {/* <button
          type="button"
          className="sidebar-brand__collapse"
          aria-label="Réduire l'historique"
          title="Réduire l'historique"
        >
          <PanelLeft />
        </button> */}
      </div>

      <button className="sidebar-new sidebar-new--trace" type="button" onClick={onNew}>
        <span>Nouvelle conversation</span>
      </button>

      <label className="sidebar-search">
        <Search className="sidebar-search__icon" />
        <input
          value={search}
          placeholder="Rechercher une analyse..."
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>

      <div className="sidebar-content sidebar-content--trace">
        {groups.length > 0 ? (
          groups.map((group) => (
            <section key={group.key} className="sidebar-group">
              <h3 className="sidebar-heading sidebar-heading--trace">
                {group.label}
              </h3>

              <ul className="sidebar-list sidebar-list--trace">
                {group.conversations.map((conversation) => (
                  <li key={conversation.id} className="sidebar-list__item">
                    <button
                      type="button"
                      onClick={() => onSelect(conversation.id)}
                      className={cn(
                        "sidebar-item sidebar-item--trace",
                        activeId === conversation.id && "sidebar-item--active",
                      )}
                    >
                      <span
                        className="sidebar-item__dot"
                        style={{
                          "--conversation-dot-color":
                            CONVERSATION_DOT_COLOR,
                        }}
                      />

                      <span className="sidebar-item__text">
                        <span className="sidebar-item__label">
                          {conversation.title}
                        </span>
                        <span className="sidebar-item__subtitle">
                          {conversationSubtitle(conversation)}
                        </span>
                      </span>
                    </button>

                    <button
                      type="button"
                      className={cn(
                        "sidebar-item__pin",
                        conversation.pinned && "sidebar-item__pin--active",
                      )}
                      aria-label={
                        conversation.pinned
                          ? `Désépingler ${conversation.title}`
                          : `Épingler ${conversation.title}`
                      }
                      title={conversation.pinned ? "Désépingler" : "Épingler"}
                      onClick={(event) => {
                        event.stopPropagation();
                        onTogglePin(conversation.id);
                      }}
                    >
                      <Pin className="sidebar-item__pin-icon" />
                    </button>

                    <button
                      type="button"
                      className="sidebar-item__delete"
                      title="Supprimer"
                      onClick={(event) => {
                        event.stopPropagation();
                        onDelete(conversation.id);
                      }}
                    >
                      <Trash2 className="sidebar-item__delete-icon" />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        ) : (
          <p className="sidebar-empty">Aucune analyse trouvée.</p>
        )}
      </div>

      <button
        className={
          adminActive
            ? "sidebar-footer sidebar-footer--active"
            : "sidebar-footer"
        }
        type="button"
        onClick={onAdmin}
      >
        <Settings className="sidebar-footer__icon" />
        <span>Documents & agents</span>
      </button>

      {open && (
        <button
          type="button"
          className="sidebar-resize-handle"
          aria-label="Redimensionner l'historique"
          title="Glisser pour élargir l'historique"
          onMouseDown={onResizeStart}
          onDoubleClick={onResizeReset}
        />
      )}
    </aside>
  );
}

export default Sidebar;

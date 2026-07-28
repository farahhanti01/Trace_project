import React from "react";
import {
  Settings,
  MessageSquare,
  // Sparkles,
  Plus,
  Trash2,
} from "lucide-react";
import { cn } from "../lib/utils";
import hpsLogo from "../assets/HPS.png";

export function Sidebar({
  conversations = [],
  activeId = null,
  onSelect = () => {},
  onNew = () => {},
  onDelete = () => {},
  onAdmin = () => {},
  adminActive = false,
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand__icon">
        <img
            src={hpsLogo}
            alt="HPS Logo"
            className="sidebar-brand__logo"
        />
        </div>


        {/* <div>
          <div className="sidebar-brand__title">TRACE AI</div>
          <div className="sidebar-brand__subtitle">Platform</div>
        </div> */}
      </div>

      <button className="sidebar-new" type="button" onClick={onNew}>
        <Plus className="sidebar-new__icon" />
        <span>New Conversation</span>
      </button>

      <div className="sidebar-content">
        <div className="sidebar-heading">History</div>

        {/* <ul className="sidebar-list">
        {conversations.map((conversation) => (
          <li key={conversation.id} className="sidebar-list__item">
            <button
              type="button"
              onClick={() => onSelect(conversation.id)}
              className={cn(
                "sidebar-item",
                activeId === conversation.id && "sidebar-item--active",
              )}
            >
              <MessageSquare className="sidebar-item__icon" />

              <span className="sidebar-item__label">
                {conversation.title}
              </span>
            </button>

            <button
              type="button"
              className="sidebar-item__delete"
              aria-label={`Delete ${conversation.title}`}
              title="Delete conversation"
              onClick={(event) => {
                event.stopPropagation();
                onDelete(conversation.id);
              }}
            >
              <Trash2 className="sidebar-item__delete-icon" />
            </button>
          </li>
        ))}
      </ul> */}

      <ul className="sidebar-list">
        {conversations.map((conversation) => (
          <li key={conversation.id} className="sidebar-list__item">
            <button
              type="button"
              onClick={() => onSelect(conversation.id)}
              className={cn(
                "sidebar-item",
                activeId === conversation.id && "sidebar-item--active",
              )}
            >
              <MessageSquare className="sidebar-item__icon" />

              <span className="sidebar-item__label">
                {conversation.title}
              </span>
            </button>

            <button
              type="button"
              className="sidebar-item__delete"
              title="Delete conversation"
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
        <span>Documents Admin</span>
      </button>
    </aside>
  );
}

export default Sidebar;

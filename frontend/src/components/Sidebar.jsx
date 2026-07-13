import React from "react";
import {
  Settings,
  MessageSquare,
  Sparkles,
  Plus,
} from "lucide-react";
import { cn } from "../lib/utils";
import hpsLogo from "../assets/HPS.png";

export function Sidebar({
  conversations = [],
  activeId = null,
  onSelect = () => {},
  onNew = () => {},
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

        <ul className="sidebar-list">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
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
            </li>
          ))}
        </ul>
      </div>

      <button className="sidebar-footer" type="button">
        <Settings className="sidebar-footer__icon" />
        <span>Settings</span>
      </button>
    </aside>
  );
}

export default Sidebar;

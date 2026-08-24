import React from "react";
import { PanelLeft } from "lucide-react";

export default function Header({
  sidebarOpen,
  onToggleSidebar,
}) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggleSidebar}
          aria-label={
            sidebarOpen
              ? "Close sidebar"
              : "Open sidebar"
          }
          title={
            sidebarOpen
              ? "Close sidebar"
              : "Open sidebar"
          }
        >
          <PanelLeft size={21} />
        </button>

        {/* <div className="topbar-title">
          TRACE Platform
        </div> */}
      </div>

      {/* <div className="topbar-profile">
        <div className="topbar-user">
          <span className="topbar-user__name">
            Farah HANTI
          </span>

          <span className="topbar-user__role">
            AI &amp; data Engineer · HPS
          </span>
        </div>

        <div className="topbar-avatar">
          FH
        </div>
      </div> */}
    </header>
  );
}
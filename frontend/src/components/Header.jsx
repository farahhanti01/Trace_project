import React from 'react'

export function Header() {
  return (
    <header className="topbar">
      <div className="topbar-title">TRACE AI Platform</div>

      <div className="topbar-profile">
        <div className="topbar-user">
          <span className="topbar-user__name">Farah HANTI</span>
          <span className="topbar-user__role">AI & data Engineer · HPS</span>
        </div>

        <div className="topbar-avatar">FH</div>
      </div>
    </header>
  )
}

export default Header

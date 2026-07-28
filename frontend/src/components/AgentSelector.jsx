import React from 'react'
import { FileText, Activity, Check } from 'lucide-react'
import { cn } from '../lib/utils'

export const AGENTS = {
  documentation: {
    name: 'Documentation Agent',
    description: 'Analyze technical specifications (PDF, Word and Excel) and answer questions based on their content.',
    icon: FileText,
  },
  log: {
    name: 'Log Analysis Agent',
    description: 'Analyze authorization logs, reconstruct transaction flows, detect non-conformities and provide diagnostic recommendations.',
    icon: Activity,
  },
}

export default function AgentSelector({ value, onChange }) {
  return (
    <section className="panel panel--agent">
      <div className="panel__header">
        {/* <div className="panel__label">AI Agent</div> */}
        {/* <div className="panel__description">Choose the specialist for this conversation.</div> */}
      </div>

      <div className="agent-grid">
        {Object.keys(AGENTS).map((id) => {
          const agent = AGENTS[id]
          const Icon = agent.icon
          const active = value === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => onChange(id)}
              className={active ? 'agent-card agent-card--active' : 'agent-card'}
            >
              <div className={active ? 'agent-card__icon agent-card__icon--active' : 'agent-card__icon'}>
                <Icon className="agent-card__icon-svg" />
              </div>
              <div className="agent-card__body">
                <h3 className="agent-card__title">{agent.name}</h3>
                <p className="agent-card__text">{agent.description}</p>
              </div>
              {active && (
                <div className="agent-card__badge">
                  <Check className="agent-card__badge-icon" strokeWidth={3} />
                </div>
              )}
            </button>
          )
        })}
      </div>
    </section>
  )
}

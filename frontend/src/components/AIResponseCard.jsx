import React from 'react'
import { FileText, AlertTriangle, CheckCircle2, BookOpen, ScrollText } from 'lucide-react'

const severityStyles = {
  info: 'response-issue--info',
  warning: 'response-issue--warning',
  error: 'response-issue--error',
}

export function AIResponseCard({ data }) {
  return (
    <section className="response-card">
      {data.summary && (
        <ResponseSection icon={ScrollText} title="Summary">
          <p className="response-card__paragraph">{data.summary}</p>
        </ResponseSection>
      )}

      {data.story && data.story.length > 0 && (
        <ResponseSection icon={BookOpen} title="Authorization Story" bordered>
          <ol className="response-story-list">
            {data.story.map((step, i) => (
              <li key={i} className="response-story-list__item">
                <span className="response-story-list__marker">{i + 1}</span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </ResponseSection>
      )}

      {data.issues && data.issues.length > 0 && (
        <ResponseSection icon={AlertTriangle} title="Detected Issues" bordered>
          <ul className="response-issues-list">
            {data.issues.map((iss, i) => (
              <li key={i} className={`response-issue ${severityStyles[iss.severity] ?? severityStyles.info}`}>
                <div className="response-issue__title">{iss.title}</div>
                {iss.detail && <div className="response-issue__detail">{iss.detail}</div>}
              </li>
            ))}
          </ul>
        </ResponseSection>
      )}

      {data.recommendations && data.recommendations.length > 0 && (
        <ResponseSection icon={CheckCircle2} title="Recommendations" bordered>
          <ul className="response-list">
            {data.recommendations.map((r, i) => (
              <li key={i} className="response-list__item">
                <CheckCircle2 className="response-list__icon" />
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </ResponseSection>
      )}

      {data.references && data.references.length > 0 && (
        <ResponseSection icon={FileText} title="References" bordered>
          <div className="response-references">
            {data.references.map((ref, i) => (
              <span key={i} className="reference-pill">
                <FileText className="reference-pill__icon" />
                {ref.source}
                {ref.page !== undefined && <span className="reference-pill__page">· p.{ref.page}</span>}
              </span>
            ))}
          </div>
        </ResponseSection>
      )}
    </section>
  )
}

function ResponseSection({ icon: Icon, title, children, bordered }) {
  return (
    <div className={bordered ? 'response-section response-section--bordered' : 'response-section'}>
      <div className="response-section__header">
        <Icon className="response-section__icon" />
        <h4 className="response-section__title">{title}</h4>
      </div>
      {children}
    </div>
  )
}

export default AIResponseCard

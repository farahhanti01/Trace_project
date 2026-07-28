import React, { useState } from 'react'
import { FileText, AlertTriangle, CheckCircle2, BookOpen, ScrollText, Download } from 'lucide-react'

const severityStyles = {
  info: 'response-issue--info',
  warning: 'response-issue--warning',
  error: 'response-issue--error',
}

export function AIResponseCard({ data }) {
  const hasTransactions = data.transactions && data.transactions.length > 0
  const displayOptions = {
    show_fields: true,
    show_log_story: true,
    show_hsm: true,
    show_documentation_findings: true,
    ...(data.display_options ?? {}),
  }

  return (
    <section className="response-card">
      {data.summary && (
        <ResponseSection icon={ScrollText} title="Summary">
          <p className="response-card__paragraph">{data.summary}</p>
        </ResponseSection>
      )}

      {hasTransactions && (
        <ResponseSection icon={BookOpen} title="Transactions" bordered>
          <TransactionDownloads data={data} />
          {data.transactions.length > 30 && (
            <p className="transaction-limit-note">
              Affichage des 30 transactions les plus importantes. Le telechargement TXT/JSON contient les {data.transactions.length} transactions.
            </p>
          )}
          <div className="transaction-list">
            {visibleTransactions(data.transactions).map((transaction, index) => (
              <TransactionCard
                key={transaction.transaction_id ?? index}
                transaction={transaction}
                displayOptions={displayOptions}
              />
            ))}
          </div>
        </ResponseSection>
      )}

      {!hasTransactions && data.story && data.story.length > 0 && (
        <ResponseSection icon={BookOpen} title="Details" bordered>
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
        <ResponseSection icon={AlertTriangle} title="Notes" bordered>
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

      {/* {data.recommendations && data.recommendations.length > 0 && (
        <ResponseSection icon={CheckCircle2} title="Next Steps" bordered>
          <ul className="response-list">
            {data.recommendations.map((r, i) => (
              <li key={i} className="response-list__item">
                <CheckCircle2 className="response-list__icon" />
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </ResponseSection>
      )} */}


      {/* {
        data.references && data.references.length > 0 && (
          <ResponseSection icon={FileText} title="Sources" bordered>
        )
      } */}
      {/* {data.references && data.references.length > 0 && (
        <ResponseSection icon={FileText} title="Sources" bordered>
          <div className="response-references">
            {data.references.map((ref, i) => (
              <span key={i} className="reference-pill">
                <FileText className="reference-pill__icon" />
                {ref.source}
                {ref.page !== undefined && ref.page !== null && (
                  <span className="reference-pill__page"> - p.{ref.page}</span>
                )}
                {ref.sheet && (
                  <span className="reference-pill__page"> - {ref.sheet}</span>
                )}
                {ref.paragraph !== undefined && ref.paragraph !== null && (
                  <span className="reference-pill__page"> - para. {ref.paragraph}</span>
                )}
              </span>
            ))}
          </div>
        </ResponseSection>
      )} */}

    </section>
  )
}

function TransactionDownloads({ data }) {
  const downloadJson = () => {
    downloadTextFile(
      'log-story.json',
      JSON.stringify(data, null, 2),
      'application/json',
    )
  }

  const downloadTxt = () => {
    downloadTextFile(
      'log-story.txt',
      buildLogStoryText(data),
      'text/plain',
    )
  }

  const downloadPdf = () => {
    const reportWindow = window.open('', '_blank')

    if (!reportWindow) return

    reportWindow.document.write(buildPrintableReport(data))
    reportWindow.document.close()
    reportWindow.focus()
    reportWindow.print()

  }

  return (
    <div className="transaction-downloads">
      <button type="button" className="transaction-downloads__button" onClick={downloadPdf}>
        <Download className="transaction-downloads__icon" />
        PDF
      </button>
      <button type="button" className="transaction-downloads__button" onClick={downloadTxt}>
        <Download className="transaction-downloads__icon" />
        TXT
      </button>
      <button type="button" className="transaction-downloads__button" onClick={downloadJson}>
        <Download className="transaction-downloads__icon" />
        JSON
      </button>
    </div>
  )
}

function TransactionCard({ transaction, displayOptions }) {
  return (
    <article className="transaction-card">
      <div className="transaction-card__header">
        <div>
          <div className="transaction-card__title">
            {transaction.display_name ?? transaction.transaction_id ?? 'transaction'}
          </div>
          <div className="transaction-card__meta">
            {transaction.message_type ?? 'Transaction'} - bloc #{transaction.log_index ?? 'N/A'} - MTI {transaction.mti ?? 'UNKNOWN'} - FLD 037 {transaction.fields?.['037'] ?? 'N/A'}
          </div>
        </div>
        <span className={`transaction-status transaction-status--${String(transaction.status ?? 'unknown').toLowerCase()}`}>
          {transaction.status ?? 'UNKNOWN'}
        </span>
      </div>

      {displayOptions.show_hsm && <HsmAnalysis hsm={transaction.hsm_analysis} />}
      {displayOptions.show_fields && <FieldGrid fields={transaction.fields ?? {}} />}
      {displayOptions.show_log_story && <LogStory story={transaction.log_story ?? []} />}
      {/* <TextList title="Observed Facts" items={transaction.observed_facts} /> */}
      {displayOptions.show_documentation_findings && (
        <DocumentationFindings findings={transaction.documentation_findings} />
      )}
      {/* <ReferenceList title="Sources" items={transaction.sources} /> */}
    </article>
  )
}

function visibleTransactions(transactions) {
  const ranked = [...transactions].sort((left, right) => {
    const weight = { FAILED: 0, WARNING: 1, UNKNOWN: 2, SUCCESS: 3 }
    return (weight[left.status] ?? 4) - (weight[right.status] ?? 4)
  })

  return ranked.slice(0, 30)
}

function FieldGrid({ fields }) {
  const keys = ['002', '003', '037', '039']

  return (
    <div className="transaction-fields">
      {keys.map((key) => (
        <div key={key} className="transaction-field">
          <span className="transaction-field__label">FLD {key}</span>
          <span className="transaction-field__value">{fields[key] ?? 'N/A'}</span>
        </div>
      ))}
    </div>
  )
}

function HsmAnalysis({ hsm }) {
  const commands = hsm?.commands ?? []

  if (!hsm || commands.length === 0) return null

  return (
    <div className="transaction-section hsm-analysis">
      <div className="hsm-analysis__header">
        <h5 className="transaction-section__title">HSM Analysis</h5>
        {hsm.thread && (
          <span className="hsm-analysis__thread">Thread {hsm.thread}</span>
        )}
      </div>
      <div className="hsm-analysis__list">
        {commands.map((command, index) => (
          <HsmCommandCard
            key={`${command.command}-${command.hsm_result_code}-${index}`}
            command={command}
          />
        ))}
      </div>
    </div>
  )
}

function HsmCommandCard({ command }) {
  const findings = command.documentation_findings ?? []
  const references = findings
    .filter((finding) => finding.source || finding.page || finding.paragraph)
    .map((finding) => (
      `${finding.source ?? 'PDF'}${finding.page !== undefined && finding.page !== null ? ` - p.${finding.page}` : ''}${finding.paragraph !== undefined && finding.paragraph !== null ? ` - para. ${finding.paragraph}` : ''}`
    ))
  const uniqueReferences = [...new Set(references)]

  return (
    <div className="hsm-command">
      <div className="hsm-command__top">
        <div className="hsm-command__codes">
          <span>
            <b>Command</b>
            {command.command || 'N/A'}
          </span>
          <span>
            <b>Response</b>
            {command.response_command || 'N/A'}
          </span>
          <span>
            <b>HsmResultCode</b>
            {command.hsm_result_code || 'N/A'}
          </span>
          <span>
            <b>Code retour</b>
            {command.return_code || 'N/A'}
          </span>
        </div>
        <span className={`log-story__status log-story__status--${String(command.status ?? 'unknown').toLowerCase()}`}>
          {command.status ?? 'UNKNOWN'}
        </span>
      </div>

      <div className="hsm-command__details">
        <HsmDetail label="Nom de la commande" value={command.command_name} />
        <HsmDetail label="Description de la commande" value={command.command_description} multiline />
        <HsmDetail label="Message envoye (TO HSM)" value={command.request_message} mono />
        <HsmDetail label="Reponse HSM" value={command.response_command} />
        <HsmDetail label="Message recu (FROM HSM)" value={command.response_message} mono />
        <HsmDetail label="Signification du code retour" value={command.return_code_meaning} multiline />
        <HsmDetail label="Resultat fonctionnel" value={command.functional_result} />
        <HsmDetail label="Interpretation technique" value={command.technical_interpretation} multiline />
        <div className="hsm-command__detail hsm-command__detail--full">
          <b>References documentaires</b>
          {uniqueReferences.length > 0 ? (
            <div className="hsm-command__references">
              {uniqueReferences.map((reference, index) => (
                <span key={index}>{reference}</span>
              ))}
            </div>
          ) : (
            <p>Non trouve dans la documentation fournie</p>
          )}
        </div>
      </div>
    </div>
  )
}

function HsmDetail({ label, value, mono, multiline }) {
  return (
    <div className={`hsm-command__detail${mono ? ' hsm-command__detail--mono' : ''}${multiline ? ' hsm-command__detail--full' : ''}`}>
      <b>{label}</b>
      <p>{value || 'Non trouve dans la documentation fournie'}</p>
    </div>
  )
}

function LogStory({ story }) {
  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">Log Story</h5>
      <div className="log-story">
        {story.map((item, index) => (
          <LogStoryRow key={`${item.function_name}-${index}`} item={item} />
        ))}
      </div>
    </div>
  )
}

function LogStoryRow({ item }) {
  const [open, setOpen] = useState(false)
  const hasError = item.status === 'ERROR' && item.error

  return (
    <div className="log-story__row">
      <span className="log-story__order">{item.order}</span>
      <span className="log-story__name">{item.function_name}</span>
      <span className={`log-story__status log-story__status--${String(item.status ?? 'unknown').toLowerCase()}`}>
        {item.status ?? 'UNKNOWN'}
      </span>
      {hasError && (
        <button type="button" className="log-story__more" onClick={() => setOpen((value) => !value)}>
          En savoir plus
        </button>
      )}
      {open && hasError && <ErrorDetails error={item.error} />}
    </div>
  )
}

function ErrorDetails({ error }) {
  const rows = [
    ['Fonction', error.function],
    ['Erreur detectee', error.detected_error],
    ['Exception', error.exception],
    ['Description', error.description],
    ['Path', error.path],
  ]

  return (
    <div className="error-details">
      {rows.map(([label, value]) => (
        <div key={label} className="error-details__row">
          <span>{label}</span>
          <strong>{value || 'Non renseigne'}</strong>
        </div>
      ))}
      {error.excel_reference && (
        <div className="error-details__row">
          <span>Excel</span>
          <strong>
            {error.excel_reference.source ?? 'N/A'}
            {error.excel_reference.sheet ? ` - ${error.excel_reference.sheet}` : ''}
            {error.excel_reference.row ? ` - ligne ${error.excel_reference.row}` : ''}
          </strong>
        </div>
      )}
    </div>
  )
}

function TextBlock({ title, value }) {
  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{title}</h5>
      <p className="transaction-section__text">{value}</p>
    </div>
  )
}

function TextList({ title, items }) {
  if (!items || items.length === 0) return null

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{title}</h5>
      <ul className="transaction-list-simple">
        {items.map((item, index) => (
          <li key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
        ))}
      </ul>
    </div>
  )
}

function DocumentationFindings({ findings }) {
  if (!findings || findings.length === 0) return null

  const visibleFindings = findings.slice(0, 4)

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">Documentation Findings</h5>
      <div className="doc-finding-list">
        {visibleFindings.map((finding, index) => (
          <div key={index} className="doc-finding">
            <strong>{finding.anomaly ?? finding.title ?? 'Anomaly justification'}</strong>
            {finding.observed_value && <p>Observed: {finding.observed_value}</p>}
            {finding.expected_rule && <p>Expected rule: {finding.expected_rule}</p>}
            {finding.explanation && <p>{finding.explanation}</p>}
            {(finding.source || finding.page || finding.paragraph) && (
              <p>
                Source: {finding.source ?? 'PDF'}
                {finding.page !== undefined && finding.page !== null ? ` - p.${finding.page}` : ''}
                {finding.paragraph !== undefined && finding.paragraph !== null ? ` - para. ${finding.paragraph}` : ''}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ReferenceList({ title, items }) {
  if (!items || items.length === 0) return null

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{title}</h5>
      <div className="response-references">
        {items.map((ref, index) => (
          <span key={index} className="reference-pill">
            <FileText className="reference-pill__icon" />
            {ref.source}
            {ref.page !== undefined && ref.page !== null && (
              <span className="reference-pill__page"> - p.{ref.page}</span>
            )}
            {ref.sheet && <span className="reference-pill__page"> - {ref.sheet}</span>}
            {ref.paragraph !== undefined && ref.paragraph !== null && (
              <span className="reference-pill__page"> - para. {ref.paragraph}</span>
            )}
            {ref.row !== undefined && ref.row !== null && (
              <span className="reference-pill__page"> - ligne {ref.row}</span>
            )}
          </span>
        ))}
      </div>
    </div>
  )
}

function downloadTextFile(filename, content, type) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function buildLogStoryText(data) {
  const lines = [data.summary ?? '', '']

  for (const transaction of data.transactions ?? []) {
    lines.push(`${transaction.display_name ?? transaction.transaction_id} | MTI ${transaction.mti ?? 'UNKNOWN'} | ${transaction.status}`)
    lines.push(`FLD 002=${transaction.fields?.['002'] ?? 'N/A'} FLD 003=${transaction.fields?.['003'] ?? 'N/A'} FLD 037=${transaction.fields?.['037'] ?? 'N/A'} FLD 039=${transaction.fields?.['039'] ?? 'N/A'}`)
    if (transaction.hsm_analysis?.commands?.length) {
      lines.push(`HSM Thread=${transaction.hsm_analysis.thread || 'N/A'}`)
      for (const command of transaction.hsm_analysis.commands) {
        lines.push(`HSM ${command.command || 'N/A'} -> ${command.response_command || 'N/A'} | HsmResultCode=${command.hsm_result_code || 'N/A'} | ReturnCode=${command.return_code || 'N/A'} | ${command.status || 'UNKNOWN'}`)
        lines.push(`CommandName=${command.command_name || 'Non trouve dans la documentation fournie'}`)
        lines.push(`CommandDescription=${command.command_description || 'Non trouve dans la documentation fournie'}`)
        if (command.request_message) lines.push(`TO HSM=${command.request_message}`)
        if (command.response_message) lines.push(`FROM HSM=${command.response_message}`)
        lines.push(`ReturnCodeMeaning=${command.return_code_meaning || 'Non trouve dans la documentation fournie'}`)
        lines.push(`FunctionalResult=${command.functional_result || 'UNKNOWN'}`)
        lines.push(`TechnicalInterpretation=${command.technical_interpretation || 'Non trouve dans la documentation fournie'}`)
      }
    }
    for (const item of transaction.log_story ?? []) {
      lines.push(`${String(item.order).padStart(3, '0')} ${item.function_name} ${item.status}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function buildPrintableReport(data) {
  const transactionHtml = (data.transactions ?? []).map((transaction) => {
    const fields = transaction.fields ?? {}
    const hsmRows = (transaction.hsm_analysis?.commands ?? []).map((command) => `
      <tr>
        <td>${escapeHtml(command.thread ?? transaction.hsm_analysis?.thread ?? 'N/A')}</td>
        <td>${escapeHtml(command.command ?? 'N/A')}</td>
        <td>${escapeHtml(command.response_command ?? 'N/A')}</td>
        <td>${escapeHtml(command.hsm_result_code ?? 'N/A')}</td>
        <td>${escapeHtml(command.return_code ?? 'N/A')}</td>
        <td>${escapeHtml(command.status ?? 'UNKNOWN')}</td>
      </tr>
      <tr>
        <td colspan="6">
          <b>Nom:</b> ${escapeHtml(command.command_name ?? 'Non trouve dans la documentation fournie')}<br>
          <b>Description:</b> ${escapeHtml(command.command_description ?? 'Non trouve dans la documentation fournie')}<br>
          <b>TO HSM:</b> ${escapeHtml(command.request_message ?? 'N/A')}<br>
          <b>FROM HSM:</b> ${escapeHtml(command.response_message ?? 'N/A')}<br>
          <b>Signification:</b> ${escapeHtml(command.return_code_meaning ?? 'Non trouve dans la documentation fournie')}<br>
          <b>Resultat:</b> ${escapeHtml(command.functional_result ?? 'UNKNOWN')}<br>
          <b>Interpretation:</b> ${escapeHtml(command.technical_interpretation ?? 'Non trouve dans la documentation fournie')}
        </td>
      </tr>
    `).join('')
    const storyRows = (transaction.log_story ?? []).map((item) => `
      <tr>
        <td>${escapeHtml(item.order)}</td>
        <td>${escapeHtml(item.function_name)}</td>
        <td>${escapeHtml(item.status)}</td>
      </tr>
    `).join('')
    const facts = (transaction.observed_facts ?? []).map((item) => (
      `<li>${escapeHtml(item)}</li>`
    )).join('')
    const findings = (transaction.documentation_findings ?? []).slice(0, 4).map((item) => (
      `<li><strong>${escapeHtml(item.anomaly ?? 'Anomaly justification')}</strong>${item.observed_value ? `<br>Observed: ${escapeHtml(item.observed_value)}` : ''}${item.expected_rule ? `<br>Expected rule: ${escapeHtml(item.expected_rule)}` : ''}${item.source ? `<br>Source: ${escapeHtml(item.source)}${item.page ? ` - p.${escapeHtml(item.page)}` : ''}` : ''}</li>`
    )).join('')

    return `
      <section class="transaction">
        <header>
          <h2>${escapeHtml(transaction.display_name ?? transaction.transaction_id)}</h2>
          <span>${escapeHtml(transaction.status ?? 'UNKNOWN')}</span>
        </header>
        <div class="fields">
          <div><b>MTI</b>${escapeHtml(transaction.mti ?? 'UNKNOWN')}</div>
          <div><b>FLD 002</b>${escapeHtml(fields['002'] ?? 'N/A')}</div>
          <div><b>FLD 003</b>${escapeHtml(fields['003'] ?? 'N/A')}</div>
          <div><b>FLD 037</b>${escapeHtml(fields['037'] ?? 'N/A')}</div>
          <div><b>FLD 039</b>${escapeHtml(fields['039'] ?? 'N/A')}</div>
        </div>
        ${hsmRows ? `
          <h3>HSM Analysis</h3>
          <table>
            <thead><tr><th>Thread</th><th>Command</th><th>Response</th><th>HsmResultCode</th><th>Return Code</th><th>Status</th></tr></thead>
            <tbody>${hsmRows}</tbody>
          </table>
        ` : ''}
        <h3>Log Story</h3>
        <table>
          <thead><tr><th>#</th><th>Function</th><th>Status</th></tr></thead>
          <tbody>${storyRows}</tbody>
        </table>
        ${facts ? `<h3>Observed Facts</h3><ul>${facts}</ul>` : ''}
        ${findings ? `<h3>Documentation Findings</h3><ul>${findings}</ul>` : ''}
      </section>
    `
  }).join('')

  return `
    <!doctype html>
    <html>
      <head>
        <title>Log Story Report</title>
        <style>
          body { margin: 32px; color: #172033; font-family: Arial, sans-serif; font-size: 12px; line-height: 1.45; }
          h1 { margin: 0 0 8px; font-size: 22px; }
          h2 { margin: 0; font-size: 16px; }
          h3 { margin: 16px 0 6px; color: #536176; font-size: 12px; text-transform: uppercase; }
          .summary { margin: 0 0 18px; }
          .transaction { break-inside: avoid; page-break-inside: avoid; margin: 0 0 18px; padding: 14px; border: 1px solid #d9e0ea; border-radius: 8px; }
          header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
          header span { padding: 4px 8px; border-radius: 999px; background: #f2f4f7; font-weight: 700; }
          .fields { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 10px 0; }
          .fields div { padding: 8px; background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 6px; overflow-wrap: anywhere; }
          .fields b { display: block; margin-bottom: 3px; color: #667085; font-size: 10px; }
          table { width: 100%; border-collapse: collapse; }
          th, td { padding: 6px 8px; border: 1px solid #e4e7ec; text-align: left; }
          th { background: #f8fafc; color: #536176; }
          ul { margin: 0; padding-left: 18px; }
          @page { margin: 18mm; }
        </style>
      </head>
      <body>
        <h1>Log Story Report</h1>
        <p class="summary">${escapeHtml(data.summary ?? '')}</p>
        ${transactionHtml}
      </body>
    </html>
  `
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

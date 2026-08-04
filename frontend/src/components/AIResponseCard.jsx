import React, { useState } from 'react'
import { FileText, AlertTriangle, CheckCircle2, BookOpen, ScrollText, Download } from 'lucide-react'

const severityStyles = {
  info: 'response-issue--info',
  warning: 'response-issue--warning',
  error: 'response-issue--error',
}

export function AIResponseCard({ data }) {
  const hasTransactions = data.transactions && data.transactions.length > 0
  const hasDocumentationSections = data.sections && data.sections.length > 0
  const displayOptions = getDisplayOptions(data)

  return (
    <section className="response-card">
      {data.summary && (
        <ResponseSection icon={ScrollText} title="Résumé">
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

      {!hasTransactions && hasDocumentationSections && (
        <ResponseSection icon={BookOpen} title="Analyse" bordered>
          <div className="response-doc-sections">
            {data.sections.map((section, i) => (
              <article key={`${section.title ?? 'section'}-${i}`} className="response-doc-section">
                {section.title && <h4>{section.title}</h4>}
                {section.blocks && section.blocks.length > 0 ? (
                  <div className="response-doc-section__blocks">
                    {deduplicateBlocks(section.blocks).map((block, index) => (
                      <ResponseBlock key={index} block={block} />
                    ))}
                  </div>
                ) : section.paragraphs && section.paragraphs.length > 0 ? (
                  section.paragraphs.map((paragraph, index) => (
                    <p key={index} className="response-doc-section__paragraph">
                      {paragraph}
                    </p>
                  ))
                ) : (
                  section.content && <p className="response-doc-section__paragraph">{section.content}</p>
                )}
                {section.items && section.items.length > 0 && (
                  <ul className="response-doc-section__items">
                    {section.items.map((item, index) => (
                      <li key={index}>
                        <strong>{item.label}</strong>
                        <span>{item.content}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
        </ResponseSection>
      )}

      {!hasTransactions && !hasDocumentationSections && data.story && data.story.length > 0 && (
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

      {!hasTransactions && data.references && data.references.length > 0 && (
        <ResponseSection icon={FileText} title="Références" bordered>
          <div className="response-references">
            {data.references.map((ref, i) => (
              <span key={i} className="reference-pill">
                <FileText className="reference-pill__icon" />
                {formatReference(ref)}
              </span>
            ))}
          </div>
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

function ResponseBlock({ block }) {
  if (!block || !block.type) return null

  switch (block.type) {
    case 'paragraph':
      return block.content ? (
        <p className="response-doc-section__paragraph">{block.content}</p>
      ) : null

    case 'list': {
      const ListTag = block.style === 'numbered' ? 'ol' : 'ul'

      return block.items && block.items.length > 0 ? (
        <ListTag className="response-doc-list">
          {block.items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ListTag>
      ) : null
    }

    case 'table':
      return <ResponseTable table={block} />

    case 'code':
      return block.content ? (
        <pre className="response-doc-code">
          <code>{block.content}</code>
        </pre>
      ) : null

    case 'key_value':
      return block.items && block.items.length > 0 ? (
        <KeyValueGrid items={block.items} />
      ) : null

    case 'callout':
      return block.content ? <ResponseCallout block={block} /> : null

    default:
      return null
  }
}

function deduplicateBlocks(blocks = []) {
  const seen = new Set()

  return blocks.filter((block) => {
    const identity = JSON.stringify(block)

    if (seen.has(identity)) {
      return false
    }

    seen.add(identity)
    return true
  })
}

function ResponseTable({ table }) {
  if (!table?.columns?.length || !table?.rows?.length) return null
  const rows = deduplicateTableRows(table.rows)

  return (
    <div className="response-doc-table-wrap">
      {table.title && <div className="response-doc-table__title">{table.title}</div>}
      <table className="response-doc-table">
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {table.columns.map((column) => (
                <td key={column.key}>{row[column.key] ?? ''}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function KeyValueGrid({ items }) {
  const uniqueItems = deduplicateKeyValueItems(items)

  return (
    <dl className="response-doc-kv">
      {uniqueItems.map((item, index) => (
        <div key={index} className="response-doc-kv__item">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function deduplicateTableRows(rows = []) {
  const seen = new Set()

  return rows.filter((row) => {
    const identity = JSON.stringify(row)

    if (seen.has(identity)) {
      return false
    }

    seen.add(identity)
    return true
  })
}

function deduplicateKeyValueItems(items = []) {
  const seen = new Set()

  return items.filter((item) => {
    const identity = `${item.label ?? ''}:${item.value ?? ''}`

    if (seen.has(identity)) {
      return false
    }

    seen.add(identity)
    return true
  })
}

function ResponseCallout({ block }) {
  return (
    <div className={`response-doc-callout response-doc-callout--${block.severity ?? 'info'}`}>
      {block.title && <div className="response-doc-callout__title">{block.title}</div>}
      <div className="response-doc-callout__content">{block.content}</div>
    </div>
  )
}

function getDisplayOptions(data) {
  return {
    analysis_mode: 'log',
    show_fields: true,
    show_log_story: true,
    show_hsm: false,
    show_documentation_findings: true,
    ...(data.display_options ?? {}),
  }
}

function TransactionDownloads({ data }) {
  const displayOptions = getDisplayOptions(data)

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
      buildLogStoryText(data, displayOptions),
      'text/plain',
    )
  }

  const downloadPdf = () => {
    const reportWindow = window.open('', '_blank')

    if (!reportWindow) return

    reportWindow.document.write(buildPrintableReport(data, displayOptions))
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
  const hsmThread = transaction.hsm_analysis?.thread
  const hsmOnly = (
    displayOptions.analysis_mode === 'hsm'
    || (
      displayOptions.show_hsm
      && !displayOptions.show_log_story
      && !displayOptions.show_fields
    )
  )

  return (
    <article className="transaction-card">
      <div className="transaction-card__header">
        <div>
          <div className="transaction-card__title">
            {hsmOnly
              ? `HSM Analysis #${transaction.log_index ?? 'N/A'}${hsmThread ? ` - Thread ${hsmThread}` : ''}`
              : transaction.display_name ?? transaction.transaction_id ?? 'transaction'}
          </div>
          {!hsmOnly && (
            <div className="transaction-card__meta">
              {transaction.message_type ?? 'Transaction'} - bloc #{transaction.log_index ?? 'N/A'} - MTI {transaction.mti ?? 'UNKNOWN'} - FLD 037 {transaction.fields?.['037'] ?? 'N/A'}
            </div>
          )}
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
  const commands = (hsm?.commands ?? []).filter(
    (command) => command.request_message || command.hsm_result_code,
  )

  if (!hsm || commands.length === 0) return null

  return (
    <div className="transaction-section hsm-analysis">
      <div className="hsm-analysis__header">
        <h5 className="transaction-section__title">HSM Analysis</h5>
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
        <span className={`log-story__status log-story__status--${String(command.status ?? 'unknown').toLowerCase()}`}>
          {command.status ?? 'UNKNOWN'}
        </span>
      </div>

      <div className="hsm-command__details">
        <HsmDetail label="Message envoye (TO HSM)" value={command.request_message} mono />
        <HsmDetail label="Thread" value={command.thread} />
        <HsmDetail label="Commande" value={command.command ? `command_${command.command}` : ''} />
        <HsmDetail label="Reponse HSM" value={command.response_command} />
        <HsmDetail label="Code retour" value={command.return_code} />
        <HsmDetail label="HsmResultCode" value={command.hsm_result_code} />
        {/* <HsmDetail
          label="Description"
          value={
            meaningfulHsmDescription(command.return_code_meaning)
            || meaningfulHsmDescription(command.command_description)
            || command.trace_description
            || command.detected_status
          }
          multiline
        /> */}
        {/* <div className="hsm-command__detail hsm-command__detail--full">
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
        </div> */}
      </div>
    </div>
  )
}

function formatReference(ref) {
  const parts = [ref.source ?? 'Document']

  if (ref.section || ref.heading) {
    parts.push(ref.section ?? ref.heading)
  }

  if (ref.printed_page) {
    parts.push(`page imprimee ${ref.printed_page}`)
  }

  if (ref.pdf_page !== undefined && ref.pdf_page !== null) {
    parts.push(`page PDF ${ref.pdf_page}`)
  } else if (ref.page !== undefined && ref.page !== null) {
    parts.push(`page PDF ${ref.page}`)
  }

  if (ref.sheet) {
    parts.push(ref.sheet)
  }

  if (ref.paragraph !== undefined && ref.paragraph !== null) {
    parts.push(`para. ${ref.paragraph}`)
  }

  return parts.join(' - ')
}

function meaningfulHsmDescription(value) {
  if (!value) return ''

  return String(value).includes('Non trouve dans la documentation')
    ? ''
    : value
}

function hsmDescription(command) {
  return (
    meaningfulHsmDescription(command.return_code_meaning)
    || meaningfulHsmDescription(command.command_description)
    || command.trace_description
    || command.detected_status
    || 'Non trouve dans la documentation fournie'
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
            {finding.field && <p>Champ: {finding.field}</p>}
            {finding.observed_value && <p>Valeur observee: {finding.observed_value}</p>}
            {finding.expected_condition && <p>Condition attendue: {finding.expected_condition}</p>}
            {finding.context && <p>Contexte: {finding.context}</p>}
            {finding.expected_rule && <p>Regle documentaire: {finding.expected_rule}</p>}
            {finding.explanation && <p>{finding.explanation}</p>}
            {finding.conclusion && <p>{finding.conclusion}</p>}
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

function buildLogStoryText(data, displayOptions = getDisplayOptions(data)) {
  const lines = [data.summary ?? '', '']

  for (const transaction of data.transactions ?? []) {
    lines.push(`${transaction.display_name ?? transaction.transaction_id} | MTI ${transaction.mti ?? 'UNKNOWN'} | ${transaction.status}`)
    lines.push(`FLD 002=${transaction.fields?.['002'] ?? 'N/A'} FLD 003=${transaction.fields?.['003'] ?? 'N/A'} FLD 037=${transaction.fields?.['037'] ?? 'N/A'} FLD 039=${transaction.fields?.['039'] ?? 'N/A'}`)
    if (transaction.hsm_analysis?.commands?.length) {
      lines.push(`HSM Thread=${transaction.hsm_analysis.thread || 'N/A'}`)
      for (const command of transaction.hsm_analysis.commands) {
        lines.push(`Thread=${command.thread || transaction.hsm_analysis.thread || 'N/A'}`)
        if (command.request_message) lines.push(`TO HSM=${command.request_message}`)
        lines.push(`Commande=command_${command.command || 'N/A'}`)
        lines.push(`Reponse HSM=${command.response_command || 'N/A'}`)
        lines.push(`Code retour=${command.return_code || 'N/A'}`)
        // lines.push(`HsmResultCode=${command.hsm_result_code || 'N/A'}`)
        lines.push(`Description=${hsmDescription(command)}`)
      }
    }
    if (displayOptions.show_log_story) {
      for (const item of transaction.log_story ?? []) {
        lines.push(`${String(item.order).padStart(3, '0')} ${item.function_name} ${item.status}`)
      }
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

function buildPrintableReport(data, displayOptions = getDisplayOptions(data)) {
  const transactionHtml = (data.transactions ?? []).map((transaction) => {
    const fields = transaction.fields ?? {}
    const hsmRows = (transaction.hsm_analysis?.commands ?? []).map((command) => `
      <tr>
        <td>${escapeHtml(command.thread ?? transaction.hsm_analysis?.thread ?? 'N/A')}</td>
        <td>${escapeHtml(command.command ? `command_${command.command}` : 'N/A')}</td>
        <td>${escapeHtml(command.response_command ?? 'N/A')}</td>
        <td>${escapeHtml(command.hsm_result_code ?? 'N/A')}</td>
        <td>${escapeHtml(command.return_code ?? 'N/A')}</td>
        <td>${escapeHtml(command.status ?? 'UNKNOWN')}</td>
      </tr>
      <tr>
        <td colspan="6">
          <b>TO HSM:</b> ${escapeHtml(command.request_message ?? 'N/A')}<br>
          <b>Thread:</b> ${escapeHtml(command.thread ?? transaction.hsm_analysis?.thread ?? 'N/A')}<br>
          <b>Description:</b> ${escapeHtml(hsmDescription(command))}
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
          // <table>
          //   <thead><tr><th>Thread</th><th>Command</th><th>Response</th><th>HsmResultCode</th><th>Return Code</th><th>Status</th></tr></thead>
          //   <tbody>${hsmRows}</tbody>
          // </table>
        ` : ''}
        ${displayOptions.show_log_story ? `
          <h3>Log Story</h3>
          <table>
            <thead><tr><th>#</th><th>Function</th><th>Status</th></tr></thead>
            <tbody>${storyRows}</tbody>
          </table>
        ` : ''}
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

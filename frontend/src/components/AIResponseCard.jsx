import React, { useState } from 'react'
import { FileText, AlertTriangle, CheckCircle2, BookOpen, ScrollText, Download, X, ZoomIn } from 'lucide-react'
import {
  getAdminDocumentHighlightedViewUrl,
  getAdminDocumentPagePreviewUrl,
  getAdminDocumentViewUrl,
} from '../services/api'
import hpsLogo from '../assets/HPS.png'

const severityStyles = {
  info: 'response-issue--info',
  warning: 'response-issue--warning',
  error: 'response-issue--error',
}

function displayValue(value) {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)

  try {
    return JSON.stringify(value, null, 2)
  } catch (_error) {
    return String(value)
  }
}

export function AIResponseCard({ data, onDocumentPreview }) {
  const transactions = Array.isArray(data.transactions)
    ? data.transactions.filter((transaction) => transaction && typeof transaction === 'object')
    : []
  const sections = Array.isArray(data.sections)
    ? data.sections.filter((section) => section && typeof section === 'object')
    : []
  const story = Array.isArray(data.story) ? data.story : []
  const references = Array.isArray(data.references)
    ? data.references.filter((reference) => reference && typeof reference === 'object')
    : []
  const issues = Array.isArray(data.issues)
    ? data.issues.filter((issue) => issue && typeof issue === 'object')
    : []
  const hasTransactions = transactions.length > 0
  const hasDocumentationSections = sections.length > 0
  const displayOptions = getDisplayOptions(data)
  const transactionGroups = hasTransactions
    ? groupTransactionsBySource(transactions)
    : []

  return (
    <section className="response-card">
      {hasTransactions ? (
        <ResponseSection icon={ScrollText} title="Statistiques">
          <LogStatisticsDashboard
            statistics={data.statistics}
            transactions={transactions}
          />
        </ResponseSection>
      ) : data.summary && (
        <ResponseSection icon={ScrollText} title="Résumé">
          <p className="response-card__paragraph">{displayValue(data.summary)}</p>
        </ResponseSection>
      )}

      {hasTransactions && (
        <ResponseSection icon={BookOpen} title="Log story" bordered>
          <TransactionDownloads data={data} />
          {transactions.length > 30 && (
            <p className="transaction-limit-note">
              Affichage des 30 transactions les plus importantes. Le telechargement TXT/JSON contient les {transactions.length} transactions.
            </p>
          )}
          <div className="transaction-list">
            {transactionGroups.map((group, groupIndex) => (
              <section
                key={group.source}
                className="trace-analysis-group"
              >
                {transactionGroups.length > 1 && (
                  <div className="trace-analysis-group__header">
                    <div>
                      <span className="trace-analysis-group__label">
                        Trace {groupIndex + 1}
                      </span>
                      <h5>{group.source}</h5>
                    </div>
                    <div className="trace-analysis-group__stats">
                      <span>{group.transactions.length} transaction(s)</span>
                      <span>{countTransactionsByStatus(group.transactions, 'FAILED')} echec(s)</span>
                      <span>{countTransactionsWithoutResponse(group.transactions)} sans retour</span>
                    </div>
                  </div>
                )}

                <div className="trace-analysis-group__transactions">
                  {group.transactions.map((transaction, index) => (
                    <TransactionCard
                      key={`${transaction.source ?? group.source}-${transaction.transaction_id ?? index}-${index}`}
                      transaction={transaction}
                      displayOptions={displayOptions}
                      colorIndex={index % 8}
                      onPreviewDocument={onDocumentPreview}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </ResponseSection>
      )}

      {!hasTransactions && hasDocumentationSections && (
        <ResponseSection icon={BookOpen} title="Analyse" bordered>
          <div className="response-doc-sections">
            {sections.map((section, i) => {
              const blocks = Array.isArray(section.blocks) ? section.blocks : []
              const paragraphs = Array.isArray(section.paragraphs) ? section.paragraphs : []
              const items = Array.isArray(section.items) ? section.items : []

              return (
                <article key={`${section.title ?? 'section'}-${i}`} className="response-doc-section">
                  {section.title && <h4>{displayValue(section.title)}</h4>}
                  {blocks.length > 0 ? (
                    <div className="response-doc-section__blocks">
                      {deduplicateBlocks(blocks).map((block, index) => (
                        <ResponseBlock key={index} block={block} />
                      ))}
                    </div>
                  ) : paragraphs.length > 0 ? (
                    paragraphs.map((paragraph, index) => (
                      <p key={index} className="response-doc-section__paragraph">
                        {displayValue(paragraph)}
                      </p>
                    ))
                  ) : (
                    section.content && <p className="response-doc-section__paragraph">{displayValue(section.content)}</p>
                  )}
                  {items.length > 0 && (
                    <ul className="response-doc-section__items">
                      {items.map((item, index) => (
                        <li key={index}>
                          <strong>{displayValue(item.label)}</strong>
                          <span>{displayValue(item.content)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </article>
              )
            })}
          </div>
        </ResponseSection>
      )}

      {!hasTransactions && !hasDocumentationSections && story.length > 0 && (
        <ResponseSection icon={BookOpen} title="Details" bordered>
          <ol className="response-story-list">
            {story.map((step, i) => (
              <li key={i} className="response-story-list__item">
                <span className="response-story-list__marker">{i + 1}</span>
                <span>{displayValue(step)}</span>
              </li>
            ))}
          </ol>
        </ResponseSection>
      )}

      {!hasTransactions && references.length > 0 && (
        <ResponseSection icon={FileText} title="Références" bordered>
          <div className="response-references">
            {references.map((ref, i) => (
              <ReferencePill
                key={i}
                reference={ref}
                onPreview={onDocumentPreview}
              />
            ))}
          </div>
        </ResponseSection>
      )}

      {issues.length > 0 && (
        <ResponseSection icon={AlertTriangle} title="Notes" bordered>
          <ul className="response-issues-list">
            {issues.map((iss, i) => (
              <li key={i} className={`response-issue ${severityStyles[iss.severity] ?? severityStyles.info}`}>
                <div className="response-issue__title">{displayValue(iss.title)}</div>
                {iss.detail && <div className="response-issue__detail">{displayValue(iss.detail)}</div>}
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
        <p className="response-doc-section__paragraph">{displayValue(block.content)}</p>
      ) : null

    case 'list': {
      const ListTag = block.style === 'numbered' ? 'ol' : 'ul'
      const items = Array.isArray(block.items) ? block.items : []

      return items.length > 0 ? (
        <ListTag className="response-doc-list">
          {items.map((item, index) => (
            <li key={index}>{displayValue(item)}</li>
          ))}
        </ListTag>
      ) : null
    }

    case 'table':
      return <ResponseTable table={block} />

    case 'code':
      return block.content ? (
        <pre className="response-doc-code">
          <code>{displayValue(block.content)}</code>
        </pre>
      ) : null

    case 'key_value':
      return Array.isArray(block.items) && block.items.length > 0 ? (
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
  const columns = normalizeTableColumns(table?.columns)
  const rows = Array.isArray(table?.rows) ? deduplicateTableRows(table.rows) : []

  if (!columns.length || !rows.length) return null

  return (
    <div className="response-doc-table-wrap">
      {table.title && <div className="response-doc-table__title">{displayValue(table.title)}</div>}
      <table className="response-doc-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{displayValue(column.label)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column, columnIndex) => (
                <td key={column.key}>
                  {displayValue(Array.isArray(row) ? row[columnIndex] : row?.[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function normalizeTableColumns(columns) {
  if (!Array.isArray(columns)) return []

  return columns
    .map((column, index) => {
      if (typeof column === 'string') {
        return {
          key: column,
          label: column,
        }
      }

      if (!column || typeof column !== 'object') {
        return null
      }

      const key = column.key ?? column.label ?? `column_${index}`

      return {
        key: String(key),
        label: column.label ?? column.key ?? `Colonne ${index + 1}`,
      }
    })
    .filter(Boolean)
}

function KeyValueGrid({ items }) {
  const uniqueItems = deduplicateKeyValueItems(Array.isArray(items) ? items : [])

  return (
    <dl className="response-doc-kv">
      {uniqueItems.map((item, index) => (
        <div key={index} className="response-doc-kv__item">
          <dt>{displayValue(item.label)}</dt>
          <dd>{displayValue(item.value ?? item.content)}</dd>
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
      {block.title && <div className="response-doc-callout__title">{displayValue(block.title)}</div>}
      <div className="response-doc-callout__content">{displayValue(block.content)}</div>
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
    setTimeout(() => reportWindow.print(), 350)

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

function TransactionCard({ transaction, displayOptions, colorIndex = 0, onPreviewDocument }) {
  const fields = transaction.fields && typeof transaction.fields === 'object' && !Array.isArray(transaction.fields)
    ? transaction.fields
    : {}
  const hsmAnalysis = transaction.hsm_analysis && typeof transaction.hsm_analysis === 'object'
    ? transaction.hsm_analysis
    : null
  const logStory = Array.isArray(transaction.log_story) ? transaction.log_story : []
  const hsmThread = hsmAnalysis?.thread
  const field037 = fields['037'] ?? 'N/A'
  const statusTone = String(transaction.status ?? 'unknown').toLowerCase()
  const hsmOnly = (
    displayOptions.analysis_mode === 'hsm'
    || (
      displayOptions.show_hsm
      && !displayOptions.show_log_story
      && !displayOptions.show_fields
    )
  )

  return (
    <article className={`transaction-card transaction-card--tone-${colorIndex} transaction-card--${statusTone}`}>
      <div className="transaction-card__header">
        <div>
          <div className="transaction-card__title">
            {hsmOnly ? (
              `HSM Analysis #${transaction.log_index ?? 'N/A'}${hsmThread ? ` - Thread ${hsmThread}` : ''}`
            ) : (
              <>
                FLD 037{' '}
                <strong className="transaction-card__highlight">{field037}</strong>
              </>
            )}
          </div>
        </div>
        <span className={`transaction-status transaction-status--${statusTone}`}>
          {transaction.status ?? 'UNKNOWN'}
        </span>
      </div>

      {displayOptions.show_fields && <FieldGrid fields={fields} />}
      {displayOptions.show_log_story && <LogStory story={logStory} />}
      {displayOptions.show_hsm && (
        <HsmAnalysis
          hsm={hsmAnalysis}
          onPreviewDocument={onPreviewDocument}
        />
      )}
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

function groupTransactionsBySource(transactions = [], { limit = true } = {}) {
  const selectedTransactions = limit
    ? visibleTransactions(transactions)
    : transactions
  const selectedBySource = selectedTransactions.reduce((groups, transaction) => {
    const source = transaction.source || transaction.trace_filename || 'Trace sans nom'

    if (!groups.has(source)) {
      groups.set(source, [])
    }

    groups.get(source).push(transaction)
    return groups
  }, new Map())
  const sourceOrder = []

  transactions.forEach((transaction) => {
    const source = transaction.source || transaction.trace_filename || 'Trace sans nom'

    if (selectedBySource.has(source) && !sourceOrder.includes(source)) {
      sourceOrder.push(source)
    }
  })

  return sourceOrder.map((source) => ({
    source,
    transactions: selectedBySource.get(source) ?? [],
  }))
}

function LogStatisticsDashboard({ statistics = {}, transactions = [] }) {
  const total = statistics.total_transactions ?? transactions.length ?? 0
  const failed = statistics.failed_transactions ?? countTransactionsByStatus(transactions, 'FAILED')
  const noResponse = statistics.no_response_transactions ?? countTransactionsWithoutResponse(transactions)
  const alerts = statistics.alert_transactions ?? statistics.warning_transactions ?? countTransactionsByStatus(transactions, 'WARNING')
  const items = [
    {
      label: 'Nbr transactions',
      value: total,
      tone: 'neutral',
    },
    {
      label: 'Nbr échec',
      value: failed,
      tone: 'danger',
    },
    {
      label: 'Sans retour',
      value: noResponse,
      tone: 'warning',
    },
    {
      label: 'Alerte détectée',
      value: alerts,
      tone: 'info',
    },
  ]

  return (
    <div className="log-dashboard">
      {items.map((item) => (
        <div
          key={item.label}
          className={`log-dashboard__item log-dashboard__item--${item.tone}`}
        >
          <span className="log-dashboard__label">{item.label}</span>
          <strong className="log-dashboard__value">{item.value}</strong>
        </div>
      ))}
    </div>
  )
}

function countTransactionsByStatus(transactions, status) {
  return transactions.filter((transaction) => transaction.status === status).length
}

function countTransactionsWithoutResponse(transactions) {
  return transactions.filter((transaction) => {
    const mti = String(transaction.mti ?? '')
    const fields = transaction.fields ?? {}

    return (
      ['0100', '0200', '1100'].includes(mti)
      && !transaction.response_mti
      && !fields['039']
    )
  }).length
}

function FieldGrid({ fields }) {
  const keys = ['002', '003', '037', '039']
  const normalizedFields = fields && typeof fields === 'object' && !Array.isArray(fields)
    ? fields
    : {}

  return (
    <div className="transaction-fields">
      {keys.map((key) => (
        <div key={key} className="transaction-field">
          <span className="transaction-field__label">FLD {key}</span>
          <span className="transaction-field__value">{displayValue(normalizedFields[key] ?? 'N/A')}</span>
        </div>
      ))}
    </div>
  )
}

function HsmAnalysis({ hsm, onPreviewDocument }) {
  const commands = (Array.isArray(hsm?.commands) ? hsm.commands : []).filter(
    (command) => command.request_message || command.hsm_result_code,
  )

  if (!hsm || commands.length === 0) return null

  return (
    <div className="transaction-section hsm-analysis">
      <div className="hsm-analysis__header">
        <h5 className="transaction-section__title">Analyse HSM </h5>
      </div>
      <div className="hsm-analysis__list">
        {commands.map((command, index) => (
          <HsmCommandCard
            key={`${command.command}-${command.hsm_result_code}-${index}`}
            command={command}
            onPreviewDocument={onPreviewDocument}
          />
        ))}
      </div>
    </div>
  )
}

function HsmCommandCard({ command, onPreviewDocument }) {
  const [showResultHelp, setShowResultHelp] = useState(false)
  const findings = Array.isArray(command.documentation_findings)
    ? command.documentation_findings
    : []
  const references = findings
    .filter((finding) => finding.source || finding.page || finding.paragraph)
    .map((finding) => ({
      source: finding.source,
      original_source: finding.original_source,
      document_id: finding.document_id,
      page: finding.page,
      pdf_page: finding.pdf_page ?? finding.page,
      printed_page: finding.printed_page,
      section: finding.section ?? finding.heading,
      heading: finding.heading,
      paragraph: finding.paragraph,
    }))
  const uniqueReferences = deduplicateReferences(references)
  const resultDescription = hsmDescription(command)
  const resultSearchText = command.documented_return_code_line || resultDescription

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
        <div className="hsm-command__detail hsm-command__detail--result">
          <b>HsmResultCode</b>
          <div className="hsm-command__result-line">
            <p>{command.hsm_result_code || 'Non trouve dans la documentation fournie'}</p>
            <button
              type="button"
              className="hsm-command__more"
              onClick={() => setShowResultHelp((current) => !current)}
            >
              En savoir plus
            </button>
          </div>
        </div>
        {showResultHelp && (
          <div className="hsm-command__detail hsm-command__detail--full hsm-command__result-help">
            <div className="hsm-command__result-help-grid">
              <div className="hsm-command__result-help-content">
                <b>Signification du HsmResultCode</b>
                <p>{resultDescription}</p>
                {uniqueReferences.length > 0 && (
                  <div className="hsm-command__references">
                    {uniqueReferences.map((reference, index) => (
                      <ReferencePill
                        key={index}
                        reference={reference}
                        searchText={resultSearchText}
                        onPreview={onPreviewDocument}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
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

function referenceIdentity(ref) {
  return [
    ref.document_id ?? '',
    ref.source ?? '',
    ref.original_source ?? '',
    ref.page ?? ref.pdf_page ?? '',
    ref.printed_page ?? '',
    ref.sheet ?? '',
    ref.paragraph ?? '',
    ref.row ?? '',
  ].join('|')
}

function deduplicateReferences(references = []) {
  const seen = new Set()

  return references.filter((reference) => {
    const identity = referenceIdentity(reference)

    if (seen.has(identity)) return false

    seen.add(identity)
    return true
  })
}

function referenceUrl(reference, searchText = '') {
  const documentId = reference.document_id || reference.documentId

  if (!documentId) return ''

  const page = reference.pdf_page ?? reference.page
  const source = String(reference.source || reference.original_source || '').toLowerCase()
  const isPdf = source.includes('.pdf')
  const fragments = []

  if (page !== undefined && page !== null) {
    fragments.push(`page=${encodeURIComponent(page)}`)
  }

  if (isPdf && searchText) {
    const highlightedUrl = getAdminDocumentHighlightedViewUrl(documentId, {
      page,
      search: searchText,
    })

    return `${highlightedUrl}${page !== undefined && page !== null ? `#page=${encodeURIComponent(page)}` : ''}`
  }

  if (searchText) {
    fragments.push(`search=${encodeURIComponent(searchText)}`)
  }

  return `${getAdminDocumentViewUrl(documentId)}${fragments.length ? `#${fragments.join('&')}` : ''}`
}

function documentViewUrl(reference) {
  const documentId = reference.document_id || reference.documentId

  if (!documentId) return ''

  const page = reference.pdf_page ?? reference.page

  return `${getAdminDocumentViewUrl(documentId)}${page !== undefined && page !== null ? `#page=${encodeURIComponent(page)}` : ''}`
}

function ReferencePill({ reference, searchText = '', onPreview }) {
  const label = formatReference(reference)
  const url = referenceUrl(reference, searchText)
  const canPreview = Boolean(onPreview && firstPreviewablePdfReference([reference]))
  const content = (
    <>
      <FileText className="reference-pill__icon" />
      {label}
    </>
  )

  if (canPreview) {
    return (
      <button
        type="button"
        className="reference-pill reference-pill--link"
        title={`Afficher l'aperçu de ${label}`}
        onClick={() => onPreview({
          reference,
          searchText,
          label,
        })}
      >
        {content}
      </button>
    )
  }

  if (!url) {
    return <span className="reference-pill">{content}</span>
  }

  return (
    <a
      className="reference-pill reference-pill--link"
      href={url}
      target="_blank"
      rel="noreferrer"
      title={`Ouvrir ${label}`}
    >
      {content}
    </a>
  )
}

export function DocumentPreviewPanel({ preview, onClose }) {
  const [isZoomOpen, setIsZoomOpen] = useState(false)
  const reference = preview.reference
  const searchText = preview.searchText ?? ''
  const label = preview.label ?? formatReference(reference)
  const documentId = reference.document_id || reference.documentId
  const page = reference.pdf_page ?? reference.page
  const previewUrl = getAdminDocumentPagePreviewUrl(documentId, {
    page,
    search: searchText,
  })
  const viewUrl = documentViewUrl(reference)
  const title = reference.section || reference.heading || reference.source || 'Document'

  return (
    <aside className="document-side-preview" aria-label="Apercu du document">
      <div className="document-side-preview__header">
        <div>
          <span className="document-side-preview__eyebrow">Apercu du document</span>
          <h4>{title}</h4>
          <p>{label}</p>
        </div>
        <button
          type="button"
          className="document-side-preview__close"
          onClick={onClose}
          aria-label="Fermer l'aperçu"
        >
          <X />
        </button>
      </div>

      <button
        type="button"
        className="document-side-preview__canvas"
        onClick={() => setIsZoomOpen(true)}
        title="Agrandir l'aperçu"
      >
        <img src={previewUrl} alt={`Apercu du document page ${page}`} />
        <span className="document-side-preview__zoom-hint">
          <ZoomIn />
          Agrandir
        </span>
      </button>

      {searchText && (
        <div className="document-side-preview__extract">
          <span>Extrait pertinent</span>
          <p>{searchText}</p>
        </div>
      )}

      <a
        className="document-side-preview__open"
        href={viewUrl}
        target="_blank"
        rel="noreferrer"
      >
        Ouvrir le PDF
      </a>

      {isZoomOpen && (
        <div className="document-zoom" role="dialog" aria-modal="true" aria-label="Apercu agrandi du document">
          <div className="document-zoom__backdrop" onClick={() => setIsZoomOpen(false)} />
          <div className="document-zoom__panel">
            <div className="document-zoom__header">
              <div>
                <span className="document-side-preview__eyebrow">Apercu agrandi</span>
                <h4>{title}</h4>
                <p>{label}</p>
              </div>
              <button
                type="button"
                className="document-side-preview__close"
                onClick={() => setIsZoomOpen(false)}
                aria-label="Fermer l'aperçu agrandi"
              >
                <X />
              </button>
            </div>
            <div className="document-zoom__canvas">
              <img src={previewUrl} alt={`Apercu agrandi du document page ${page}`} />
            </div>
            <a
              className="document-side-preview__open"
              href={viewUrl}
              target="_blank"
              rel="noreferrer"
            >
              Ouvrir le PDF complet
            </a>
          </div>
        </div>
      )}
    </aside>
  )
}

function firstPreviewablePdfReference(references = []) {
  return references.find((reference) => {
    const documentId = reference.document_id || reference.documentId
    const page = reference.pdf_page ?? reference.page
    const source = String(reference.source || reference.original_source || '').toLowerCase()

    return documentId && source.includes('.pdf') && page !== undefined && page !== null
  })
}

function PdfReferencePreview({ reference, searchText = '', side = false }) {
  if (!reference) return null

  const documentId = reference.document_id || reference.documentId
  const page = reference.pdf_page ?? reference.page

  if (!documentId || page === undefined || page === null) return null

  const previewUrl = getAdminDocumentPagePreviewUrl(documentId, {
    page,
    search: searchText,
  })
  const viewUrl = referenceUrl(reference, searchText)

  return (
    <a
      className={`pdf-reference-preview${side ? ' pdf-reference-preview--side' : ''}`}
      href={viewUrl}
      target="_blank"
      rel="noreferrer"
      title="Ouvrir le PDF"
    >
      <span className="pdf-reference-preview__label">Apercu PDF - page {page}</span>
      <img src={previewUrl} alt={`Apercu PDF page ${page}`} loading="lazy" />
    </a>
  )
}

function meaningfulHsmDescription(value) {
  if (!value) return ''

  return String(value).includes('Non trouve dans la documentation')
    ? ''
    : value
}

function hsmDescription(command) {
  return (
    meaningfulHsmDescription(command.documented_return_code_line)
    || meaningfulHsmDescription(command.return_code_meaning)
    || meaningfulHsmDescription(command.command_description)
    || 'Non trouve dans la documentation fournie'
  )
}

function HsmDetail({ label, value, mono, multiline }) {
  return (
    <div className={`hsm-command__detail${mono ? ' hsm-command__detail--mono' : ''}${multiline ? ' hsm-command__detail--full' : ''}`}>
      <b>{label}</b>
      <p>{displayValue(value) || 'Non trouve dans la documentation fournie'}</p>
    </div>
  )
}

function LogStory({ story }) {
  const rows = Array.isArray(story) ? story : []

  if (rows.length === 0) return null

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">Fonctions</h5>
      <div className="log-story">
        {rows.map((item, index) => (
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
  const details = error && typeof error === 'object' && !Array.isArray(error)
    ? error
    : {}
  const rows = [
    ['Fonction', details.function],
    ['Erreur detectee', details.detected_error],
    ['Exception', details.exception],
    ['Description', details.description],
    ['Path', details.path],
  ]

  return (
    <div className="error-details">
      {rows.map(([label, value]) => (
        <div key={label} className="error-details__row">
          <span>{label}</span>
          <strong>{displayValue(value) || 'Non renseigne'}</strong>
        </div>
      ))}
      {details.excel_reference && (
        <div className="error-details__row">
          <span>Excel</span>
          <strong>
            {displayValue(details.excel_reference.source ?? 'N/A')}
            {details.excel_reference.sheet ? ` - ${displayValue(details.excel_reference.sheet)}` : ''}
            {details.excel_reference.row ? ` - ligne ${displayValue(details.excel_reference.row)}` : ''}
          </strong>
        </div>
      )}
    </div>
  )
}

function functionErrorType(item) {
  if (!item || item.status !== 'ERROR') {
    return ''
  }

  const error = item.error ?? {}

  return (
    item.detected_error
    || error.detected_error
    || error.exception
    || error.description
    || 'Erreur non renseignee'
  )
}

function functionErrorDetails(item) {
  if (!item || item.status !== 'ERROR') {
    return ''
  }

  const error = item.error ?? {}
  const details = [
    error.description && `Description: ${error.description}`,
    error.exception && `Exception: ${error.exception}`,
    error.path && `Path: ${error.path}`,
    error.excel_reference && (
      `Excel: ${[
        error.excel_reference.source,
        error.excel_reference.sheet,
        error.excel_reference.row ? `ligne ${error.excel_reference.row}` : '',
      ].filter(Boolean).join(' - ')}`
    ),
  ].filter(Boolean)

  return details.join(' | ')
}

function TextBlock({ title, value }) {
  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{displayValue(title)}</h5>
      <p className="transaction-section__text">{displayValue(value)}</p>
    </div>
  )
}

function TextList({ title, items }) {
  const listItems = Array.isArray(items) ? items : []

  if (listItems.length === 0) return null

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{displayValue(title)}</h5>
      <ul className="transaction-list-simple">
        {listItems.map((item, index) => (
          <li key={index}>{displayValue(item)}</li>
        ))}
      </ul>
    </div>
  )
}

function DocumentationFindings({ findings }) {
  const normalizedFindings = Array.isArray(findings)
    ? findings.filter((finding) => finding && typeof finding === 'object')
    : []

  if (normalizedFindings.length === 0) return null

  const visibleFindings = normalizedFindings.slice(0, 4)

  return (
    <div className="transaction-section transaction-section--findings">
      <div className="doc-findings-alert-banner">
        <AlertTriangle className="doc-findings-alert-banner__icon" />
        <span>{normalizedFindings.length} anomalie(s) documentée(s) détectée(s)</span>
      </div>
      <h5 className="transaction-section__title">Inconformités documentées</h5>
      <div className="doc-finding-list">
        {visibleFindings.map((finding, index) => (
          <div key={index} className="doc-finding">
            <span className="doc-finding__status-icon" aria-hidden="true">
              <AlertTriangle />
            </span>
            <strong>{displayValue(finding.anomaly ?? finding.title ?? 'Anomaly justification')}</strong>
            {finding.field && <p>Champ: {displayValue(finding.field)}</p>}
            {finding.observed_value && <p>Valeur observee: {displayValue(finding.observed_value)}</p>}
            {finding.expected_condition && <p>Condition attendue: {displayValue(finding.expected_condition)}</p>}
            {finding.context && <p>Contexte: {displayValue(finding.context)}</p>}
            {finding.expected_rule && <p>Regle documentaire: {displayValue(finding.expected_rule)}</p>}
            {finding.explanation && <p>{displayValue(finding.explanation)}</p>}
            {finding.conclusion && <p>{displayValue(finding.conclusion)}</p>}
            {(finding.source || finding.page || finding.paragraph) && (
              <p>
                Source: {displayValue(finding.source ?? 'PDF')}
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
  const references = Array.isArray(items)
    ? items.filter((item) => item && typeof item === 'object')
    : []

  if (references.length === 0) return null

  return (
    <div className="transaction-section">
      <h5 className="transaction-section__title">{displayValue(title)}</h5>
      <div className="response-references">
        {references.map((ref, index) => (
          <ReferencePill key={index} reference={ref} />
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

function reportTraceName(transactionGroups = []) {
  if (transactionGroups.length === 1) {
    return transactionGroups[0].source || 'trace'
  }

  if (transactionGroups.length > 1) {
    return `${transactionGroups.length} traces`
  }

  return 'trace'
}

function buildPrintableReport(data, displayOptions = getDisplayOptions(data)) {
  const statistics = data.statistics ?? {}
  const totalTransactions = statistics.total_transactions ?? data.transactions?.length ?? 0
  const failedTransactions = statistics.failed_transactions ?? 0
  const noResponseTransactions = statistics.no_response_transactions ?? 0
  const alertTransactions = statistics.alert_transactions ?? statistics.warning_transactions ?? 0
  const successTransactions = statistics.successful_transactions ?? 0
  const dashboardHtml = `
    <section class="report-summary">
      <h2>Synthese de l'analyse</h2>
      <p>
        ${escapeHtml(totalTransactions)} transaction(s) analysee(s),
        ${escapeHtml(successTransactions)} succes,
        ${escapeHtml(failedTransactions)} echec(s),
        ${escapeHtml(noResponseTransactions)} sans retour,
        ${escapeHtml(alertTransactions)} alerte(s) detectee(s).
      </p>
      <table class="summary-table">
        <thead>
          <tr>
            <th>Indicateur</th>
            <th>Nombre</th>
            <th>Lecture</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Transactions</td>
            <td>${escapeHtml(totalTransactions)}</td>
            <td>Nombre total de transactions conservees dans le rapport.</td>
          </tr>
          <tr>
            <td>Echecs</td>
            <td>${escapeHtml(failedTransactions)}</td>
            <td>Transactions marquees en echec par l'analyse deterministe.</td>
          </tr>
          <tr>
            <td>Sans retour</td>
            <td>${escapeHtml(noResponseTransactions)}</td>
            <td>Transactions pour lesquelles aucun retour exploitable n'a ete rattache.</td>
          </tr>
          <tr>
            <td>Alertes detectees</td>
            <td>${escapeHtml(alertTransactions)}</td>
            <td>Transactions ou controles necessitant une attention particuliere.</td>
          </tr>
        </tbody>
      </table>
    </section>
  `
  const transactionGroups = groupTransactionsBySource(data.transactions ?? [], { limit: false })
  const traceName = reportTraceName(transactionGroups)
  const reportTitle = `Rapport d'analyse de la trace ${traceName}`
  const logoUrl = new URL(hpsLogo, window.location.origin).href
  const transactionHtml = transactionGroups.map((group, groupIndex) => {
    const groupTransactionsHtml = group.transactions.map((transaction) => {
    const fields = transaction.fields ?? {}
    const transactionStatus = String(transaction.status ?? 'UNKNOWN').toLowerCase()
    const hsmRows = (transaction.hsm_analysis?.commands ?? []).map((command) => `
      <tr>
        <td>${escapeHtml(command.thread ?? transaction.hsm_analysis?.thread ?? 'N/A')}</td>
        <td>${escapeHtml(command.command ? `command_${command.command}` : 'N/A')}</td>
        <td>${escapeHtml(command.response_command ?? 'N/A')}</td>
        <td>${escapeHtml(command.hsm_result_code ?? 'N/A')}</td>
        <td>${escapeHtml(command.return_code ?? 'N/A')}</td>
        <td><span class="status status--${escapeHtml(String(command.status ?? 'unknown').toLowerCase())}">${escapeHtml(command.status ?? 'UNKNOWN')}</span></td>
      </tr>
      <tr>
        <td colspan="6" class="hsm-message">
          <b>TO HSM:</b> ${escapeHtml(command.request_message ?? 'N/A')}<br>
          <b>Thread:</b> ${escapeHtml(command.thread ?? transaction.hsm_analysis?.thread ?? 'N/A')}<br>
          <b>Description:</b> ${escapeHtml(hsmDescription(command))}
        </td>
      </tr>
    `).join('')
    const storyRows = (transaction.log_story ?? []).map((item) => {
      const errorType = functionErrorType(item)
      const errorDetails = functionErrorDetails(item)

      return `
        <tr>
          <td>${escapeHtml(item.order)}</td>
          <td>${escapeHtml(item.function_name)}</td>
          <td><span class="status status--${escapeHtml(String(item.status ?? 'unknown').toLowerCase())}">${escapeHtml(item.status)}</span></td>
          <td class="${errorType ? 'error-type' : ''}">${escapeHtml(errorType || '-')}</td>
        </tr>
        ${errorDetails ? `
          <tr>
            <td></td>
            <td colspan="3" class="error-detail">${escapeHtml(errorDetails)}</td>
          </tr>
        ` : ''}
      `
    }).join('')
    const facts = (transaction.observed_facts ?? []).map((item) => (
      `<li>${escapeHtml(item)}</li>`
    )).join('')
    const findings = (transaction.documentation_findings ?? []).slice(0, 4).map((item) => (
      `<li><strong>${escapeHtml(item.anomaly ?? 'Anomaly justification')}</strong>${item.observed_value ? `<br>Observed: ${escapeHtml(item.observed_value)}` : ''}${item.expected_rule ? `<br>Expected rule: ${escapeHtml(item.expected_rule)}` : ''}${item.source ? `<br>Source: ${escapeHtml(item.source)}${item.page ? ` - p.${escapeHtml(item.page)}` : ''}` : ''}</li>`
    )).join('')

    return `
      <section class="transaction transaction--${escapeHtml(transactionStatus)}">
        <header>
          <div>
            <h2>FLD 037 ${escapeHtml(fields['037'] ?? 'N/A')}</h2>
            <p>Code MTI : ${escapeHtml(transaction.mti ?? 'UNKNOWN')}</p>
          </div>
          <span class="status status--${escapeHtml(transactionStatus)}">${escapeHtml(transaction.status ?? 'UNKNOWN')}</span>
        </header>
        <div class="fields">
          <div><b>MTI</b>${escapeHtml(transaction.mti ?? 'UNKNOWN')}</div>
          <div><b>FLD 002</b>${escapeHtml(fields['002'] ?? 'N/A')}</div>
          <div><b>FLD 003</b>${escapeHtml(fields['003'] ?? 'N/A')}</div>
          <div><b>FLD 037</b>${escapeHtml(fields['037'] ?? 'N/A')}</div>
          <div><b>FLD 039</b>${escapeHtml(fields['039'] ?? 'N/A')}</div>
        </div>
        ${displayOptions.show_log_story ? `
          <h3>Fonctions</h3>
          <table>
            <thead><tr><th>#</th><th>Function</th><th>Status</th><th>Type d'erreur</th></tr></thead>
            <tbody>${storyRows}</tbody>
          </table>
        ` : ''}
        ${displayOptions.show_hsm && hsmRows ? `
          <h3>HSM Analysis</h3>
          <table>
            <thead><tr><th>Thread</th><th>Command</th><th>Response</th><th>HsmResultCode</th><th>Return Code</th><th>Status</th></tr></thead>
            <tbody>${hsmRows}</tbody>
          </table>
        ` : ''}
        ${facts ? `<h3>Observed Facts</h3><ul>${facts}</ul>` : ''}
        ${findings ? `<h3>Documentation Findings</h3><ul>${findings}</ul>` : ''}
      </section>
    `
    }).join('')

    return `
      <section class="trace-report-group">
        ${transactionGroups.length > 1 ? `
          <div class="trace-report-group__header">
            <div>
              <p>Trace ${escapeHtml(groupIndex + 1)}</p>
              <h2>${escapeHtml(group.source)}</h2>
            </div>
            <div class="trace-report-group__stats">
              <span>${escapeHtml(group.transactions.length)} transaction(s)</span>
              <span>${escapeHtml(countTransactionsByStatus(group.transactions, 'FAILED'))} echec(s)</span>
              <span>${escapeHtml(countTransactionsWithoutResponse(group.transactions))} sans retour</span>
            </div>
          </div>
        ` : ''}
        ${groupTransactionsHtml}
      </section>
    `
  }).join('')

  return `
    <!doctype html>
    <html>
      <head>
        <title>${escapeHtml(reportTitle)}</title>
        <style>
          * { box-sizing: border-box; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
          html, body { min-height: 100%; }
          body { margin: 0; color: #172033; background: #ffffff; font-family: Arial, sans-serif; font-size: 12px; line-height: 1.45; }
          .report-shell { max-width: 980px; margin: 0 auto; }
          .report-header { margin-bottom: 18px; padding: 0 0 18px; border-bottom: 2px solid #d9e0ea; display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
          .report-brand { display: flex; align-items: center; gap: 16px; min-width: 0; }
          .report-logo { width: 118px; height: auto; object-fit: contain; }
          .report-title-block { min-width: 0; }
          .report-kicker { margin: 0 0 5px; color: #536176; font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
          .report-date { margin: 4px 0 0; color: #667085; font-size: 11px; text-align: right; white-space: nowrap; }
          h1 { margin: 0; color: #101828; font-size: 22px; line-height: 1.25; overflow-wrap: anywhere; }
          h2 { margin: 0; color: #101828; font-size: 16px; }
          header p { margin: 4px 0 0; color: #667085; }
          h3 { margin: 16px 0 8px; color: #174a91; font-size: 12px; font-weight: 800; text-transform: uppercase; }
          .report-summary { margin-bottom: 18px; padding: 14px 0 4px; border-bottom: 1px solid #d9e0ea; }
          .report-summary h2 { margin: 0 0 6px; color: #174a91; font-size: 15px; }
          .report-summary p { margin: 0 0 10px; color: #344054; }
          .summary-table { margin-bottom: 8px; }
          .summary-table th { background: #f2f6fb; color: #344054; }
          .summary-table td:nth-child(2) { width: 80px; color: #101828; font-weight: 800; text-align: center; }
          .trace-report-group { margin-bottom: 18px; }
          .trace-report-group__header { margin: 0 0 14px; padding: 10px 0; border-top: 1px solid #d9e0ea; border-bottom: 1px solid #d9e0ea; display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
          .trace-report-group__header p { margin: 0 0 4px; color: #536176; font-size: 10px; font-weight: 900; letter-spacing: .05em; text-transform: uppercase; }
          .trace-report-group__stats { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
          .trace-report-group__stats span { padding: 3px 0 3px 10px; color: #344054; font-size: 10px; font-weight: 800; }
          .transaction { break-inside: auto; page-break-inside: auto; margin: 0 0 18px; padding: 14px 0 4px; border-top: 3px solid #286fcf; background: #ffffff; }
          .transaction--failed { border-left-color: #e31b54; }
          .transaction--warning { border-left-color: #f79009; }
          .transaction--success { border-left-color: #12b76a; }
          header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
          .status { display: inline-flex; align-items: center; justify-content: center; min-width: 78px; padding: 4px 9px; border-radius: 999px; font-size: 10px; font-weight: 900; text-transform: uppercase; }
          .status--success, .status--ok { background: #e7f8ef; color: #027a48; }
          .status--failed, .status--error { background: #fff0f3; color: #c01048; }
          .status--warning { background: #fffaeb; color: #b54708; }
          .status--unknown { background: #eef2f6; color: #475467; }
          .fields { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 10px 0 14px; }
          .fields div { padding: 10px; background: #f8fbff; border: 1px solid #d9e8fb; border-radius: 8px; overflow-wrap: anywhere; }
          .fields b { display: block; margin-bottom: 3px; color: #536176; font-size: 10px; text-transform: uppercase; }
          table { width: 100%; border-collapse: collapse; }
          th, td { padding: 7px 8px; border: 1px solid #dce6f2; text-align: left; vertical-align: top; }
          th { background: #edf5ff; color: #174a91; font-size: 10px; text-transform: uppercase; }
          tr:nth-child(even) td { background: #fbfdff; }
          .hsm-message { background: #f8fbff !important; color: #344054; font-family: Consolas, monospace; overflow-wrap: anywhere; }
          .error-type { color: #c01048; font-weight: 800; }
          .error-detail { background: #fff5f6 !important; color: #7a1f36; font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
          ul { margin: 0; padding-left: 18px; }
          @page { size: A4; margin: 14mm; }
        </style>
      </head>
      <body>
        <main class="report-shell">
          <section class="report-header">
            <div class="report-brand">
              <img class="report-logo" src="${escapeHtml(logoUrl)}" alt="HPS">
              <div class="report-title-block">
                <p class="report-kicker">Rapport d'analyse</p>
                <h1>${escapeHtml(reportTitle)}</h1>
              </div>
            </div>
            <p class="report-date">${escapeHtml(new Date().toLocaleString('fr-FR'))}</p>
          </section>
          ${dashboardHtml}
          ${transactionHtml}
        </main>
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

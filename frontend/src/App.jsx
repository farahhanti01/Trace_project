import React, { useEffect, useRef, useState } from 'react'
import Sidebar from './components/Sidebar'
import Header from './components/Header'
import AgentSelector from './components/AgentSelector'
import FileUpload from './components/UploadArea'
import MessageInput from './components/ChatInput'
import ChatMessage from './components/ChatMessage'

function makeId() {
  return Math.random().toString(36).slice(2, 10)
}

function mockResponse(agent, question) {
  if (agent === 'log') {
    return {
      summary: 'Analyzed 1,284 log entries from the provided authorization trace. Identified a partial reversal path and one non-conformity against the ISO 8583 specification.',
      story: [
        'Terminal 74210 initiated a purchase authorization (MTI 0100) for USD 128.50.',
        'Acquirer switch routed the request to the Visa network with correct BIN mapping.',
        'Issuer approved with response code 00 and returned an authorization code.',
        'Terminal timed out before receiving 0110 response; automatic reversal (0400) triggered.',
      ],
      issues: [
        { severity: 'error', title: 'Missing DE-39 in reversal message', detail: 'The 0400 reversal is missing the original response code from the approved 0110.' },
        { severity: 'warning', title: 'Response latency above 8s threshold', detail: 'Issuer round-trip time reached 9.4s, exceeding the SLA defined in Switch Validation spec §4.2.' },
      ],
      recommendations: ['Backfill DE-39 in reversal generation before retrying the transaction end-to-end.', 'Increase issuer timeout window to 12s or investigate latency at the acquirer link.'],
      references: [{ source: 'ISO8583-1987.pdf', page: 42 }, { source: 'SwitchValidation.docx', page: 12 }, { source: 'auth-trace-2025-11-14.log' }],
    }
  }

  return {
    summary: `Reviewed the uploaded documentation to answer: "${question}". Extracted the relevant specification clauses and cross-referenced the authorization flow requirements.`,
    story: ['Located the relevant chapter in the Visa Core Rules describing cardholder verification.', 'Cross-checked the merchant category code handling against internal HPS specification.', 'Confirmed the conditions under which offline PIN is accepted.'],
    issues: [{ severity: 'info', title: 'Wording ambiguity in section 5.3', detail: 'The specification allows two interpretations for fallback behaviour. Clarify with the issuer.' }],
    recommendations: ['Adopt the stricter interpretation (require online PIN) for HPS test scenarios.', 'Add a dedicated test case for offline PIN fallback on chip-and-PIN terminals.'],
    references: [{ source: 'Visa Specification.pdf', page: 42 }, { source: 'Authorization.docx', page: 7 }, { source: 'Merchant Rules.xlsx' }],
  }
}

export default function App() {
  const SEED_CONVERSATIONS = [
    { id: 'c1', title: 'Visa Authorization' },
    { id: 'c2', title: 'Mastercard Issue' },
    { id: 'c3', title: 'ATM Failure' },
    { id: 'c4', title: 'Switch Validation' },
  ]

  const [conversations, setConversations] = useState(SEED_CONVERSATIONS)
  const [activeId, setActiveId] = useState(null)
  const [messagesByConv, setMessagesByConv] = useState({})
  const [agentByConv, setAgentByConv] = useState({})
  const [filesByConv, setFilesByConv] = useState({})

  const [draftAgent, setDraftAgent] = useState('documentation')
  const [draftFiles, setDraftFiles] = useState([])

  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  const scrollRef = useRef(null)
  const textareaRef = useRef(null)

  const activeMessages = activeId ? messagesByConv[activeId] ?? [] : []
  const activeAgent = activeId ? agentByConv[activeId] ?? 'documentation' : draftAgent
  const activeFiles = activeId ? filesByConv[activeId] ?? [] : draftFiles

  useEffect(() => {
    textareaRef.current?.focus()
  }, [activeId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [activeMessages.length, sending])

  const startNew = () => {
    setActiveId(null)
    setInput('')
    setDraftFiles([])
    setTimeout(() => textareaRef.current?.focus(), 0)
  }

  const setActiveAgent = (id) => {
    if (activeId) {
      setAgentByConv((prev) => ({ ...prev, [activeId]: id }))
    } else {
      setDraftAgent(id)
    }
  }

  const setActiveFiles = (files) => {
    if (activeId) {
      setFilesByConv((prev) => ({ ...prev, [activeId]: files }))
    } else {
      setDraftFiles(files)
    }
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text || sending) return

    let convId = activeId
    if (!convId) {
      convId = makeId()
      const title = text.length > 40 ? text.slice(0, 40) + '…' : text
      const newConv = { id: convId, title }
      setConversations((prev) => [newConv, ...prev])
      setAgentByConv((prev) => ({ ...prev, [convId]: draftAgent }))
      setFilesByConv((prev) => ({ ...prev, [convId]: draftFiles }))
      setActiveId(convId)
    }

    const userMsg = { id: makeId(), role: 'user', content: text }
    setMessagesByConv((prev) => ({
      ...prev,
      [convId]: [...(prev[convId] ?? []), userMsg],
    }))
    setInput('')
    setSending(true)

    const currentAgent = activeId ? agentByConv[activeId] ?? 'documentation' : draftAgent

    window.setTimeout(() => {
      const aiMsg = {
        id: makeId(),
        role: 'assistant',
        content: '',
        structured: mockResponse(currentAgent, text),
      }
      setMessagesByConv((prev) => ({
        ...prev,
        [convId]: [...(prev[convId] ?? []), aiMsg],
      }))
      setSending(false)
      setTimeout(() => textareaRef.current?.focus(), 0)
    }, 900)
  }

  const showEmpty = !activeId

  return (
    <div className="app-shell">
      <Sidebar conversations={conversations} activeId={activeId} onSelect={setActiveId} onNew={startNew} />

      <div className="main-shell">
        <Header />

        <main ref={scrollRef} className="content-area">
          {showEmpty ? (
            <main>
              {/* <section className="welcome-section">
                <div className="hero-icon">★</div>
                <h1 className="hero-title">TRACE AI Platform</h1>
                <p className="hero-subtitle">AI-powered validation of technical documentation and authorization logs.</p>
              </section> */}

              <div className="workspace">
                <AgentSelector value={draftAgent} onChange={setActiveAgent} />
                <FileUpload files={draftFiles} onFilesChange={setActiveFiles} />
              </div>
            </main>
          ) : (
            <section className="conversation-panel">
              {activeMessages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
              {sending && (
                <div className="chat-loading">
                  <div className="chat-loading__avatar">★</div>
                  <div className="chat-loading__bubble">Typing…</div>
                </div>
              )}
            </section>
          )}
        </main>

        <footer className="composer-footer">
          <MessageInput
            ref={textareaRef}
            value={input}
            onChange={setInput}
            onSubmit={handleSend}
            onAttach={(list) => setActiveFiles([...activeFiles, ...Array.from(list)])}
            disabled={sending}
          />
        </footer>
      </div>
    </div>
  )
}


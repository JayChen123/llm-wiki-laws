import { useCallback, useEffect, useState } from 'react'

import { api, streamChat } from '../api'
import Composer from '../components/Composer'
import MessageList from '../components/MessageList'
import SessionList from '../components/SessionList'

/** 法律问答模块：会话列表 + 消息流 + 输入框。
 *
 * 顶栏（品牌、知识库规模、用户与登出）已上移到 AppShell，好让导航条对所有模块生效。
 */
export default function ChatPage({ onLogout }) {
  const [sessions, setSessions] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState(null)   // {text, citations, kind, intent}
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadSessions = useCallback(async () => {
    try {
      const list = await api.listSessions()
      setSessions(list)
      return list
    } catch (err) {
      if (err.status === 401) onLogout()
      else setError(err.message)
      return []
    }
  }, [onLogout])

  const openSession = useCallback(async (sessionId) => {
    setActiveId(sessionId)
    setError('')
    try {
      const history = await api.messages(sessionId)
      setMessages(history.map((m) => ({ role: m.role, content: m.content,
                                         citations: m.citations || [] })))
    } catch (err) {
      setMessages([])
      setError(err.message)
    }
  }, [])

  // 首次进入：拉会话列表；有会话就打开最近一个
  useEffect(() => {
    ;(async () => {
      const list = await loadSessions()
      if (list.length > 0) await openSession(list[0].session_id)
    })()
  }, [loadSessions, openSession])

  async function createSession() {
    setBusy(true)
    try {
      const created = await api.createSession()
      await loadSessions()
      setActiveId(created.session_id)
      setMessages([])
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function deleteSession(sessionId) {
    try {
      await api.deleteSession(sessionId)
      const list = await loadSessions()
      if (sessionId === activeId) {
        if (list.length > 0) await openSession(list[0].session_id)
        else { setActiveId(null); setMessages([]) }
      }
    } catch (err) {
      setError(err.message)
    }
  }

  function ask(question) {
    if (!activeId) return
    setError('')
    setBusy(true)
    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setStreaming({ text: '', citations: [], kind: 'answer', intent: null })

    streamChat(activeId, question, {
      onMeta: (data) =>
        setStreaming((s) => (s ? { ...s, citations: data.citations || [] } : s)),
      onDelta: (text) =>
        setStreaming((s) => (s ? { ...s, text: s.text + text } : s)),
      onFallback: (data) =>
        setStreaming({ text: data.answer || '', citations: [], kind: 'fallback',
                       intent: data.intent }),
      onCitations: (list) =>
        setStreaming((s) => (s ? { ...s, citations: list } : s)),
      onError: (data) => setError(data.message || '生成失败'),
      onDone: () => {
        setStreaming((s) => {
          if (s?.text) {
            // 引用要跟着回答一起落地：否则回答一完成，最该被核对的溯源信息就消失了
            setMessages((prev) => [
              ...prev,
              { role: 'assistant', content: s.text, citations: s.citations || [] },
            ])
          }
          return null
        })
        setBusy(false)
        loadSessions()      // 刷新消息数与标题
      },
    })
  }

  return (
    <div className="flex-1 flex min-h-0">
      <SessionList
        sessions={sessions}
        activeId={activeId}
        onSelect={openSession}
        onCreate={createSession}
        onDelete={deleteSession}
        busy={busy}
      />

      <main className="flex-1 flex flex-col min-w-0">
        {error && (
          <div className="mx-6 mt-4 text-xs text-danger-400 bg-ink-700/60
                          border border-danger-400/30 rounded px-3 py-2">
            {error}
          </div>
        )}
        <MessageList messages={messages} streaming={streaming} querying={busy} />
        <Composer onSend={ask} disabled={!activeId} busy={busy} />
      </main>
    </div>
  )
}

/** 与后端 API 的通信层。
 *
 * 两个约定：
 * 1. 认证靠 HttpOnly Cookie（前端拿不到令牌，也不该拿到）——所以 fetch 一律带 credentials: 'include'。
 * 2. 问答是 **POST** 的 SSE：浏览器原生 EventSource 只支持 GET，故用 fetch + ReadableStream
 *    手动解析 `event:` / `data:` 块。
 */

async function request(path, options = {}) {
  const resp = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (resp.status === 204) return null
  const text = await resp.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!resp.ok) {
    const error = new Error(data?.detail || `请求失败（${resp.status}）`)
    error.status = resp.status
    throw error
  }
  return data
}

export const api = {
  me: () => request('/api/auth/me'),
  register: (username, password, display_name) =>
    request('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password, display_name: display_name || null }),
    }),
  login: (username, password) =>
    request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  listSessions: () => request('/api/sessions'),
  createSession: () => request('/api/sessions', { method: 'POST' }),
  messages: (sessionId) => request(`/api/sessions/${sessionId}/messages`),
  deleteSession: (sessionId) =>
    request(`/api/sessions/${sessionId}`, { method: 'DELETE' }),
}

/**
 * 流式问答：逐个事件回调。
 * handlers: { onMeta, onDelta, onFallback, onCitations, onError, onMemory, onDone }
 */
export async function streamChat(sessionId, question, handlers = {}) {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question }),
  })

  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`
    try {
      detail = (await resp.json()).detail || detail
    } catch { /* 保持默认文案 */ }
    handlers.onError?.({ message: detail })
    handlers.onDone?.()
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  // 按空行切分 SSE 块；最后一段可能不完整，留在 buffer 里等下一片
  const flush = (final = false) => {
    const blocks = buffer.split('\n\n')
    buffer = final ? '' : blocks.pop()
    for (const block of blocks) {
      if (!block.trim()) continue
      let event = null
      let data = {}
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) {
          try {
            data = JSON.parse(line.slice(6))
          } catch {
            data = {}
          }
        }
      }
      dispatch(event, data, handlers)
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    flush()
  }
  buffer += decoder.decode()
  flush(true)
}

function dispatch(event, data, handlers) {
  switch (event) {
    case 'meta':      handlers.onMeta?.(data); break
    case 'delta':     handlers.onDelta?.(data.text || ''); break
    case 'fallback':  handlers.onFallback?.(data); break
    case 'citations': handlers.onCitations?.(data.citations || []); break
    case 'memory':    handlers.onMemory?.(data); break
    case 'error':     handlers.onError?.(data); break
    case 'done':      handlers.onDone?.(); break
    default: break
  }
}

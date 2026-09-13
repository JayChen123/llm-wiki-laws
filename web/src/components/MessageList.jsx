import { useEffect, useRef } from 'react'

import CitationPanel from './CitationPanel'
import MarkdownBody from './MarkdownBody'

/** 消息流：用户提问右对齐、助手回答左对齐（法条正文用衬线体）。 */
export default function MessageList({ messages, streaming, querying }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, streaming])

  return (
    <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
      {messages.length === 0 && !streaming && <EmptyState />}

      {messages.map((m, i) => (
        <Message key={i} message={m} />
      ))}

      {streaming && (
        <Message
          message={{ role: 'assistant', content: streaming.text }}
          pending={querying}
          kind={streaming.kind}
          fallbackIntent={streaming.intent}
          citations={streaming.citations}
        />
      )}

      <div ref={bottomRef} />
    </div>
  )
}

function Message({ message, pending, kind, fallbackIntent, citations }) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[76%] bg-ink-600 border border-line rounded-lg px-4 py-2.5
                        text-sm text-parchment whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    )
  }

  const isFallback = kind === 'fallback'
  const shown = citations ?? message.citations ?? []

  return (
    <div className="flex flex-col items-start">
      <div className="text-[11px] tracking-[0.3em] text-gold-500 mb-2">AI · 法律问答</div>
      <div
        className={
          'max-w-[86%] rounded-lg px-4 py-3 border text-sm ' +
          (isFallback
            ? 'bg-ink-700/70 border-warn-400/30 text-muted'
            : 'bg-ink-800 border-line text-parchment')
        }
      >
        {isFallback && (
          <div className="text-[11px] text-warn-400 mb-2">
            {fallbackIntent === 'legal_uncovered' ? '本知识库未覆盖该法律领域' : '非法律问题'}
          </div>
        )}
        <MarkdownBody className={isFallback ? '' : 'legal-text'}>
          {message.content}
        </MarkdownBody>
        {pending && !message.content && (
          <div className="text-xs text-faint">正在检索知识库…</div>
        )}
      </div>

      {shown.length > 0 && <CitationPanel citations={shown} />}
    </div>
  )
}

function EmptyState() {
  const examples = [
    '试用期最长可以约定多久？',
    '离婚冷静期是多久？',
    '高空抛物砸伤人谁负责？',
    '加班费应该怎么计算？',
  ]
  return (
    <div className="h-full flex items-center justify-center">
      <div className="max-w-lg text-center">
        <div className="text-[11px] tracking-[0.42em] text-gold-500 mb-3">LLM · WIKI</div>
        <h2 className="text-lg font-medium mb-3">每条结论都可溯源到具体法条</h2>
        <div className="gold-rule mx-auto w-16 mb-5" />
        <p className="text-sm text-muted mb-6 leading-relaxed">
          知识库已编译 1465 条法条知识点（民法典 1260 条 + 劳动法 107 条 + 劳动合同法 98 条），
          支持跨法条的多跳推理。
        </p>
        <div className="text-left space-y-2">
          {examples.map((q) => (
            <div key={q} className="text-xs text-faint border border-line/70 rounded px-3 py-2">
              {q}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

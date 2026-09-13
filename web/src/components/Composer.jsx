import { useState } from 'react'

/** 提问输入框：Enter 发送、Shift+Enter 换行；发送中禁用避免重复提交。 */
export default function Composer({ onSend, disabled, busy }) {
  const [text, setText] = useState('')

  function send() {
    const q = text.trim()
    if (!q || disabled || busy) return
    setText('')
    onSend(q)
  }

  return (
    <div className="border-t border-line bg-ink-800 px-6 py-4">
      <div className="flex items-end gap-3 max-w-4xl mx-auto">
        <textarea
          value={text}
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          disabled={disabled}
          placeholder={disabled ? '请先新建会话' : '输入法律问题，Enter 发送 / Shift+Enter 换行'}
          className="flex-1 resize-none bg-ink-700 border border-line rounded-lg px-4 py-3
                     text-sm outline-none focus:border-gold-600 disabled:opacity-60
                     transition-colors max-h-40"
        />
        <button
          onClick={send}
          disabled={disabled || busy || !text.trim()}
          className="bg-gold-500 hover:bg-gold-400 disabled:opacity-40 text-ink-900 text-sm
                     font-medium rounded-lg px-5 py-3 transition-colors shrink-0"
        >
          {busy ? '生成中…' : '提问'}
        </button>
      </div>
    </div>
  )
}

import { useState } from 'react'

/** 引用溯源面板：把回答背后的知识点（含法律名与来源行号）摊开展示。
 *
 * 这是本项目"可审计"卖点的可视化落点——用户能核对答案到底依据了哪几条。
 */
export default function CitationPanel({ citations }) {
  const [open, setOpen] = useState(false)

  if (!citations?.length) return null

  return (
    <div className="mt-2 max-w-[86%] border border-gold-600/40 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 bg-ink-700/50
                   text-xs text-gold-400 hover:bg-ink-700 transition-colors"
      >
        <span>引用来源 · {citations.length} 条法条</span>
        <span className="text-faint">{open ? '收起 ▲' : '展开 ▼'}</span>
      </button>

      {open && (
        <ul className="divide-y divide-line/70 bg-ink-800/60">
          {citations.map((c) => {
            const law = c.source?.includes('.md') ? c.source.split('.md')[0].trim() : ''
            return (
              <li key={c.id} className="px-3 py-2 text-xs">
                <div className="flex items-baseline gap-2">
                  <span className="text-faint shrink-0">{c.id}</span>
                  <span className="legal-text text-parchment">
                    {law && <span className="text-muted">{law} · </span>}
                    {c.title}
                  </span>
                  <span className="ml-auto text-faint shrink-0">{c.score}</span>
                </div>
                {c.source && (
                  <div className="text-[11px] text-faint mt-1">原文位置：{c.source}</div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

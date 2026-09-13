/** 左侧会话列表：切换历史会话、新建、删除。 */
export default function SessionList({ sessions, activeId, onSelect, onCreate, onDelete, busy }) {
  return (
    <aside className="w-64 shrink-0 border-r border-line bg-ink-800 flex flex-col">
      <div className="px-4 py-4 border-b border-line">
        <button
          onClick={onCreate}
          disabled={busy}
          className="w-full text-sm border border-gold-600/60 text-gold-400 hover:bg-ink-700
                     disabled:opacity-50 rounded py-2 transition-colors"
        >
          ＋ 新建会话
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto py-2">
        {sessions.length === 0 && (
          <p className="px-4 py-3 text-xs text-faint">暂无会话，点上方新建开始提问</p>
        )}
        {sessions.map((s) => {
          const active = s.session_id === activeId
          return (
            <div
              key={s.session_id}
              className={
                'group flex items-start gap-2 px-4 py-2.5 cursor-pointer border-l-2 ' +
                (active
                  ? 'border-gold-500 bg-ink-700'
                  : 'border-transparent hover:bg-ink-700/60')
              }
              onClick={() => onSelect(s.session_id)}
            >
              <div className="min-w-0 flex-1">
                <div className={'text-sm truncate ' + (active ? 'text-parchment' : 'text-muted')}>
                  {s.title || '新会话'}
                </div>
                <div className="text-[11px] text-faint mt-0.5">{s.message_count} 条消息</div>
              </div>
              <button
                title="删除会话"
                onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
                className="opacity-0 group-hover:opacity-100 text-faint hover:text-danger-400
                           text-xs px-1 transition-opacity"
              >
                ✕
              </button>
            </div>
          )
        })}
      </nav>
    </aside>
  )
}

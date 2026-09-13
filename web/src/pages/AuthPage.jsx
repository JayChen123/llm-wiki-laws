import { useState } from 'react'
import { api } from '../api'

/** 登录 / 注册（同页切换）。注册即登录，成功后直接进问答。 */
export default function AuthPage({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const isRegister = mode === 'register'

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const user = isRegister
        ? await api.register(username.trim(), password)
        : await api.login(username.trim(), password)
      onAuthenticated(user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-md">
        <header className="mb-8">
          <div className="text-[11px] tracking-[0.42em] text-gold-500 mb-3">LLM · WIKI</div>
          <h1 className="text-2xl font-semibold tracking-wide">法律智能问答</h1>
          <div className="gold-rule mt-4 w-24" />
          <p className="mt-4 text-sm text-muted leading-relaxed">
            基于结构化知识点与关联图谱作答，每条结论均可溯源到具体法条
          </p>
        </header>

        <form
          onSubmit={submit}
          className="bg-ink-800 border border-line rounded-lg p-6 space-y-5"
        >
          <div className="flex gap-6 border-b border-line pb-3 text-sm">
            {[
              ['login', '登录'],
              ['register', '注册'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => { setMode(key); setError('') }}
                className={
                  'pb-2 -mb-[13px] transition-colors ' +
                  (mode === key
                    ? 'text-gold-400 border-b border-gold-500'
                    : 'text-faint hover:text-muted')
                }
              >
                {label}
              </button>
            ))}
          </div>

          <label className="block">
            <span className="text-xs text-muted">用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              className="mt-1.5 w-full bg-ink-700 border border-line rounded px-3 py-2.5 text-sm
                         outline-none focus:border-gold-600 transition-colors"
              placeholder={isRegister ? '至少 3 位，不含空格' : ''}
            />
          </label>

          <label className="block">
            <span className="text-xs text-muted">密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isRegister ? 'new-password' : 'current-password'}
              required
              className="mt-1.5 w-full bg-ink-700 border border-line rounded px-3 py-2.5 text-sm
                         outline-none focus:border-gold-600 transition-colors"
              placeholder={isRegister ? '至少 8 位' : ''}
            />
          </label>

          {error && (
            <div className="text-sm text-danger-400 bg-ink-700/60 border border-danger-400/30
                            rounded px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full bg-gold-500 hover:bg-gold-400 disabled:opacity-50 text-ink-900
                       font-medium text-sm rounded py-2.5 transition-colors"
          >
            {busy ? '请稍候…' : isRegister ? '注册并进入' : '登录'}
          </button>
        </form>

        <p className="mt-5 text-xs text-faint leading-relaxed">
          知识库覆盖《中华人民共和国民法典》《中华人民共和国劳动法》《中华人民共和国劳动合同法》
        </p>
      </div>
    </div>
  )
}

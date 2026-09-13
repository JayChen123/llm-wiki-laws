import { useEffect, useState } from 'react'

import { api } from './api'
import AppShell from './components/AppShell'
import AuthPage from './pages/AuthPage'

/** 应用外壳：进站先问 /api/auth/me 判断登录态（令牌在 HttpOnly Cookie 里，前端读不到）。 */
export default function App() {
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    api.me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecking(false))
  }, [])

  if (checking) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-faint">
        正在加载…
      </div>
    )
  }

  return user
    ? <AppShell user={user} onLogout={() => setUser(null)} />
    : <AuthPage onAuthenticated={setUser} />
}

import { useEffect, useState } from 'react'
import { api, type Health } from './api/client'

type Load = { state: 'loading' } | { state: 'ready'; health: Health } | { state: 'error' }

/**
 * Placeholder shell. The two-pane reading UI replaces this in M1; for now it
 * proves the SPA is wired to the API.
 */
export default function App() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  useEffect(() => {
    let live = true
    api
      .health()
      .then((health) => live && setLoad({ state: 'ready', health }))
      .catch(() => live && setLoad({ state: 'error' }))
    return () => {
      live = false
    }
  }, [])

  return (
    <main className="app">
      <h1>Feedelio</h1>
      {load.state === 'loading' && <p role="status">Connecting…</p>}
      {load.state === 'error' && <p role="alert">Cannot reach the API.</p>}
      {load.state === 'ready' && (
        <dl className="status">
          <dt>Version</dt>
          <dd>{load.health.version}</dd>
          <dt>Feeds</dt>
          <dd>{load.health.feeds}</dd>
          <dt>Unread</dt>
          <dd>{load.health.unread}</dd>
        </dl>
      )}
    </main>
  )
}

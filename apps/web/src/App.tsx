import {
  Braces,
  Check,
  ChevronRight,
  Clock3,
  Code2,
  Database,
  History,
  Layers3,
  Link2,
  Plus,
  Search,
  Send,
  ShieldCheck,
  Square,
  Table2,
  X,
} from 'lucide-react'
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'

type RunStatus = 'idle' | 'running' | 'completed' | 'failed'

type RunEvent = {
  run_id: string
  seq?: number
  type: string
  payload: Record<string, unknown>
  occurred_at?: string
}

type HistoryItem = {
  id: string
  sourceId: string
  question: string
  status: RunStatus
  elapsedMs: number
  events: RunEvent[]
}

type SourceInfo = {
  source_id: string
  url: string
  database: string
  release_id: string
  collection_count: number
  field_count: number
  graph_count: number
  status: 'ready'
}

type SourceForm = {
  source_id: string
  url: string
  database: string
  username: string
  password: string
  sample_size: number
}

const EMPTY_SOURCE_FORM: SourceForm = {
  source_id: '',
  url: 'http://host.docker.internal:8529',
  database: '',
  username: '',
  password: '',
  sample_size: 50,
}

const GRAPH_STEPS = [
  { type: 'context_retrieved', label: '语义召回', icon: Search },
  { type: 'plan_created', label: '查询计划', icon: Layers3 },
  { type: 'query_compiled', label: '编译 AQL', icon: Code2 },
  { type: 'query_prepared', label: '安全检查', icon: ShieldCheck },
  { type: 'query_executed', label: '执行查询', icon: Database },
  { type: 'completed', label: '生成回答', icon: Check },
]

const SUGGESTIONS = [
  '查询已支付订单，返回订单编号、客户编号和实付金额',
  '按客户地区统计已支付订单成交额',
  '查询累计消费最高的前 10 名客户',
  '查询浏览过但没有购买 product-010 的客户',
]

function eventOf(events: RunEvent[], type: string) {
  return events.find((event) => event.type === type)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

async function streamQuery(
  sourceId: string,
  question: string,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
) {
  const response = await fetch('/api/v1/runs:stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId, question }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`请求失败 (${response.status})`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const dataLine = block.split('\n').find((line) => line.startsWith('data: '))
      if (dataLine) onEvent(JSON.parse(dataLine.slice(6)) as RunEvent)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}

async function fetchSources(): Promise<SourceInfo[]> {
  const response = await fetch('/api/v1/sources')
  if (!response.ok) throw new Error(`数据源加载失败 (${response.status})`)
  return response.json() as Promise<SourceInfo[]>
}

async function onboardSource(form: SourceForm): Promise<SourceInfo> {
  const response = await fetch('/api/v1/sources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(payload?.detail ?? `数据源接入失败 (${response.status})`)
  }
  return response.json() as Promise<SourceInfo>
}

export default function App() {
  const [question, setQuestion] = useState(SUGGESTIONS[0])
  const [events, setEvents] = useState<RunEvent[]>([])
  const [status, setStatus] = useState<RunStatus>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [detailTab, setDetailTab] = useState<'aql' | 'evidence'>('aql')
  const [serviceHealthy, setServiceHealthy] = useState<boolean | null>(null)
  const [sources, setSources] = useState<SourceInfo[]>([])
  const [sourceId, setSourceId] = useState('commerce')
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [sourceForm, setSourceForm] = useState<SourceForm>(EMPTY_SOURCE_FORM)
  const [sourceSubmitting, setSourceSubmitting] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetch('/health/live')
      .then((response) => setServiceHealthy(response.ok))
      .catch(() => setServiceHealthy(false))
    fetchSources()
      .then(setSources)
      .catch(() => setServiceHealthy(false))
    return () => abortRef.current?.abort()
  }, [])

  const contextEvent = eventOf(events, 'context_retrieved')
  const planEvent = eventOf(events, 'plan_created')
  const compiledEvent = eventOf(events, 'query_compiled')
  const preparedEvent = eventOf(events, 'query_prepared')
  const executedEvent = eventOf(events, 'query_executed')
  const completedEvent = eventOf(events, 'completed')
  const failedEvent = eventOf(events, 'failed')
  const displayedPlan = (compiledEvent?.payload.plan as Record<string, unknown> | undefined)
    ?? planEvent?.payload
  const rows = (executedEvent?.payload.rows as Record<string, unknown>[] | undefined) ?? []
  const columns = useMemo(
    () => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))),
    [rows],
  )
  const answer = completedEvent?.payload.answer as string | undefined
  const rowCount = (executedEvent?.payload.row_count as number | undefined) ?? 0
  const selectedSource = sources.find((source) => source.source_id === sourceId)
  const release = (contextEvent?.payload.release_id as string | undefined)
    ?? selectedSource?.release_id
    ?? '—'

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const nextQuestion = question.trim()
    if (!nextQuestion || status === 'running') return

    const controller = new AbortController()
    abortRef.current = controller
    const started = performance.now()
    const nextEvents: RunEvent[] = []
    setEvents([])
    setElapsedMs(0)
    setStatus('running')
    try {
      await streamQuery(sourceId, nextQuestion, controller.signal, (runEvent) => {
        nextEvents.push(runEvent)
        setEvents([...nextEvents])
      })
      const terminalStatus: RunStatus = nextEvents.at(-1)?.type === 'completed' ? 'completed' : 'failed'
      const duration = performance.now() - started
      setElapsedMs(duration)
      setStatus(terminalStatus)
      setHistory((items) => [
        {
          id: nextEvents[0]?.run_id ?? crypto.randomUUID(),
          sourceId,
          question: nextQuestion,
          status: terminalStatus,
          elapsedMs: duration,
          events: [...nextEvents],
        },
        ...items.filter((item) => item.question !== nextQuestion),
      ].slice(0, 8))
    } catch (error) {
      if ((error as Error).name === 'AbortError') {
        setStatus('idle')
        return
      }
      const duration = performance.now() - started
      const failure: RunEvent = {
        run_id: crypto.randomUUID(),
        type: 'failed',
        payload: { code: 'network_error', message: (error as Error).message },
      }
      setEvents([...nextEvents, failure])
      setElapsedMs(duration)
      setStatus('failed')
    } finally {
      abortRef.current = null
    }
  }

  function stop() {
    abortRef.current?.abort()
  }

  function newQuery() {
    abortRef.current?.abort()
    setQuestion('')
    setEvents([])
    setElapsedMs(0)
    setStatus('idle')
  }

  function restore(item: HistoryItem) {
    setSourceId(item.sourceId)
    setQuestion(item.question)
    setEvents(item.events)
    setElapsedMs(item.elapsedMs)
    setStatus(item.status)
  }

  async function connectSource(event: FormEvent) {
    event.preventDefault()
    if (sourceSubmitting) return
    setSourceSubmitting(true)
    setSourceError(null)
    try {
      const source = await onboardSource(sourceForm)
      setSources((items) => [source, ...items.filter((item) => item.source_id !== source.source_id)]
        .sort((left, right) => left.source_id.localeCompare(right.source_id)))
      setSourceId(source.source_id)
      setSourceForm(EMPTY_SOURCE_FORM)
      setSourceDialogOpen(false)
      newQuery()
    } catch (error) {
      setSourceError((error as Error).message)
    } finally {
      setSourceSubmitting(false)
    }
  }

  function updateSourceForm<K extends keyof SourceForm>(key: K, value: SourceForm[K]) {
    setSourceForm((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark"><Braces size={18} /></div>
          <strong>QueryPilot</strong>
          <span className="source-label">NL2AQL Workbench</span>
        </div>
        <div className="topbar-actions">
          <label className="source-picker">
            <Database size={14} />
            <select
              aria-label="当前数据源"
              value={sourceId}
              disabled={status === 'running'}
              onChange={(event) => {
                setSourceId(event.target.value)
                newQuery()
              }}
            >
              {sources.map((source) => (
                <option key={source.source_id} value={source.source_id}>{source.source_id}</option>
              ))}
            </select>
          </label>
          <button
            className="connect-source-button"
            type="button"
            onClick={() => {
              setSourceError(null)
              setSourceDialogOpen(true)
            }}
          >
            <Link2 size={14} /> 接入数据源
          </button>
          <div className={`service-state ${serviceHealthy === false ? 'is-down' : ''}`}>
            <span className="service-dot" />
            <span>{serviceHealthy === null ? '正在检查' : serviceHealthy ? '服务正常' : '服务异常'}</span>
            <span className="release-label">{release}</span>
          </div>
        </div>
      </header>

      <div className="workbench-grid">
        <aside className="history-rail">
          <button className="new-query-button" type="button" onClick={newQuery}>
            <Plus size={16} /> 新建查询
          </button>
          <div className="history-heading"><History size={14} /> 本次会话</div>
          <div className="history-list">
            {history.length === 0 && <p className="muted compact">暂无查询</p>}
            {history.map((item) => (
              <button className="history-item" type="button" key={item.id} onClick={() => restore(item)}>
                <span>{item.question}</span>
                <small>{item.sourceId} · {item.status === 'completed' ? `${Math.round(item.elapsedMs)} ms` : '未完成'}</small>
              </button>
            ))}
          </div>
          <div className="data-snapshot">
            <span>DATA SOURCE</span>
            <dl>
              <div><dt>集合</dt><dd>{selectedSource?.collection_count ?? '—'}</dd></div>
              <div><dt>字段</dt><dd>{selectedSource?.field_count ?? '—'}</dd></div>
              <div><dt>图</dt><dd>{selectedSource?.graph_count ?? '—'}</dd></div>
              <div><dt>数据库</dt><dd className="database-name">{selectedSource?.database ?? '—'}</dd></div>
            </dl>
          </div>
        </aside>

        <main className="query-workspace">
          <form className="query-form" onSubmit={submit}>
            <label htmlFor="question">向 {sourceId} 数据提问</label>
            <div className="query-control">
              <textarea
                id="question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void submit()
                  }
                }}
                rows={2}
                placeholder="输入业务问题"
              />
              {status === 'running' ? (
                <button className="run-button stop-button" type="button" onClick={stop} title="停止查询">
                  <Square size={17} fill="currentColor" />
                </button>
              ) : (
                <button className="run-button" type="submit" disabled={!question.trim()} title="运行查询">
                  <Send size={18} />
                </button>
              )}
            </div>
            {sourceId === 'commerce' && (
              <div className="suggestion-row">
                {SUGGESTIONS.map((suggestion) => (
                  <button type="button" key={suggestion} onClick={() => setQuestion(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </form>

          {status === 'idle' && events.length === 0 ? (
            <section className="empty-state">
              <div className="empty-icon"><Database size={22} /></div>
              <h1>{sourceId} 数据源已就绪</h1>
              <div className="empty-metrics">
                <span><b>{selectedSource?.collection_count ?? '—'}</b> 集合</span>
                <span><b>{selectedSource?.field_count ?? '—'}</b> 字段</span>
                <span><b>{selectedSource?.graph_count ?? '—'}</b> 图</span>
              </div>
            </section>
          ) : (
            <>
              <section className={`run-summary ${status}`} aria-live="polite">
                <div className="summary-meta">
                  {status === 'running' && <span className="spinner" />}
                  {status === 'completed' && <Check size={15} />}
                  {status === 'failed' && <X size={15} />}
                  <span>{status === 'running' ? '正在执行' : status === 'completed' ? '执行完成' : '执行失败'}</span>
                  {elapsedMs > 0 && <span>{(elapsedMs / 1000).toFixed(2)}s</span>}
                  {executedEvent && <span>{rowCount} rows</span>}
                </div>
                {answer && <h1>{answer}</h1>}
                {failedEvent && (
                  <div className="failure-message">
                    <b>{String(failedEvent.payload.code ?? 'failed')}</b>
                    <span>{String(failedEvent.payload.message ?? '查询未完成')}</span>
                  </div>
                )}
              </section>

              <section className="mobile-trace" aria-label="执行进度">
                {GRAPH_STEPS.map((step) => {
                  const reached = Boolean(eventOf(events, step.type))
                  return <span key={step.type} className={reached ? 'reached' : ''}>{step.label}</span>
                })}
              </section>

              <section className="result-section">
                <div className="section-heading">
                  <div><Table2 size={16} /><h2>查询结果</h2></div>
                  {rows.length > 0 && <span>显示 {rows.length} 行</span>}
                </div>
                <div className="table-wrap">
                  {rows.length > 0 ? (
                    <table>
                      <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
                      <tbody>
                        {rows.map((row, index) => (
                          <tr key={index}>{columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="no-rows">{status === 'running' ? '等待查询结果' : '结果为空'}</div>
                  )}
                </div>
              </section>
            </>
          )}
        </main>

        <aside className="inspection-rail">
          <section className="trace-section">
            <div className="rail-title"><Clock3 size={15} /> LANGGRAPH 轨迹</div>
            <ol className="trace-list">
              {GRAPH_STEPS.map((step, index) => {
                const runEvent = eventOf(events, step.type)
                const Icon = step.icon
                return (
                  <li key={step.type} className={runEvent ? 'complete' : status === 'running' && events.length === index ? 'active' : ''}>
                    <span className="trace-icon"><Icon size={14} /></span>
                    <div><b>{step.label}</b><small>{runEvent ? '完成' : status === 'running' && events.length === index ? '进行中' : '等待'}</small></div>
                  </li>
                )
              })}
            </ol>
          </section>

          <section className="detail-section">
            <div className="detail-tabs" role="tablist">
              <button className={detailTab === 'aql' ? 'active' : ''} type="button" onClick={() => setDetailTab('aql')}>
                AQL
              </button>
              <button className={detailTab === 'evidence' ? 'active' : ''} type="button" onClick={() => setDetailTab('evidence')}>
                召回证据
              </button>
            </div>
            {detailTab === 'aql' ? (
              <div className="code-area">
                <pre>{String(compiledEvent?.payload.query ?? '暂无 AQL')}</pre>
                {preparedEvent && (
                  <div className="policy-line"><ShieldCheck size={14} /> 只读检查通过 · cost {String(preparedEvent.payload.estimated_cost ?? 0)}</div>
                )}
              </div>
            ) : (
              <div className="evidence-list">
                {((contextEvent?.payload.evidence_ids as string[] | undefined) ?? []).map((id) => (
                  <div key={id}><ChevronRight size={13} /><span>{id}</span></div>
                ))}
                {!contextEvent && <p className="muted compact">暂无召回证据</p>}
              </div>
            )}
          </section>

          {displayedPlan && (
            <section className="plan-section">
              <div className="rail-title"><Layers3 size={15} /> 查询计划</div>
              <dl>
                <div><dt>意图</dt><dd>{String(displayedPlan.intent)}</dd></div>
                <div><dt>根集合</dt><dd>{String(displayedPlan.root_collection)}</dd></div>
                <div><dt>关联边</dt><dd>{((displayedPlan.joins as string[] | undefined) ?? []).join(', ') || '无'}</dd></div>
                <div><dt>输出</dt><dd>{((displayedPlan.outputs as string[] | undefined) ?? []).join(', ')}</dd></div>
              </dl>
            </section>
          )}
        </aside>
      </div>

      {sourceDialogOpen && (
        <div className="dialog-backdrop" role="presentation" onMouseDown={() => !sourceSubmitting && setSourceDialogOpen(false)}>
          <section
            className="source-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="source-dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span className="dialog-icon"><Database size={17} /></span>
                <h2 id="source-dialog-title">接入 ArangoDB</h2>
              </div>
              <button
                className="icon-button"
                type="button"
                title="关闭"
                disabled={sourceSubmitting}
                onClick={() => setSourceDialogOpen(false)}
              >
                <X size={17} />
              </button>
            </header>
            <form onSubmit={connectSource}>
              <div className="source-form-grid">
                <label>
                  <span>数据源 ID</span>
                  <input
                    required
                    pattern="[A-Za-z0-9][A-Za-z0-9_-]*"
                    maxLength={128}
                    value={sourceForm.source_id}
                    onChange={(event) => updateSourceForm('source_id', event.target.value)}
                    placeholder="analytics"
                  />
                </label>
                <label>
                  <span>数据库</span>
                  <input
                    required
                    maxLength={128}
                    value={sourceForm.database}
                    onChange={(event) => updateSourceForm('database', event.target.value)}
                    placeholder="business"
                  />
                </label>
                <label className="span-two">
                  <span>ArangoDB 地址</span>
                  <input
                    required
                    type="url"
                    value={sourceForm.url}
                    onChange={(event) => updateSourceForm('url', event.target.value)}
                  />
                </label>
                <label>
                  <span>只读用户名</span>
                  <input
                    required
                    autoComplete="username"
                    value={sourceForm.username}
                    onChange={(event) => updateSourceForm('username', event.target.value)}
                  />
                </label>
                <label>
                  <span>密码</span>
                  <input
                    required
                    type="password"
                    autoComplete="current-password"
                    value={sourceForm.password}
                    onChange={(event) => updateSourceForm('password', event.target.value)}
                  />
                </label>
                <label>
                  <span>每个集合采样</span>
                  <input
                    required
                    type="number"
                    min={1}
                    max={200}
                    value={sourceForm.sample_size}
                    onChange={(event) => updateSourceForm('sample_size', Number(event.target.value))}
                  />
                </label>
              </div>
              {sourceError && <div className="source-error">{sourceError}</div>}
              <footer>
                <button type="button" disabled={sourceSubmitting} onClick={() => setSourceDialogOpen(false)}>取消</button>
                <button className="primary-action" type="submit" disabled={sourceSubmitting}>
                  {sourceSubmitting ? <span className="spinner" /> : <Link2 size={15} />}
                  {sourceSubmitting ? '正在扫描' : '连接并扫描'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      )}
    </div>
  )
}

// Complete public snapshot shapes for browser journeys that don't need fleet data.
export const emptyOverview = () => ({
  observed_at: Date.now() / 1000, paused: false, tasks_pending: 0,
  execution: { working: 0, starting: 0, disconnected: 0 },
  projects: [], live_tasks: [], subscriptions: [], recent_deliveries: { count: 0, items: [] },
  capacity: { machines: [], machines_total: 0, machines_online: 0, agents_total: 0, agents_ready: 0,
    runners_total: 0, runners_online: 0, runners_busy: 0, runners_idle: 0, runners_usable: 0 },
  attention: { count: 0, offers: [], questions: [], human_todos: [] },
  totals: { tasks_running: 0, agents_ready: 0, agents_total: 0, machines_online: 0, machines_total: 0,
    needs_you: 0, spend_today: 0, budget_today: 0 },
});

export const emptyInbox = () => ({
  items: [], total: 0, offset: 0, limit: 1, observed_at: Date.now() / 1000,
  counts: { all: 0, question: 0, todo: 0, approval: 0, blocked: 0, yours: 0, others: 0 },
});

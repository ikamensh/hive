# Hive redesign — visual and interaction QA

final result: passed

Reviewed 2026-09-07. The selected direction is the dark left navigation panel
with a light operations workspace. This report covers the implementation in
the repository, viewed locally with explicit sample data; it is not a deployment report.

## Evidence and normalization

- Source visual truth: [selected reference](wiki/assets/ui-redesign/reference.png).
- Implementation: [final Overview](wiki/assets/ui-redesign/overview-final.png),
  rendered at `http://127.0.0.1:5178/` in the Codex in-app browser.
- Both source and final capture are **1487 × 1058 pixels**. Browser CSS viewport:
  **1487 × 1058**, `devicePixelRatio: 1`; no density resampling or browser chrome.
- State: authenticated workspace, unpaused, four machines, three online, two busy,
  one idle, three recent receipts, two compact provider readings. Names, task
  descriptions, quota observations, spend and Inbox count differ from the
  illustrative reference because the preview uses the application's coherent
  mock API. These differences are not treated as pixel errors.
- The source and final full captures were opened together in one comparison
  input. [Source work region](wiki/assets/ui-redesign/reference-work-region.png)
  and [implemented work region](wiki/assets/ui-redesign/overview-final-work-region.png)
  were also opened together at the same 1250 × 400 crop to inspect text,
  table alignment, icon sharpness and state labels.
- Mobile evidence: [Overview](wiki/assets/ui-redesign/overview-mobile.png)
  and [new work](wiki/assets/ui-redesign/new-work-mobile.png), 390 × 844 CSS/pixels.
- Additional edge states: [short-window account controls](wiki/assets/ui-redesign/sidebar-short-window.png)
  and [project-filtered Inbox](wiki/assets/ui-redesign/project-filtered-inbox-mobile.png).
  The filtered Inbox capture uses the browser test fixture; its old timestamps
  are fixture content rather than a live workspace observation.
- Additional inspected screens: [Projects](wiki/assets/ui-redesign/projects-desktop.png),
  [Inbox](wiki/assets/ui-redesign/inbox-desktop.png),
  [Capacity](wiki/assets/ui-redesign/capacity-desktop.png),
  [provider limits](wiki/assets/ui-redesign/limits-desktop.png),
  [new work](wiki/assets/ui-redesign/new-work-desktop.png), and
  [selected result evidence](wiki/assets/ui-redesign/result-evidence.png).

## Findings and comparison history

No actionable P0/P1/P2 visual findings remain.

1. **P2, overview density — fixed.** The first desktop capture
   (`/tmp/hive-ui-final/overview-desktop.png`, 1440 × 1024) showed four quota
   windows and excess table padding, putting the attention strip below the
   first screen. Compact quota readings now show one explicitly named window
   per provider, with all windows reachable through View all. Reduced row and
   header spacing, and removed the duplicate limits link. The final capture
   shows all primary regions and the attention strip in the reference viewport.
2. **P2, operational typography — fixed.** The first comparison made capacity
   and task content too small relative to the reference. Capacity figures and
   primary table content now use 15px, section headings 20px, and delivery
   descriptions 14px. The final full and focused comparisons show a readable
   hierarchy; secondary metadata is intentionally smaller.
3. **P2, mobile task visibility — fixed.** The initial mobile table required
   horizontal scrolling to see the work title. It now stacks machine, status,
   project/work, agent, stage and elapsed time. The final mobile screenshot
   exposes the task without horizontal scrolling; browser regression checks
   that the work link is inside the viewport and the document does not overflow.
4. **P2, short-window navigation — fixed.** At 900 × 360 and 700 × 360,
   account controls extended below the viewport. The sidebar now scrolls and
   account details stay in its flow at short heights. Keyboard regressions
   cover opening, reaching Sign out, Escape, focus restoration and overflow.
5. **P2, misleading stale/draining state — fixed.** A failed or old observation
   no longer presents current green availability. Paused and starting states
   retain disconnected-assignment warnings. Capacity labels retained data as
   last known and offers Retry. Browser tests cover failure and recovery.
6. **P2, result destination — fixed.** A receipt originally selected editable
   work details. Delivery links now select Results & evidence and open/focus the
   matching item. Active-plan and historical-plan reloads are covered; the
   final evidence screenshot shows the matching commit and validation output.
7. **P2, project attention navigation — fixed.** Project Inbox and scout
   recovery links referenced obsolete routes. They now open the real Inbox
   with a visible, removable project filter and the real Capacity page.
   Browser integration checks actual navigation, exclusion of other projects,
   reload and clearing the filter. Issue attention opens the Issues tab.

## Required fidelity surfaces

| Surface | Assessment |
| --- | --- |
| Fonts and typography | System sans typography retains the reference's clear hierarchy and plain wordmark. Primary content is 14–15px, headings 20–27px; secondary metadata 11–13px. No external font fetch, clipped primary labels or arbitrary uppercase headings on the overview. Longer real task titles wrap naturally. |
| Spacing and layout rhythm | 208px dark sidebar, light workspace, status and actions, ruled capacity row, machine table, two supporting columns, attention strip. Region order and proportions follow the selected direction. Mobile uses stacked rows and an expandable top navigation. |
| Colors and tokens | Charcoal `#20252b`, white canvas, blue action `#1558e8`, quiet gray rules, green confirmed activity, amber attention. Status also has text and icons, so color is not the only signal. Visible keyboard focus and reduced-motion rules are present. |
| Image quality and assets | UI is rendered as real controls and text. Icons come from the installed Tabler library; OpenAI uses its library brand icon. Other providers use explicit names with library category icons. Invented project logos in the concept are omitted because projects do not have corresponding assets. No generated screenshot is used as the UI. |
| Copy and content | The overview answers whether work is executing, where, what capacity is available, and what landed. Current, starting, disconnected, paused and stale states are distinct. Recorded spend and quota observation scope are explicit. New work offers prepared plans and AI design with a broad goal. |

## Intentional product differences

- Execution has stage names, without invented percentage-complete bars.
- The conceptual Wake machine button is replaced with a link to existing
  Capacity controls; Hive does not expose a wake capability.
- Capacity is counted per runner. Multiple installed agent CLIs do not create
  extra simultaneous execution slots.
- Provider windows remain attached to the machine and observation that reported
  them. Missing or expired readings do not become fabricated free quota.
- Recent results use real landing receipts. A passing label requires validation
  evidence for the landed commit. A delivery is not proof the whole goal is done.
- The sidebar adds an account control and preserves existing member/settings
  access. Results remain reachable from Overview, consistent with the selected
  four-item primary navigation.

## Validation and limits

- Backend integration/regression suite: **824 passed**.
- Browser integration suite: **34 passed**. Covers authentication/cache races, both new-work
  paths, setup retry, plan approval/readiness, live completion, result downloads,
  exact-plan evidence, portfolio, Inbox ownership/pagination, capacity repair,
  quota expiration, lost connections, pause/resume and responsive navigation.
- Production TypeScript/Vite build passed. Vite reports a non-blocking large
  bundle advisory (approximately 165 KB compressed).
- In-app browser error log checked during final navigation: no console errors.
- Preview interactions use mock data; backend tests exercise real HTTP handlers
  with in-memory storage and no live agent/LLM dispatch. Live paid-agent execution
  and production deployment were not part of this verification.
- Inbox currently includes questions, human tasks, plan approvals and blocked
  plan items. Issue blockers and intake brief approvals retain their project
  Issues/Conversation surfaces, as documented in the redesign contract.

Implementation checklist: selected layout implemented; responsive screenshots
inspected; concrete interaction regressions fixed; real API integration tested;
reference and final screenshots saved with this report.

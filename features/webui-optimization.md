# Feature Proposal: Web UI Space Optimization

**Status:** Proposed
**Priority:** High
**Effort:** Medium (2-3 days)
**Affects:** `web/index.html`

---

## Problem

The current Web UI layout wastes significant vertical space, especially on smaller screens
and laptops. Three issues compound to reduce the usable chat area:

1. **Agent cards row at top** -- Each agent gets a horizontal card (~80px tall). With 4-5
   agents, the top section consumes 80-100px of vertical space that is always visible,
   pushing the chat area down.

2. **Redundant agent display** -- The currently selected agent is shown in the agent cards
   (highlighted), in the status/header area above the chat, and sometimes in the session
   bar. The same information appears in 2-3 places simultaneously.

3. **Status bar duplication** -- Agent name, model, and context usage are spread across
   multiple UI elements instead of consolidated into one clean status line.

On a 768px-tall laptop screen, the chat area ends up with roughly 400-450px of usable
height after the header, agent cards, status bar, session bar, and input area are
accounted for. That is barely enough for 3-4 message bubbles.

### Current Layout (Before)

```text
+------------------------------------------------------------------------+
|  [Logo]  Brain Agent              [Theme] [Settings]    <- header 40px |
+------------------------------------------------------------------------+
|                                                                        |
|  +----------+  +----------+  +----------+  +----------+                |
|  |  main    |  | Research |  |  crow    |  | Reporter |  <- cards 80px |
|  |  [avatar]|  | [avatar] |  | [avatar] |  | [avatar] |               |
|  |  online  |  |  idle    |  |  idle    |  |  idle    |               |
|  +----------+  +----------+  +----------+  +----------+                |
|                                                                        |
+------------------------------------------------------------------------+
|  Agent: main  |  Model: claude-opus-4-6  |  Context: 12%  <- status   |
+------------------------------------------------------------------------+
|  [New Chat] [Session 1] [Session 2] [Session 3]       <- sessions     |
+------------------------------------------------------------------------+
|                                                                        |
|  +-- User -------------------------------------------------------+    |
|  | How do I add a new provider?                                   |    |
|  +----------------------------------------------------------------+    |
|                                                                        |
|  +-- main -------------------------------------------------------+    |
|  | To add a new provider, edit config.json and add...             |    |
|  +----------------------------------------------------------------+    |
|                                                         <- chat ~400px |
|                                                                        |
+------------------------------------------------------------------------+
|  [Type a message...]                              [Send] <- input 60px |
+------------------------------------------------------------------------+

Total vertical overhead: ~220px (header + cards + status + sessions)
Usable chat area on 768px screen: ~450px
```

As more agents are added, the cards row either wraps (consuming even more space) or
requires horizontal scrolling, which is clunky on touch devices.

---

## Proposed Solution

Replace the top agent cards row with a **collapsible left sidebar**. Consolidate all
status information into a single compact header line. This maximizes the chat area
while keeping agent switching fast and accessible.

### Proposed Layout (After)

```text
+-- Expanded Sidebar ----+---------------------------------------------------+
| [<<] Brain Agent       | [New Chat] [Session 1] [Session 2]    [Settings] |
|                        +---------------------------------------------------+
| AGENTS                 | main | claude-opus-4-6 | ctx 12%  <- one line    |
| +--------------------+ +---------------------------------------------------+
| | [*] main        .. | |                                                   |
| |     claude-opus     | |  +-- User -----------------------------------+   |
| |     ctx: 12%        | |  | How do I add a new provider?              |   |
| +--------------------+ |  +--------------------------------------------+   |
| | [ ] Research     .. | |                                                   |
| |     sonnet-4-6      | |  +-- main -----------------------------------+   |
| +--------------------+ |  | To add a new provider, edit config.json    |   |
| | [ ] crow         .. | |  | and add a new entry under "providers"...  |   |
| |     Crow-4B         | |  +--------------------------------------------+   |
| +--------------------+ |                                                   |
| | [ ] Reporter     .. | |                                                   |
| |     sonnet-4-6      | |                                                   |
| +--------------------+ |                                                   |
|                        |                                                   |
| TEAMS                  |                              <- chat area ~600px  |
| +--------------------+ |                                                   |
| | Research Team   [3]| |                                                   |
| +--------------------+ |                                                   |
|                        |                                                   |
| SERVICES               |                                                   |
| QMD: healthy           |                                                   |
| oMLX: running          |                                                   |
|                        +---------------------------------------------------+
| [+ New Agent]          | [Type a message...]                       [Send] |
+------------------------+---------------------------------------------------+

Sidebar width: ~220px (expanded)
Total vertical overhead: ~40px (one combined header/status line)
Usable chat area on 768px screen: ~660px (+210px gained)
```

### Collapsed Sidebar (Icons Only)

```text
+------+------------------------------------------------------------+
| [>>] | [New Chat] [Session 1] [Session 2]           [Settings]   |
|      +------------------------------------------------------------+
| [BA] | main | claude-opus-4-6 | ctx 12%                          |
|      +------------------------------------------------------------+
| [Av] |                                                            |
| [Av] |  +-- User ------------------------------------------+     |
| [Av] |  | How do I add a new provider?                      |     |
| [Av] |  +---------------------------------------------------+    |
|      |                                                            |
|      |  +-- main ------------------------------------------+     |
|      |  | To add a new provider...                          |     |
|      |  +---------------------------------------------------+    |
|      |                                                            |
|      |                                                            |
|      |                                      <- chat area ~660px   |
|      |                                      <- width gains ~180px |
|      |                                                            |
|      |                                                            |
|      |                                                            |
|      |                                                            |
|      +------------------------------------------------------------+
| [+]  | [Type a message...]                              [Send]    |
+------+------------------------------------------------------------+

Collapsed sidebar width: ~48px (icons + avatar circles)
Hover over icon: tooltip with agent name + status
```

### Mobile / Narrow View (< 768px)

```text
+------------------------------------------------------------+
| [=] Brain Agent     main | opus-4-6 | 12%      [Settings] |
+------------------------------------------------------------+
| [New Chat] [Session 1] [Session 2]                         |
+------------------------------------------------------------+
|                                                             |
|  +-- User ------------------------------------------+      |
|  | How do I add a new provider?                      |      |
|  +---------------------------------------------------+     |
|                                                             |
|  +-- main ------------------------------------------+      |
|  | To add a new provider, edit config.json...        |      |
|  +---------------------------------------------------+     |
|                                                             |
|                                                             |
+------------------------------------------------------------+
| [Type a message...]                              [Send]    |
+------------------------------------------------------------+

Hamburger [=] opens sidebar as overlay drawer:

+--------------------+-------------------------------------------+
| [X] Brain Agent    |                                           |
|                    |  (dimmed overlay)                         |
| AGENTS             |                                           |
| [*] main        .. |                                           |
| [ ] Research    .. |                                           |
| [ ] crow        .. |                                           |
| [ ] Reporter    .. |                                           |
|                    |                                           |
| TEAMS              |                                           |
| Research Team   [3]|                                           |
|                    |                                           |
| [+ New Agent]      |                                           |
+--------------------+-------------------------------------------+
```

### Consolidated Status Bar

Before (multiple redundant elements):

```text
Agent cards:     [main (selected)]  [Research]  [crow]     <- shows current agent
Status bar:      Agent: main | Model: claude-opus-4-6      <- shows current agent again
Chat header:     Chatting with main                        <- shows current agent AGAIN
```

After (single line, no duplication):

```text
Status line:     main | claude-opus-4-6 | ctx 12% | 3 tools active
```

All information in one compact line. The sidebar shows which agent is selected via
highlight. No repetition anywhere.

---

## Space Savings Summary

```text
                        Before          After           Saved
                        ------          -----           -----
Header:                 40px            0px (merged)    40px
Agent cards:            80px            0px (sidebar)   80px
Status bar:             30px            30px (compact)   0px
Session bar:            36px            36px             0px
                        ------          -----           -----
Vertical overhead:      186px           66px            120px

Chat area (768px):      ~450px          ~660px          +210px (+47%)
Chat area (1080px):     ~760px          ~970px          +210px (+28%)

Horizontal (collapsed): full width      full - 48px     (negligible)
Horizontal (expanded):  full width      full - 220px    (sidebar has useful info)
```

---

## Implementation Plan

### Phase 1: Sidebar Structure (Day 1)

1. Add a `<aside>` element for the sidebar with agent list
2. Move agent cards into sidebar as compact list items
3. Add expand/collapse toggle with localStorage persistence
4. Add CSS transitions for smooth open/close animation
5. Remove the old horizontal agent cards section

### Phase 2: Status Consolidation (Day 1-2)

1. Merge header and status bar into a single `<header>` element
2. Show: agent name + model + context percentage in one line
3. Remove all duplicate agent name displays
4. Add active tool count indicator (replaces verbose tool status)

### Phase 3: Responsive / Mobile (Day 2-3)

1. CSS media query at 768px breakpoint
2. Below 768px: hide sidebar, show hamburger menu
3. Sidebar opens as overlay drawer with backdrop
4. Touch-friendly: swipe to close sidebar
5. Test on iOS Safari, Android Chrome

### Phase 4: Polish (Day 3)

1. Agent activity indicators (pulsing dot) in sidebar
2. Keyboard shortcut: `Ctrl+B` to toggle sidebar
3. Right-click context menu on agent items (configure, pause, delete)
4. Drag-and-drop agent reordering in sidebar

---

## Benefits

- **47% more chat area** on laptop screens -- the primary interaction surface
- **Scales to many agents** -- sidebar scrolls vertically, no wrapping issues
- **Cleaner look** -- no redundant information, professional layout
- **Familiar pattern** -- matches Slack, Discord, VS Code sidebar navigation
- **Mobile-friendly** -- overlay drawer is standard mobile UX pattern
- **Quick switching** -- agents always one click away in sidebar
- **Room for growth** -- sidebar can host teams, services, settings in future

## Trade-offs

- **Less visual agent info at a glance** -- avatars are smaller in sidebar vs cards.
  Mitigated by hover tooltips and expanded mode showing model + status.
- **Horizontal space cost** -- sidebar takes 220px (expanded) or 48px (collapsed).
  On 1920px+ screens this is negligible. On narrow screens, collapsed mode or
  overlay mode eliminates this cost.
- **Learning curve** -- existing users expect top cards. Mitigated by the sidebar
  being a universally familiar pattern (every chat app uses it).
- **Implementation touches one file** -- `web/index.html` is a single-page app, so
  all changes are in one (large) file. No backend changes needed.

## Dependencies

- None. Pure frontend change.
- No new libraries needed (Tailwind CSS handles all layout).
- No API changes required.

## Alternatives Considered

1. **Tabbed agent switcher** -- A tab bar instead of cards. Saves vertical space but
   does not show agent status or scale well past 6-7 agents.

2. **Dropdown agent selector** -- Minimal space but hides agents behind a click.
   Loses the quick-switch benefit and visibility of agent activity.

3. **Keep cards, make them smaller** -- Reduces cards to 40px height. Marginal
   improvement, still consumes a full row, still duplicates status info.

The sidebar approach was chosen because it is the most space-efficient while keeping
all agents visible and one-click accessible. It is also the most extensible for
future features (teams, services, notifications).

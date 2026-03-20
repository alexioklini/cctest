# Feature Proposal: Web Result Caching

**Status:** Proposed
**Priority:** Medium
**Effort:** Low (~2 days)
**Affects:** `claude_cli.py`, `server.py`, `web/index.html`, `tui.py`, `config.json`

---

## Problem

Brain Agent's `web_fetch` and `exa_search` tools make fresh HTTP requests every
time they are called. During a typical research session, agents frequently fetch
the same URL multiple times:

```text
User: Research the pricing of Vercel, Netlify, and Railway.

main: [delegates to Researcher]

Researcher:
  Round 1: web_fetch("https://vercel.com/pricing")           2.3s
  Round 1: web_fetch("https://www.netlify.com/pricing")      1.8s
  Round 1: web_fetch("https://railway.app/pricing")          2.1s
  Round 2: web_fetch("https://vercel.com/pricing")           2.3s  <-- duplicate
  Round 2: web_fetch("https://www.netlify.com/pricing")      1.8s  <-- duplicate
  Round 3: exa_search("Vercel pricing 2026")                 1.5s
  Round 3: exa_search("Vercel pricing 2026")                 1.5s  <-- duplicate
```

Duplicates happen because:

1. **Context compaction** -- after 75% context is used, older tool results are
   summarized. The agent re-fetches URLs to get full content again.
2. **Multi-round research** -- agents compare information across sources,
   revisiting pages to verify details.
3. **Delegation** -- a team head and team member may fetch the same URLs
   independently.
4. **Retry patterns** -- if an agent's response is interrupted or the user
   asks a follow-up, the same URLs get fetched again.

### Impact

- **Time:** Each duplicate web_fetch adds 1-3 seconds. A 10-fetch research
  task with 40% duplicates wastes 4-12 seconds.
- **API costs:** exa_search costs per query. Duplicate searches are wasted
  money.
- **Rate limits:** Repeated fetches hit rate limits faster, causing failures
  that require retry logic.
- **User experience:** Users see the agent "thinking" during duplicate fetches
  with no new information being gathered.

### Competitive Context

OpenClaw caches web results for 15 minutes by default. Claude Code caches
tool results internally. Brain Agent has no caching.

---

## Proposed Solution

Add an **in-memory LRU cache** in `claude_cli.py` for `web_fetch` and
`exa_search` results. Cache is keyed by URL (for web_fetch) or query string
(for exa_search). Configurable TTL, max entries, and enable/disable flag.

### Design Principles

1. **Transparent** -- callers (tools.md, agents) do not need to know about
   the cache; it is automatic
2. **Visible** -- tool results show "(cached)" so users know data is not live
3. **Configurable** -- TTL, max entries, and enable/disable in config.json
4. **Bounded** -- LRU eviction prevents unbounded memory growth
5. **Per-process** -- cache lives in server process memory, cleared on restart

---

## Cache Architecture

### Flow Diagrams

#### Cache Hit (0ms network)

```
  web_fetch("https://vercel.com/pricing")
       |
       v
  +------------------+
  | Check cache      |
  | key: URL + args  |
  +------------------+
       |
       | found + fresh (age < TTL)
       v
  +------------------+
  | Return cached    |
  | result           |
  | + "(cached)" tag |
  +------------------+
       |
       v
  Tool result returned to agent in ~0ms
```

#### Cache Miss (normal fetch)

```
  web_fetch("https://vercel.com/pricing")
       |
       v
  +------------------+
  | Check cache      |
  | key: URL + args  |
  +------------------+
       |
       | not found OR expired (age >= TTL)
       v
  +------------------+
  | HTTP GET         |
  | vercel.com/...   |----> 2.3 seconds
  +------------------+
       |
       v
  +------------------+
  | Store in cache   |
  | key: URL + args  |
  | timestamp: now   |
  +------------------+
       |
       v
  Tool result returned to agent
```

#### Cache Eviction (LRU)

```
  Cache is full (100 entries)
       |
       v
  +------------------+
  | New entry needs  |
  | to be stored     |
  +------------------+
       |
       v
  +------------------+
  | Evict least      |
  | recently used    |
  | entry            |
  +------------------+
       |
       v
  +------------------+
  | Store new entry  |
  | in freed slot    |
  +------------------+
```

---

## Implementation

### Cache Data Structure

```python
import threading
from collections import OrderedDict
from time import time

class WebCache:
    """Thread-safe LRU cache for web_fetch and exa_search results."""

    def __init__(self, max_entries: int = 100, ttl_seconds: int = 900):
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> dict | None:
        with self._lock:
            if key in self._cache:
                ts, value = self._cache[key]
                if time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    del self._cache[key]
            self._misses += 1
            return None

    def put(self, key: str, value: dict):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_entries:
                self._cache.popitem(last=False)  # evict LRU
            self._cache[key] = (time(), value)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio": round(self._hits / total, 2) if total else 0,
                "memory_kb": sum(
                    len(str(v)) for _, v in self._cache.values()
                ) // 1024
            }

# Global instance
_web_cache = WebCache()
```

### Cache Key Generation

```python
def _cache_key(tool: str, **kwargs) -> str:
    """Generate cache key from tool name and parameters."""
    if tool == "web_fetch":
        # Key on URL + method (ignore headers for cache purposes)
        return f"fetch:{kwargs.get('url', '')}:{kwargs.get('method', 'GET')}"
    elif tool == "exa_search":
        # Key on query + num_results
        return f"exa:{kwargs.get('query', '')}:{kwargs.get('num_results', 5)}"
    return ""
```

### Integration with web_fetch

```python
def _tool_web_fetch(url: str, method: str = "GET", **kwargs) -> dict:
    # Check cache first
    cache_key = _cache_key("web_fetch", url=url, method=method)
    if method == "GET":  # only cache GET requests
        cached = _web_cache.get(cache_key)
        if cached is not None:
            cached["_cached"] = True
            return cached

    # Normal fetch
    result = _do_http_fetch(url, method, **kwargs)

    # Store in cache (only successful GET responses)
    if method == "GET" and result.get("status_code", 0) < 400:
        _web_cache.put(cache_key, result)

    return result
```

### Integration with exa_search

```python
def _tool_exa_search(query: str, num_results: int = 5, **kwargs) -> dict:
    cache_key = _cache_key("exa_search", query=query, num_results=num_results)
    cached = _web_cache.get(cache_key)
    if cached is not None:
        cached["_cached"] = True
        return cached

    result = _do_exa_search(query, num_results, **kwargs)

    if result.get("results"):
        _web_cache.put(cache_key, result)

    return result
```

---

## Configuration

### config.json

```json
{
  "web_cache": {
    "enabled": true,
    "ttl_seconds": 900,
    "max_entries": 100
  }
}
```

### Per-Agent Override (agent.json)

Agents doing real-time monitoring may want caching disabled:

```json
{
  "description": "Stock market monitor",
  "display_name": "Ticker",
  "model": "claude-sonnet-4-6",
  "web_cache": {
    "enabled": false
  }
}
```

### Runtime Control

```
POST /v1/cache/clear     -- flush the entire web cache
GET  /v1/cache/stats     -- return hit/miss stats
```

---

## UI Integration

### Web UI: Cached Badge

When a tool result comes from cache, show a small badge:

```
+------------------------------------------------------------------+
|  Researcher                                                       |
+------------------------------------------------------------------+
|                                                                    |
|  [tool] web_fetch("https://vercel.com/pricing")                   |
|  +--------------------------------------------------------------+ |
|  | Status: 200                              [Cached - 3m ago]   | |
|  |                                                              | |
|  | Vercel Pricing                                               | |
|  | - Hobby: Free                                                | |
|  | - Pro: $20/user/month                                        | |
|  | - Enterprise: Custom                                         | |
|  +--------------------------------------------------------------+ |
|                                                                    |
|  [tool] web_fetch("https://www.netlify.com/pricing")              |
|  +--------------------------------------------------------------+ |
|  | Status: 200                                                  | |
|  |                                                              | |
|  | Netlify Pricing                                              | |
|  | - Starter: Free                                              | |
|  | - Pro: $19/member/month                                      | |
|  +--------------------------------------------------------------+ |
|                                                                    |
+------------------------------------------------------------------+
```

The `[Cached - 3m ago]` badge appears only on cache hits. It shows how old
the cached data is so the user can judge freshness.

### Implementation in web/index.html

```javascript
function renderToolResult(tool, result) {
    const el = document.createElement('div');
    el.className = 'tool-result';

    if (result._cached) {
        const badge = document.createElement('span');
        badge.className = 'cache-badge';
        badge.textContent = `Cached - ${formatAge(result._cached_at)}`;
        el.appendChild(badge);
    }

    // ... rest of rendering
}
```

CSS:

```css
.cache-badge {
    float: right;
    font-size: 0.75rem;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--accent-subtle);
    color: var(--text-secondary);
}
```

### TUI: Cached Indicator

```
  [tool] web_fetch  https://vercel.com/pricing  (cached)
  Status: 200 | 12.4 KB

  Vercel Pricing
  - Hobby: Free
  - Pro: $20/user/month
  ...
```

The `(cached)` text appears inline after the URL in the tool call header.

### Telegram

```
[web_fetch] vercel.com/pricing (cached)
Status: 200

Vercel Pricing
- Hobby: Free
...
```

---

## Cache Stats in /status

The existing `GET /v1/status` endpoint gains a `web_cache` section:

```json
{
  "status": "ok",
  "uptime": "4h 23m",
  "web_cache": {
    "enabled": true,
    "entries": 34,
    "max_entries": 100,
    "ttl_seconds": 900,
    "hits": 127,
    "misses": 89,
    "hit_ratio": 0.59,
    "memory_kb": 482
  }
}
```

### Web UI Settings Tab

```
+------------------------------------------------------------------+
|  Settings > Cache                                                 |
+------------------------------------------------------------------+
|                                                                    |
|  Web Result Cache                                                 |
|                                                                    |
|  Enabled:      [x]                                                |
|  TTL:          [900] seconds (15 minutes)                         |
|  Max entries:  [100]                                              |
|                                                                    |
|  --- Statistics ---                                               |
|  Entries:      34 / 100                                           |
|  Hits:         127                                                |
|  Misses:       89                                                 |
|  Hit ratio:    59%                                                |
|  Memory:       482 KB                                             |
|                                                                    |
|  [Clear Cache]   [Save Settings]                                  |
|                                                                    |
+------------------------------------------------------------------+
```

---

## End-to-End Workflow

### Scenario: Research Task with Caching

```
Step 1: Agent fetches Vercel pricing (cache MISS)
        web_fetch("https://vercel.com/pricing")
        => HTTP GET, 2.3 seconds
        => Stored in cache, key: "fetch:https://vercel.com/pricing:GET"
        => User sees normal tool result

Step 2: Agent fetches Netlify pricing (cache MISS)
        web_fetch("https://www.netlify.com/pricing")
        => HTTP GET, 1.8 seconds
        => Stored in cache

Step 3: Agent re-fetches Vercel for comparison (cache HIT)
        web_fetch("https://vercel.com/pricing")
        => Cache hit, 0ms
        => User sees tool result with [Cached - 2m ago] badge

Step 4: 15 minutes later, cache entry expires
        web_fetch("https://vercel.com/pricing")
        => Cache expired, fresh HTTP GET, 2.1 seconds
        => New result stored in cache

Step 5: Server restarts
        => Cache cleared (in-memory only)
        => All fetches are fresh on next use
```

### Scenario: exa_search Caching

```
Step 1: exa_search("Vercel pricing 2026", num_results=5)
        => API call, 1.5 seconds, costs $0.001
        => Stored in cache, key: "exa:Vercel pricing 2026:5"

Step 2: Same search triggered again (context compaction or retry)
        exa_search("Vercel pricing 2026", num_results=5)
        => Cache hit, 0ms, $0.00
        => User sees "(cached)" indicator

Step 3: Different query, not cached
        exa_search("Netlify pricing 2026", num_results=5)
        => Cache miss, fresh API call
```

---

## Cache Invalidation Strategy

### When Cache is Cleared

| Event                  | Action                    | Rationale                        |
|------------------------|---------------------------|----------------------------------|
| TTL expires            | Entry evicted on next GET | Default staleness protection     |
| LRU eviction           | Oldest entry removed      | Memory bound                     |
| Server restart         | Full clear                | In-memory, no persistence needed |
| Manual clear           | Full clear via API        | User control                     |
| POST/PUT/DELETE fetch  | Never cached              | Only GET is idempotent           |

### What is NOT Cached

- `web_fetch` with method other than GET (POST, PUT, DELETE)
- `web_fetch` responses with status >= 400 (errors should be retried fresh)
- `web_fetch` with `force_fresh: true` parameter (new optional param)
- Responses larger than 1MB (prevent memory bloat from large pages)

### Force Fresh Parameter

For cases where the agent explicitly needs live data:

```json
{
  "name": "web_fetch",
  "parameters": {
    "url": "https://api.example.com/live-data",
    "force_fresh": true
  }
}
```

This bypasses the cache for both read and write -- the result is fetched
fresh and does NOT update the cache (since the user explicitly wanted to
skip caching for this URL).

---

## Benefits

1. **Faster responses** -- cache hits return in 0ms instead of 1-3 seconds
2. **Lower costs** -- duplicate exa_search calls cost nothing from cache
3. **Better UX** -- "(cached)" badge gives users transparency about data
   freshness
4. **Rate limit protection** -- fewer duplicate requests means fewer 429 errors
5. **Simple implementation** -- Python stdlib OrderedDict, no external
   dependencies, thread-safe with a single lock
6. **Configurable** -- per-agent and global settings, TTL, max entries
7. **Zero maintenance** -- in-memory cache clears itself on restart, LRU
   handles eviction automatically

## Trade-offs

1. **Stale data** -- cached results may be up to 15 minutes old. Mitigated
   by configurable TTL and `force_fresh` parameter.
2. **Memory usage** -- 100 cached pages at ~10KB each = ~1MB. Bounded by
   max_entries and 1MB per-entry limit.
3. **Cache key collisions** -- two different POST bodies to the same URL
   would collide. Mitigated by only caching GET requests.
4. **No persistence** -- cache is lost on server restart. This is intentional:
   web content changes, and a fresh start guarantees fresh data.
5. **No cross-agent sharing** -- cache is global (all agents share it), which
   is actually a benefit for delegation scenarios where multiple agents
   research the same topic.

## Effort Breakdown

| Task                                  | Days |
|---------------------------------------|------|
| WebCache class + tests                | 0.25 |
| web_fetch integration                 | 0.25 |
| exa_search integration                | 0.25 |
| Config loading (config.json + agent)  | 0.25 |
| Web UI cached badge                   | 0.25 |
| TUI + Telegram cached indicator       | 0.25 |
| /v1/cache/stats + /v1/cache/clear     | 0.25 |
| Settings tab UI                       | 0.25 |
| **Total**                             | **2**  |

## Open Questions

1. Should the cache persist to disk (SQLite) for survival across restarts?
   Current design says no -- web content is ephemeral and a fresh start is
   healthy. But for expensive exa_search results, persistence might save money.
2. Should cache TTL be per-domain? News sites might need 5-minute TTL while
   documentation sites could safely cache for 1 hour.
3. Should delegated tasks share the parent's cache or have their own? Current
   design uses a single global cache (shared), which seems right for research
   tasks.
4. Maximum single-entry size -- 1MB seems reasonable. Should this be
   configurable?

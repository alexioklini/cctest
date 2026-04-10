---
title: "### Key Patterns"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:33:04.549287+00:00"
chunk_index: 2
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-001.md
    type: prev_chunk
  - file: ingest-6ebdb6-003.md
    type: next_chunk
  - file: ingest-6ebdb6-000.md
    type: same_source
---

### Key Patterns

nswer/{query_id}`
- TUI interactive: `client.chat(..., interactive=True)` enables AskUserQuestion, renders questions with options, sends answers via `client.answer()`

Provider routing for SDK (env vars per provider):
- `cliproxyapi`: Claude models (Max subscription OAuth) + Gemini, Qwen — `ANTHROPIC_BASE_URL=http://127.0.0.1:8317`
- `omlx`: Local Crow models — `ANTHROPIC_BASE_URL=http://127.0.0.1:8000`
- `minimax`: MiniMax M2.5/M2.7 — `ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic`

### Multi-Provider Routing

The server supports multiple LLM providers (config.json). When a model is selected,
the server automatically routes to the correct provider based on which one has that model.
Provider types: `openai` (OpenAI-compatible) and `anthropic` (native Anthropic API).

### Key Patterns

-

`tools.md`

and

`soul.md`

are

injected

into

the

system

prompt

—

primary

way

to

control

agent

behavior

-

`execute_command`

runs

with

no

TTY,

no

stdin,

TERM=dumb

—

interactive

commands

timeout

-

SQLite

connections

use

thread-local

pools

(`_db_conn`,

`_sched_conn`)

to

prevent

handle

leaks

-

All

ChatDB

methods

wrapped

with

`@_db_safe`

—

SQLite

errors

don't

crash

the

server

-

SSE

keepalive

comments

sent

every

5s

to

prevent

browser

timeout

during

tool

execution

-

`AbortController`

in

web

UI

ensures

proper

fetch

cleanup

between

messages

-

Tool

call

dedup

tracker

prevents

infinite

loops

(2

identical

calls

=

hard

abort)

-

Scheduled

tasks

have

configurable

timeout

(default

5

min)

via

watchdog

thread

-

`_run_delegate`

uses

thread-local

`max_tool_rounds`

override

(no

global

mutation)

and

thread-local

memory

stores

-

Memory

uses

QMD

hybrid

search

(BM25

+

vector

+

LLM

reranking)

via

HTTP

MCP

on

port

8181

-

Markdown

files

are

source

of

truth

for

memory;

QMD

indexes

them

with

per-collection

debounced

embed

after

writes

-

If

QMD

is

unreachable,

memory

recall

falls

back

to

file-scan

substring

matching

-

QMD

docs

endpoint

returns

index

health

per

file:

`indexed`,

`embedded_at`,

`current`

(hash

match)

-

QMD

path

normalization:

QMD

lowercases

paths

and

converts

underscores

to

hyphens

—

`/docs`

endpoint

mirrors

this

when

matching

filesystem

paths

to

index

entries

-

`/v1/services`

returns

per-collection

health

stats:

`total`,

`indexed`,

`embedded`,

`stale`,

`not_indexed`

-

Smart

model

routing:

`init_models_config()`

auto-discovers

models

from

providers,

`resolve_model()`

picks

by

purpose

-

Providers

without

`/models`

endpoint:

manually-configured

models

from

`_models_config`

are

included

in

provider

listings

-

QMD

session

reuse:

`_qmd_session_lock`

prevents

concurrent

threads

from

creating

duplicate

MCP

sessions

-

QMD

health

check

uses

lightweight

TCP

socket

connect

(no

MCP

session

created)

-

`memory_shared`

and

`list_all`

return

full

content

body,

not

just

metadata

-

Telegram

runs

as

an

in-process

thread,

not

a

separate

launchd

daemon

-

Thread-safe

agent

context:

`_thread_local.current_agent`

and

`_thread_local.mcp_manager`

preferred

over

globals

for

concurrent

requests

-

Session

restore

resolves

provider

from

model

via

`_resolve_provider_static()`

(no

more

wrong

API

key/URL

on

old

chats)

-

Provider

cache

uses

`_provider_cache_lock`

for

thread-safe

access

-

Memory

frontmatter

uses

`_yaml_escape()`

to

prevent

YAML

injection

from

user

content

-

Memory

filenames

include

hash

suffix

to

prevent

collisions

between

similar

names

-

Scheduler

executes

due

tasks

in

parallel

threads

instead

of

sequentially

-

Agent

activity

tracking:

`/v1/agents/activity`

returns

active

tasks/chats

per

agent

for

UI

indicators

-

Auto

memory

creation:

heuristic

detection

(corrections,

identity,

decisions,

references)

+

LLM

extraction

via

Haiku,

runs

in

background

after

each

response

-

Continuous

session

summarization:

memory

summary

refreshes

at

10K

tokens,

then

every

5K

during

active

conversations

-

Autodream

memory

consolidation:

chains

after

relationship

discovery

in

nightly

pipeline

(Memory

Summary

→

RD

→

Autodream)

-

Autodream

passes:

dedup

(QMD

similarity

+

LLM

merge),

staleness

(frontmatter

`last_recalled`

+

`stale`

flags),

conflicts

(LLM

contradiction

detection),

skill

candidates

(procedural

memory

detection)

-

Autodream

config

in

agent.json:

`autodream:

{enabled,

stale_threshold_days,

dedup_similarity_threshold,

max_dedup_merges,

max_conflict_checks,

report_retention}`

-

Memory

summary

config:

`memory_summary:

{enabled,

frequency,

start_time,

model}`

—

`model`

overrides

default

Sonnet

for

the

nightly

scheduled

task

-

Relationship

discovery

config:

`relationship_discovery:

{enabled,

frequency,

start_time,

model}`

—

`model`

overrides

default

Haiku;

configurable

in

GUI

(Agent

config

→

Memory

tab)

-

Token

optimization:

memory

summary

injected

on

`_tool_round==0`

only

(not

per

tool-loop

call),

3K

char

cap

on

injected

summary,

`read_file`

default

limit

400

lines,

compact

threshold

60%,

fresh_tail

16

-

Background

pipeline

models:

memory

summary

scheduled

tasks

→

Sonnet,

relationship

discovery

→

Haiku;

`ensure_*_schedules()`

auto-recreates

when

model

changes

-

Autodream

health

report:

stored

as

"Memory

Health

Report

—

{date}"

memory

file

(type:

system),

auto-retained

(last

N

reports)

-

`last_recalled`

frontmatter

field:

stamped

on

recall

in

background

thread,

used

for

staleness

detection

-

`get_memory_health(agent_id)`:

live

stats

—

total,

by_type,

stale_count,

age_distribution,

recall_frequency

(hot/warm/cold/never),

autodream

results,

health_score

-

`GET

/v1/agents/<id>/memory-health`:

full

health

dashboard

data;

`trigger_autodream(agent_id)`

for

manual

runs

-

Knowledge

graph:

auto-discovery

(LLM-based,

entity

extraction,

co-recall),

graph-aware

recall

default

(1

hop),

visualization

via

Canvas

2D

-

Model-aware

max_tokens:

Opus

32K,

Sonnet

16K,

Haiku

8K,

MiniMax

32K,

configurable

via

`max_output`

in

models

config

-

Provider

fallback

ordering:

same

provider

first,

then

capabilities,

then

priority

-

Chat

file

attachments:

files

created

by

agents

(write_file/edit_file)

appear

as

viewable/downloadable

attachments

-

`get_model_max_output(model)`

returns

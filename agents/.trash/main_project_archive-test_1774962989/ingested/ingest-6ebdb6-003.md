---
title: "### Key Patterns"
source: CLAUDE.md
source_type: md
ingested_at: "2026-03-31T12:33:04.516848+00:00"
chunk_index: 3
total_chunks: 6
agent: main
tags:
  - ingested
  - claude
related:
  - file: ingest-6ebdb6-002.md
    type: prev_chunk
  - file: ingest-6ebdb6-004.md
    type: next_chunk
  - file: ingest-6ebdb6-000.md
    type: same_source
---

### Key Patterns

_type,

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

max

output

tokens

based

on

model

family

or

config

-

Project

notes:

NoteManager

CRUD,

AI

editing

via

write_file/edit_file

tools

(not

EDIT_NOTE

tags),

auto-reload

on

filesystem

changes

-

Chat

transcript

indexing:

chats-indexed/*.md

chunks

stored

in

QMD

for

semantic

search

-

LLM

chat

summaries:

generated

via

Haiku

after

first

response,

shown

in

sidebar

-

Project

panel

auto-refresh:

5s

polling

detects

filesystem

changes

from

any

source

-

Note

AI

sessions:

status

`note_chat`,

hidden

from

project

chat

list,

persistent

per

note

via

localStorage

-

Agent

teams:

hierarchical

team

structure

with

team

heads

orchestrating

members

-

Cost

tracking:

`CostTracker`

logs

every

LLM

call

to

`costs.db`

(tokens,

model,

provider,

estimated

cost)

-

Rate

limiting:

`RateLimiter`

with

sliding-window

per

agent

(requests/min,

tokens/hr,

cost/day)

from

`rate_limits`

in

agent.json

-

Cost

rates

from

`_cost_rates`

defaults

+

`cost_input`/`cost_output`

fields

in

`_models_config`

-

`list_nodes`

tool

queries

`GET

/v1/nodes`

to

let

agents

discover

available

remote

nodes

-

`node.py`

supports

`--install`

(launchd

plist),

`--uninstall`,

`--status`

for

macOS

daemon

management

-

Node

plist:

`~/Library/LaunchAgents/com.brain-agent.node.{name}.plist`,

logs

to

`~/.brain-agent/node-{name}.log`

-

Node

connectivity:

quick

`GET

/v1/nodes`

check

before

entering

long-poll

loop

for

instant

"Connected"

feedback

-

Sidebar

session

list

polls

after

stream

end

until

async

LLM

summary

appears

(2s

interval,

30s

max)

-

Chat

content

search:

3-tier

(QMD

semantic

→

SQLite

title/summary

→

SQLite

message

content)

-

Chat

transcript

indexing

decoupled

from

summary

generation;

backfill

runs

at

startup

for

unindexed

sessions

-

Sessions

API

returns

`indexed`

field

(true/false/null)

based

on

chats-indexed

file

mtime

vs

last_active

-

`_parse_frontmatter()`

skips

indented/nested

YAML

lines

to

prevent

`related:`

sub-fields

overwriting

top-level

keys

-

Knowledge

graph

edge

resolution:

ref

files

with

`/`

treated

as

agent-relative

paths

(no

double-prefix)

-

Relationship

discovery:

two-stage

(QMD

semantic

candidates

→

LLM

full-content

classification),

scales

to

large

file

counts

-

QMD

query

cleanup:

strip

newlines,

quotes,

markdown

formatting

—

QMD

silently

returns

empty

on

multiline

queries

-

Lossless

context:

`ContextManager`

in

`claude_cli.py`

with

SQLite

DAG

(`context.db`),

replaces

flat

compaction

-

Context

config:

`GET/POST

/v1/context/config`,

`GET

/v1/context/stats?session_id=X`

-

Context

assembly:

summaries

(highest

depth

first)

+

fresh

tail

(default

16

messages)

within

token

budget

-

Three-level

escalation:

leaf

summaries

→

condensation

→

fallback

truncation

-

Thread-local

`current_session_id`

set

before

compaction

for

context

tools

to

access

-

Legacy

`_compact_conversation`

remains

as

fallback

when

ContextManager

is

disabled

-

Three-layer

hooks:

tool

pre/post

(external

scripts),

after_file_write

(centralized

pipeline),

LLM-level

(built-in)

-

`HookRunner`

loads

hooks

from

`agent.json`

`hooks.scripts[]`,

runs

via

subprocess

with

env

vars

+

stdin

JSON

-

Hooks

timeout

(default

5s),

fail-open

on

crash,

exit

1

=

block

(pre)

or

error

(post),

exit

2

=

skip

chain

-

`_after_file_write()`

centralizes

QMD

reindex

+

entity

extraction

+

KG

update

+

file

events

+

external

hooks

-

`_execute_tool()`

orchestrates:

built-in

pre

→

external

pre

→

execute

→

built-in

post

→

external

post

-

Workflow

`allowed_tools`

restriction

now

enforced

(was

dead

code)

-

Hook

runners

cached

per

agent,

invalidated

on

config

save

-

GET/POST

`/v1/agents/{id}/hooks`

for

hook

management

-

Compaction

sends

SSE

events

(`compacting`,

`compacted`)

for

spinner

feedback

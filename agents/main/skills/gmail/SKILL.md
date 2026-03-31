---
name: Gmail
slug: gmail
description: "Send and receive emails via Gmail using IMAP/SMTP. Read inbox, search, send, reply, forward."
last_recalled: 2026-03-30
---

# Gmail Skill

Send and receive emails via a Gmail account.

## Setup

1. Enable 2-Factor Authentication on your Google Account
2. Go to https://myaccount.google.com/apppasswords
3. Create an App Password for "Mail"
4. Configure in Brain Agent: agents/main/gmail.json

```json
{
  "email": "your.email@gmail.com",
  "app_password": "xxxx xxxx xxxx xxxx"
}
```

## Available Tools

The gmail tools are available as built-in tools when configured:

- `gmail_inbox` — List recent emails (subject, from, date)
- `gmail_read` — Read a specific email by ID
- `gmail_search` — Search emails by query (Gmail search syntax)
- `gmail_send` — Send a new email
- `gmail_reply` — Reply to an email

## Gmail Search Syntax

- `from:someone@example.com` — from specific sender
- `subject:meeting` — subject contains
- `is:unread` — unread only
- `after:2026/03/01` — date filter
- `has:attachment` — with attachments
- `label:important` — by label

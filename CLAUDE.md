# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A lightweight Python CLI tool for interacting with the Claude API. Single-file implementation with zero external dependencies (stdlib only).

## Running the CLI

```bash
# Run directly
python3 claude_cli.py "your message"

# Interactive mode (reads from stdin)
python3 claude_cli.py -i

# List available models
python3 claude_cli.py -l

# With custom model
python3 claude_cli.py -m claude-sonnet-4-20250514 "your message"
```

## Architecture

**Single file**: `claude_cli.py` - HTTP client that connects to a Claude API server (default: `http://localhost:8317/v1`)

Key functions:
- `list_models()` - GET /models endpoint
- `send_message()` - POST /chat/completions with SSE streaming
- `main()` - CLI argument parsing

The tool uses Server-Sent Events (SSE) for streaming responses.

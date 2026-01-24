#!/usr/bin/env python3
"""Simple CLI tool to send messages to the Claude Code API."""

import argparse
import json
import sys
import urllib.request
import urllib.error


def get_available_models(api_key: str, base_url: str) -> list[str]:
    """Fetch available models from the API and return as a list."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    request = urllib.request.Request(
        f"{base_url}/models",
        headers=headers,
        method="GET",
    )

    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read().decode("utf-8"))
            models = data.get("data", [])
            return [model.get("id") for model in models if model.get("id")]
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []


def list_models(api_key: str, base_url: str) -> None:
    """List available models from the API."""
    models = get_available_models(api_key, base_url)
    if models:
        print("Available models:")
        for model_id in models:
            print(f"  {model_id}")
    else:
        print("No models available")


def send_message(message: str, model: str, api_key: str, base_url: str) -> bool:
    """Send a message to the Claude API and stream the response.

    Returns True on success, False on model-related errors.
    Exits on other errors.
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": message}],
        "stream": True,
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/messages",
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            current_event = None
            for line in response:
                line = line.decode("utf-8").strip()
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    if current_event == "message_stop":
                        break
                    try:
                        event = json.loads(line[6:])
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                print(delta.get("text", ""), end="", flush=True)
                    except json.JSONDecodeError:
                        pass

        print()  # Final newline
        return True

    except urllib.error.HTTPError as e:
        if e.code == 400:
            # Model-related error, return False to allow fallback
            return False
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        try:
            error_body = e.read().decode("utf-8")
            print(error_body, file=sys.stderr)
        except:
            pass
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def send_message_with_fallback(message: str, model: str, api_key: str, base_url: str) -> None:
    """Send a message, falling back to available models if the requested one fails."""
    # Try the requested model first
    if send_message(message, model, api_key, base_url):
        return

    # Model failed, try to get available models
    available_models = get_available_models(api_key, base_url)
    if not available_models:
        print(f"Error: Model '{model}' is not available and no fallback models found.", file=sys.stderr)
        sys.exit(1)

    # Try each available model
    tried_models = {model}
    for fallback_model in available_models:
        if fallback_model in tried_models:
            continue
        tried_models.add(fallback_model)

        print(f"Note: Model '{model}' is not available, using '{fallback_model}' instead.", flush=True)
        if send_message(message, fallback_model, api_key, base_url):
            return

    # All models failed
    print(f"Error: No working models found. Tried: {', '.join(tried_models)}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Send messages to the Claude Code API"
    )
    parser.add_argument(
        "message",
        nargs="?",
        help="Message to send (or use -i for interactive mode)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default="claude-opus-4-5-20251101",
        help="Model to use (default: claude-opus-4-5-20251101)",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactive mode - read message from stdin",
    )
    parser.add_argument(
        "-l",
        "--list-models",
        action="store_true",
        help="List available models and exit",
    )
    parser.add_argument(
        "--api-key",
        default="sk-Xk7kOHpIpZkLutwnyxHpRO9jn4ZwyPaS",
        help="API key for authentication",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8317/v1",
        help="Base URL for the API (default: http://localhost:8317/v1)",
    )

    args = parser.parse_args()

    if args.list_models:
        list_models(args.api_key, args.base_url)
        sys.exit(0)

    if args.interactive:
        print("Enter your message (Ctrl+D to send):")
        message = sys.stdin.read().strip()
    elif args.message:
        message = args.message
    else:
        parser.print_help()
        sys.exit(1)

    if not message:
        print("Error: No message provided", file=sys.stderr)
        sys.exit(1)

    send_message_with_fallback(message, args.model, args.api_key, args.base_url)


if __name__ == "__main__":
    main()

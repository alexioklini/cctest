#!/usr/bin/env python3
"""Brain Agent Notification System — in-app, webhook, email channels."""

import collections
import json
import logging
import os
import smtplib
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Event types
EVENT_TYPES = {
    "task_complete", "task_failed", "task_timeout",
    "budget_alert", "node_offline", "approval_needed",
    "agent_error", "service_offline", "service_online",
    "delegate_complete", "delegate_failed", "server_restart",
}

# Severity levels (ordered)
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


class Notification:
    """A single notification entry."""

    def __init__(self, event_type: str, title: str, message: str,
                 severity: str = "info", agent: str | None = None,
                 metadata: dict | None = None):
        self.id = str(uuid.uuid4())[:12]
        self.event_type = event_type
        self.title = title
        self.message = message
        self.severity = severity
        self.agent = agent
        self.metadata = metadata or {}
        self.created_at = datetime.utcnow().isoformat() + "Z"
        self.read = False
        self.dismissed = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "agent": self.agent,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "read": self.read,
            "dismissed": self.dismissed,
        }


class NotificationManager:
    """Thread-safe notification manager with in-app, webhook, and email channels.

    Config structure (from config.json "notifications" section):
    {
        "enabled": true,
        "channels": {
            "in_app": {"enabled": true, "min_severity": "info"},
            "webhook": {"enabled": false, "url": "...", "min_severity": "warning",
                        "retry_max": 3, "retry_backoff": [5, 30, 300]},
            "email": {"enabled": false, "smtp_host": "smtp.gmail.com", "smtp_port": 587,
                      "smtp_user": "", "smtp_password": "", "to": [],
                      "min_severity": "error"}
        }
    }
    """

    def __init__(self, config: dict | None = None):
        self._lock = threading.Lock()
        self._notifications: collections.deque[Notification] = collections.deque(maxlen=100)
        self._config: dict = config or {}
        self._worker_queue: collections.deque = collections.deque(maxlen=500)
        self._worker_thread: threading.Thread | None = None
        self._running = False
        if self._config.get("enabled", False):
            self._start_worker()

    def update_config(self, config: dict):
        """Update notification config and restart worker if needed."""
        with self._lock:
            self._config = config
        if config.get("enabled", False) and not self._running:
            self._start_worker()
        elif not config.get("enabled", False) and self._running:
            self._running = False

    def get_config(self) -> dict:
        with self._lock:
            return dict(self._config)

    def notify(self, event_type: str, title: str, message: str,
               severity: str = "info", agent: str | None = None,
               metadata: dict | None = None):
        """Create and dispatch a notification. Non-blocking."""
        if not self._config.get("enabled", False):
            return
        if event_type not in EVENT_TYPES:
            logging.warning(f"Unknown notification event type: {event_type}")

        notif = Notification(event_type, title, message, severity, agent, metadata)

        # Store in-app
        channels = self._config.get("channels", {})
        in_app_cfg = channels.get("in_app", {})
        if in_app_cfg.get("enabled", True):
            min_sev = SEVERITY_ORDER.get(in_app_cfg.get("min_severity", "info"), 0)
            if SEVERITY_ORDER.get(severity, 0) >= min_sev:
                with self._lock:
                    self._notifications.appendleft(notif)

        # Queue webhook/email for background delivery
        for channel_name in ("webhook", "email"):
            ch_cfg = channels.get(channel_name, {})
            if not ch_cfg.get("enabled", False):
                continue
            min_sev = SEVERITY_ORDER.get(ch_cfg.get("min_severity", "warning"), 1)
            if SEVERITY_ORDER.get(severity, 0) >= min_sev:
                self._worker_queue.append((channel_name, ch_cfg, notif))

    def get_notifications(self, limit: int = 50, include_dismissed: bool = False) -> list[dict]:
        """Return recent in-app notifications."""
        with self._lock:
            result = []
            for n in self._notifications:
                if not include_dismissed and n.dismissed:
                    continue
                result.append(n.to_dict())
                if len(result) >= limit:
                    break
            return result

    def get_unread_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._notifications if not n.read and not n.dismissed)

    def mark_read(self, notification_id: str | None = None):
        """Mark a notification as read, or all if id is None."""
        with self._lock:
            for n in self._notifications:
                if notification_id is None or n.id == notification_id:
                    n.read = True

    def dismiss(self, notification_id: str):
        """Dismiss a specific notification."""
        with self._lock:
            for n in self._notifications:
                if n.id == notification_id:
                    n.dismissed = True
                    return True
        return False

    def clear_all(self):
        """Clear all notifications."""
        with self._lock:
            self._notifications.clear()

    # --- Background worker ---

    def _start_worker(self):
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._delivery_worker, daemon=True, name="notif-worker")
        self._worker_thread.start()

    def _delivery_worker(self):
        """Background thread that processes webhook/email deliveries."""
        while self._running:
            try:
                if not self._worker_queue:
                    time.sleep(1)
                    continue
                channel_name, ch_cfg, notif = self._worker_queue.popleft()
                try:
                    if channel_name == "webhook":
                        self._send_webhook(ch_cfg, notif)
                    elif channel_name == "email":
                        self._send_email(ch_cfg, notif)
                except Exception as e:
                    logging.warning(f"Notification delivery failed ({channel_name}): {e}")
            except IndexError:
                time.sleep(1)
            except Exception as e:
                logging.warning(f"Notification worker error: {e}")
                time.sleep(5)

    def _send_webhook(self, cfg: dict, notif: Notification, attempt: int = 0):
        """POST notification as JSON to configured webhook URL with retry."""
        url = cfg.get("url", "")
        if not url:
            return
        max_retries = cfg.get("retry_max", 3)
        backoff_schedule = cfg.get("retry_backoff", [5, 30, 300])

        payload = {
            "event": notif.event_type,
            "severity": notif.severity,
            "timestamp": notif.created_at,
            "agent": notif.agent,
            "title": notif.title,
            "message": notif.message,
            "metadata": notif.metadata,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = dict(cfg.get("headers", {"Content-Type": "application/json"}))
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status < 300:
                    return  # Success
        except Exception as e:
            if attempt < max_retries:
                delay = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                logging.info(f"Webhook retry {attempt + 1}/{max_retries} in {delay}s: {e}")
                time.sleep(delay)
                self._send_webhook(cfg, notif, attempt + 1)
            else:
                logging.warning(f"Webhook delivery failed after {max_retries} retries: {e}")

    def _send_email(self, cfg: dict, notif: Notification):
        """Send notification via SMTP."""
        host = cfg.get("smtp_host", "")
        port = cfg.get("smtp_port", 587)
        user = cfg.get("smtp_user", "")
        password = cfg.get("smtp_password", "")
        to_addrs = cfg.get("to", [])
        if not all([host, user, password, to_addrs]):
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[BRAIN AGENT] {notif.title}"
        msg["From"] = user
        msg["To"] = ", ".join(to_addrs)

        text_body = (
            f"Event: {notif.event_type}\n"
            f"Severity: {notif.severity}\n"
            f"Agent: {notif.agent or 'N/A'}\n"
            f"Time: {notif.created_at}\n\n"
            f"{notif.message}\n"
        )
        if notif.metadata:
            text_body += f"\nMetadata:\n{json.dumps(notif.metadata, indent=2)}\n"

        msg.attach(MIMEText(text_body, "plain"))

        try:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(user, password)
                server.sendmail(user, to_addrs, msg.as_string())
        except Exception as e:
            logging.warning(f"Email notification failed: {e}")

    def stop(self):
        """Stop the background worker."""
        self._running = False

"""Email anomaly alerts (SMTP).

Edge-triggered + de-duplicated per terminal via a cooldown window so a terminal
that stays anomalous for days doesn't spam the inbox. If SMTP env vars are not
configured, alerts degrade to a log line — the collector still runs fine.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from . import config
from .store import Store

log = logging.getLogger("alerts")


class Alerter:
    def __init__(self, store: Store):
        self.store = store
        self.enabled = bool(config.SMTP_HOST and config.ALERT_TO)
        if not self.enabled:
            log.info("SMTP not configured; anomaly alerts will be logged only.")

    def maybe_alert(self, terminal_id: str, metrics: dict, terminal_name: str) -> None:
        if not metrics.get("anomaly"):
            return
        if self.store.alert_recent(terminal_id, config.ALERT_COOLDOWN_H):
            return
        subject = f"[commodity-monitor] Anomaly at {terminal_name} ({terminal_id})"
        direction = "SPIKE" if (metrics.get("z") or 0) > 0 else "DROP"
        body = (
            f"Activity anomaly detected.\n\n"
            f"Terminal:      {terminal_name} ({terminal_id})\n"
            f"Commodity:     {metrics.get('commodity')}\n"
            f"Direction:     {direction}\n"
            f"z-score:       {metrics.get('z')}\n"
            f"activity_index:{metrics.get('activity_index')}\n"
            f"departures_7d: {metrics.get('departures_7d')}\n"
            f"queue:         {metrics.get('queue')}\n\n"
            f"Reminder: activity is a PROXY, not verified tonnage. Spikes/drops at a "
            f"single-source terminal often track strikes, maintenance, or weather.\n"
        )
        try:
            if self.enabled:
                self._send(subject, body)
            else:
                log.warning("ANOMALY (%s): z=%s idx=%s", terminal_id,
                            metrics.get("z"), metrics.get("activity_index"))
            self.store.mark_alert(terminal_id)
        except Exception:  # never let alerting crash the collector
            log.exception("failed to send alert for %s", terminal_id)

    def _send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = config.SMTP_FROM
        msg["To"] = ", ".join(config.ALERT_TO)
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
        log.info("sent alert email: %s", subject)

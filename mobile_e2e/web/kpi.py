"""Account-level KPI refresh — followers, profile views, clicks, demographics.

These are FIRST-PARTY account insights (``threads_manage_insights``) and work at
Standard Access today. Follower demographics (gender/age) unlock per account once
it passes 100 followers. We snapshot daily so the dashboard can show growth.
"""

from __future__ import annotations

import json
import os

from mobile_e2e.utils.logger import get_logger

LOG = get_logger(__name__)


def _user_id(credentials_file: str) -> str:
    try:
        with open(credentials_file, "r", encoding="utf-8") as f:
            return str(json.load(f).get("user_id") or "")
    except Exception:  # noqa: BLE001
        return ""


def refresh_account_insights(store, days: int = 30) -> dict:
    """Snapshot each account's KPIs + demographics. Best-effort per account."""
    from mobile_e2e.web import threads_client

    done = 0
    for a in store.list_accounts():
        creds = a.get("credentials_file")
        if not creds or not os.path.exists(creds):
            continue
        uid = _user_id(creds)
        if not uid:
            continue
        try:
            ins = threads_client.fetch_account_insights(uid, creds, days=days)
        except Exception as exc:  # noqa: BLE001 - skip this account
            LOG.warning("account insights failed for %s: %s", a.get("handle"), exc)
            continue
        store.snapshot_account_insight(a["id"], **ins)
        # remember which links people click (e.g. the bio Telegram link)
        links = ins.get("click_links") or []
        if links:
            store.set_setting(f"clicklinks:{a['id']}", json.dumps(links[:3]))
        done += 1
        # demographics unlock at 100+ followers — store gender split when available
        if (ins.get("followers") or 0) >= 100:
            demo = threads_client.fetch_follower_demographics(uid, creds, "gender")
            if demo:
                store.set_setting(f"demographics:{a['id']}", json.dumps(demo))
    store.mark_heartbeat("account_insights_last")
    if done:
        store.log("kpi.refresh", f"{done} accounts", None)
    return {"accounts": done}

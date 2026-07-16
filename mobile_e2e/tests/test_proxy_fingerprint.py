"""Tests for per-account proxy + fingerprint anti-correlation (Stage 1).

Covers the store rules (2 accounts/proxy, immutable fingerprint, transport
resolution) and the fail-closed transport guarantee, without needing a live
proxy or the Threads API.
"""

from __future__ import annotations

import asyncio

import pytest

from mobile_e2e.core import fingerprint, http_session
from mobile_e2e.core.exceptions import ProxyRequiredError
from mobile_e2e.web.store import Store


# -- fingerprint ------------------------------------------------------------
def test_fingerprint_generate_has_required_fields():
    fp = fingerprint.generate(seed=1)
    assert fp["user_agent"] and fp["accept_language"]
    assert fp["device_model"] and fp["os"]


def test_fingerprint_seed_is_deterministic():
    assert fingerprint.generate(seed=42) == fingerprint.generate(seed=42)


def test_fingerprint_headers_render():
    fp = {"user_agent": "UA/1", "accept_language": "uk-UA"}
    assert fingerprint.headers(fp) == {"User-Agent": "UA/1", "Accept-Language": "uk-UA"}
    assert fingerprint.headers({}) == {}


def test_fingerprint_loads_tolerates_garbage():
    assert fingerprint.loads("") == {}
    assert fingerprint.loads("not json") == {}


# -- store: proxies ---------------------------------------------------------
def _store():
    return Store(":memory:")


def test_add_proxy_rejects_bad_scheme_and_port():
    s = _store()
    with pytest.raises(ValueError):
        s.add_proxy("ftp", "1.2.3.4", 8080)
    with pytest.raises(ValueError):
        s.add_proxy("http", "1.2.3.4", 70000)


def test_add_proxy_is_idempotent_on_host_port():
    s = _store()
    s.add_proxy("http", "1.2.3.4", 8080, "u", "p")
    s.add_proxy("http", "1.2.3.4", 8080, "u2", "p2")
    assert len(s.list_proxies()) == 1


def test_proxy_url_http_and_socks5():
    s = _store()
    p1 = s.add_proxy("http", "1.2.3.4", 8080, "u", "p")
    p2 = s.add_proxy("socks5", "5.6.7.8", 1080)
    assert s.proxy_url(p1) == "http://u:p@1.2.3.4:8080"
    assert s.proxy_url(p2) == "socks5://5.6.7.8:1080"
    assert s.proxy_url(None) is None


def test_proxy_has_no_limit_by_default():
    # MAX_ACCOUNTS_PER_PROXY defaults to 0 (unlimited) — any number of accounts
    # may share a proxy.
    s = _store()
    p = s.add_proxy("http", "1.2.3.4", 8080)
    for i in range(5):
        s.assign_proxy(s.add_account(name=f"a{i}")["id"], p["id"])
    assert s.list_proxies()[0]["accounts_using"] == 5


def test_proxy_limit_enforced_when_set():
    # When a cap is configured (>0), assigning past it is refused.
    s = _store()
    s.MAX_ACCOUNTS_PER_PROXY = 2
    p = s.add_proxy("http", "1.2.3.4", 8080)
    a1 = s.add_account(name="a1")
    a2 = s.add_account(name="a2")
    a3 = s.add_account(name="a3")
    s.assign_proxy(a1["id"], p["id"])
    s.assign_proxy(a2["id"], p["id"])
    with pytest.raises(ValueError):
        s.assign_proxy(a3["id"], p["id"])
    # reassigning an already-attached account does not count as a 3rd
    assert s.assign_proxy(a1["id"], p["id"])["proxy_id"] == p["id"]


def test_assign_unknown_proxy_raises():
    s = _store()
    a = s.add_account(name="a")
    with pytest.raises(ValueError):
        s.assign_proxy(a["id"], 999)


def test_delete_proxy_unassigns_accounts():
    s = _store()
    p = s.add_proxy("http", "1.2.3.4", 8080)
    a = s.add_account(name="a")
    s.assign_proxy(a["id"], p["id"])
    s.delete_proxy(p["id"])
    assert s.get_account(a["id"])["proxy_id"] is None


# -- store: fingerprint immutability ---------------------------------------
def test_fingerprint_is_generated_once_and_frozen():
    s = _store()
    a = s.add_account(name="a")
    fp1 = s.ensure_fingerprint(a["id"])
    fp2 = s.ensure_fingerprint(a["id"])
    assert fp1 == fp2
    # reloading the account exposes the same fingerprint
    assert s.get_account(a["id"])["fingerprint"] == fp1


# -- store: transport resolution -------------------------------------------
def test_transport_for_assigned_proxy_is_required():
    s = _store()
    p = s.add_proxy("http", "1.2.3.4", 8080, "u", "p")
    a = s.add_account(name="a", credentials_file="c.json")
    s.assign_proxy(a["id"], p["id"])
    url, headers, require = s.transport_for("c.json")
    assert url == "http://u:p@1.2.3.4:8080"
    assert require is True
    assert "User-Agent" in headers


def test_transport_for_no_proxy_direct_unless_enforced():
    s = _store()
    s.add_account(name="a", credentials_file="c.json")
    url, _h, require = s.transport_for("c.json")
    assert url is None and require is False
    s.set_setting("proxy_enforce_all", "1")
    url2, _h2, require2 = s.transport_for("c.json")
    assert url2 is None and require2 is True  # fail-closed: block direct


def test_transport_for_unknown_creds_respects_enforce():
    s = _store()
    assert s.transport_for("nope.json") == (None, {}, False)
    s.set_setting("proxy_enforce_all", "1")
    assert s.transport_for("nope.json")[2] is True


# -- http_session: fail-closed ---------------------------------------------
def test_build_session_requires_proxy_when_missing():
    async def go():
        with pytest.raises(ProxyRequiredError):
            await http_session.build_session(None, {}, require_proxy=True)
    asyncio.run(go())


def test_requests_proxy_env_sets_and_restores(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    with http_session.requests_proxy_env("http://u:p@1.2.3.4:8080"):
        import os
        assert os.environ["HTTPS_PROXY"] == "http://u:p@1.2.3.4:8080"
    import os
    assert "HTTPS_PROXY" not in os.environ


def test_requests_proxy_env_requires_proxy_when_enforced():
    with pytest.raises(ProxyRequiredError):
        with http_session.requests_proxy_env(None, require_proxy=True):
            pass


def test_requests_identity_sets_ua_and_proxy_then_restores(monkeypatch):
    import os
    import requests
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    before_ua = requests.utils.default_user_agent()
    UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) X"
    with http_session.requests_identity("http://u:p@1.2.3.4:8080", UA):
        assert requests.Session().headers["User-Agent"] == UA
        assert os.environ["HTTPS_PROXY"] == "http://u:p@1.2.3.4:8080"
    assert requests.utils.default_user_agent() == before_ua
    assert "HTTPS_PROXY" not in os.environ


def test_requests_identity_without_ua_keeps_default(monkeypatch):
    import requests
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    before = requests.utils.default_user_agent()
    with http_session.requests_identity("http://1.2.3.4:8080", None):
        assert requests.utils.default_user_agent() == before
    assert requests.utils.default_user_agent() == before


def test_probe_dead_proxy_reports_error_without_raising():
    # 10.255.255.1:9 is unroutable — probe must fail-closed, not raise.
    res = http_session.probe("http://10.255.255.1:9", timeout=4)
    assert res["ok"] is False and res["ip"] == "" and res["error"]


# -- store: threads apps (multi-app) ---------------------------------------
def test_add_app_requires_id_and_secret():
    s = _store()
    with pytest.raises(ValueError):
        s.add_app(app_id="", app_secret="x")
    with pytest.raises(ValueError):
        s.add_app(app_id="123", app_secret="")


def test_app_dict_never_exposes_secret_but_store_can_read_it():
    s = _store()
    app = s.add_app(app_id="1752043659154933", app_secret="TOPSECRET",
                    redirect_uri="https://localhost:8443/callback", label="ufo001")
    assert "app_secret" not in app
    assert app["has_secret"] is True
    assert app["app_id_masked"] == "1752…4933"
    assert s.get_app_secret(app["id"]) == "TOPSECRET"


def test_three_accounts_per_app_limit():
    s = _store()
    app = s.add_app(app_id="123", app_secret="x")
    ids = [s.add_account(name=f"a{i}")["id"] for i in range(4)]
    for i in range(3):
        s.assign_app(ids[i], app["id"])
    with pytest.raises(ValueError):
        s.assign_app(ids[3], app["id"])
    assert s.list_apps()[0]["accounts_using"] == 3


def test_add_app_idempotent_on_app_id_updates_secret():
    s = _store()
    s.add_app(app_id="123", app_secret="old")
    s.add_app(app_id="123", app_secret="new")
    apps = s.list_apps()
    assert len(apps) == 1
    assert s.get_app_secret(apps[0]["id"]) == "new"


def test_delete_app_unassigns_accounts():
    s = _store()
    app = s.add_app(app_id="123", app_secret="x")
    a = s.add_account(name="a")
    s.assign_app(a["id"], app["id"])
    s.delete_app(app["id"])
    assert s.get_account(a["id"])["app_ref"] is None


def test_account_carries_resolved_app():
    s = _store()
    app = s.add_app(app_id="123", app_secret="x", label="app#2")
    a = s.add_account(name="a")
    s.assign_app(a["id"], app["id"])
    assert s.get_account(a["id"])["app"]["label"] == "app#2"

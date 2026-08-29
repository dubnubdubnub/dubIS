"""The distributor paths that need a real browser, run against a real browser.

WHAT THIS TIER IS FOR
`mouser_client`'s keyless path is the only way dubIS reads a per-carrier price
ladder -- the Search API publishes one flat list of breaks and no MouseReel
fee, while `table.pricing-table` on the product page names each carrier and
gives it its own ladder. Until now that path could not be tested anywhere
automated: pytest has no pywebview loop, so `browser_page.available()` was
false and every keyless fetch fell through to the urllib scrape Mouser blocks.
The tests below were, in practice, untestable code.

The CDP backend removes that. Point `DUBIS_CDP_URL` at a Chrome that is already
running -- the cluster's shared one -- and the same `BrowserPage` renders the
page from a machine that has no GUI at all, which is what lets CI exercise this
instead of only Isaac's desktop.

WHY A SEPARATE MARKER
Every test here is `live` (it hits mouser.com) *and* `browser` (it needs that
endpoint). It deliberately is NOT `credentials`: nothing here reads a secret,
which is the entire reason CI can run it. `-m "live and not credentials"` is
therefore the CI selection and `-m browser` is "just the shared-browser ones".

WHY AN UNSET DUBIS_CDP_URL IS A FAILURE, NOT A SKIP
The marker is the opt-in. Nothing selects these tests unless someone asked for
them by name, so by the time one runs, the operator has already said they want
a real browser fetch -- and answering that with a green skip would report
success for work that never happened. Project policy bans `pytest.skip` for a
missing dependency for exactly this reason, so the missing endpoint is a
`pytest.fail` naming the variable and the in-cluster URL to put in it.

DO NOT POINT THIS AT A HEADLESS CHROME YOU JUST STARTED
Measured, not guessed: a fresh `--headless=new` Chrome gets `example.com` fine
and gets nothing at all from Mouser -- a search page with no product links, and
a product page that fails the parse as a bot block. Mouser is reading the
browser, not the address. The endpoint tests below will pass and every Mouser
one will fail, which looks like a code regression and is not. The target is a
long-lived headful browser whose profile also sees ordinary human traffic;
that is the whole argument for borrowing the shared one rather than launching
a private browser next to the runner.

BEING A GUEST
That browser is shared, and its profile holds live logged-in sessions. The
etiquette `browser_page` implements applies here too and one of the tests below
enforces the visible half of it: pages this process opens are closed again, so
nobody comes back to a window full of Mouser tabs. Pacing goes through
`browser_page`'s own throttle, which draws from `human_pause`, rather than a
constant this file would invent -- and the whole module shares ONE product
fetch, because two tests wanting the same page is not a reason to load it twice.
"""

from __future__ import annotations

import os
import uuid

import pytest

import browser_page
import mouser_client
from mouser_client import MouserClient

# Every test in this module needs both a network and that browser.
pytestmark = [pytest.mark.live, pytest.mark.browser]

# A real MPN, not a Mouser part number, so the fetch goes through the search
# page and follows the first result. That is the longer of the two routes in
# `_fetch_via_browser`, the one a BOM actually exercises (the Mouser column in
# a purchase ledger is usually an MPN), and -- measured from inside the cluster
# -- the only one that works: a direct /ProductDetail/<mouser-pn> from there
# comes back "Access to this page has been denied." while the same part reached
# through search renders fine. `_fetch_via_browser` tries both and takes
# whichever answers, which is what makes that survivable; this test picks the
# identifier shape that puts the working route first rather than asserting on
# a deny page whose appearance depends on where the browser is standing.
MOUSER_MPN = "LM358DR"

# Median seconds between navigations when the operator has not chosen one.
# Jittered log-normally by `browser_page.human_pause`; see its docstring for
# why a constant is the wrong shape.
DEFAULT_PACE_MEDIAN_S = 6.0

# Something tiny, stable and bot-protection-free, purely to answer "is the
# endpoint wired up at all" separately from "did Mouser cooperate today".
# The nonce is what makes the URL *ours*: this browser is shared, somebody may
# genuinely have example.com open, and a leftover-page check that cannot tell
# their tab from ours would fail on their browsing habits. example.com ignores
# the query string, so it costs nothing.
PROBE_NONCE = uuid.uuid4().hex
PROBE_URL = f"https://example.com/?dubis-live-test={PROBE_NONCE}"
PROBE_MARKER = "Example Domain"


@pytest.fixture(scope="module")
def shared_browser():
    """A `BrowserPage` bound to the configured shared browser.

    Fails -- loudly, not silently -- when `DUBIS_CDP_URL` is unset, because a
    selected browser test that quietly passes without a browser is worse than
    no test at all.
    """
    endpoint = browser_page.cdp_endpoint()
    if endpoint is None:
        pytest.fail(
            f"{browser_page.CDP_ENV} is not set, so there is no browser to "
            "borrow and these tests cannot prove anything. Set it to a Chrome "
            "DevTools endpoint -- in the cluster that is "
            "http://browser-x.browser.svc.cluster.local:9222, and reaching it "
            "requires the calling namespace to carry the label "
            "browser-client=enabled."
        )

    # Pace ourselves against a browser other people are using. Set only as a
    # default: an operator who already chose a cadence keeps it.
    previous = os.environ.get(browser_page.CDP_PAUSE_ENV)
    if not previous:
        os.environ[browser_page.CDP_PAUSE_ENV] = str(DEFAULT_PACE_MEDIAN_S)

    page = browser_page.BrowserPage(title="dubIS live test")
    try:
        yield page
    finally:
        # Detaches this CDP client; the browser itself is not ours to stop.
        page.destroy()
        if previous is None:
            os.environ.pop(browser_page.CDP_PAUSE_ENV, None)


@pytest.fixture(scope="module")
def keyless_mouser_product(shared_browser, tmp_path_factory):
    """One real, keyless Mouser fetch, shared by every assertion below.

    `credentials_file` points into a tmp dir that has no credentials in it, so
    `get_api_key()` is None and `_fetch_raw` takes the browser path -- the same
    routing the app takes, rather than the private method, so a regression in
    the routing fails here too.
    """
    data_dir = tmp_path_factory.mktemp("mouser-keyless")
    client = MouserClient(credentials_file=str(data_dir / "mouser_credentials.json"))
    assert client.get_api_key() is None, (
        "this tier is meaningless with a key configured: the API path would "
        "answer and the browser would never be touched"
    )
    # Hand it the module's page rather than letting it open a second CDP
    # connection to the same browser: one attachment, one shared throttle.
    client._page = shared_browser
    product = client._fetch_raw(MOUSER_MPN)
    if product is None:
        pytest.fail(
            f"a keyless fetch of {MOUSER_MPN} through {browser_page.cdp_endpoint()} "
            "returned nothing. Either the shared browser is unreachable (check "
            "the browser-client=enabled label on this namespace), or Mouser "
            "served a challenge page, or the product page changed shape -- the "
            "warnings logged by browser_page/mouser_client say which."
        )
    return product


class TestTheEndpointItself:
    """Separate from Mouser, so a red run says which half broke."""

    def test_the_shared_browser_renders_a_page(self, shared_browser):
        html = shared_browser.fetch_html(PROBE_URL)
        assert html is not None, (
            f"{browser_page.CDP_ENV}={browser_page.cdp_endpoint()} did not "
            "render a trivial page, so nothing further here can work"
        )
        assert PROBE_MARKER in html

    def test_it_leaves_no_page_behind_in_the_shared_profile(self, shared_browser):
        """A guest closes the windows it opened.

        Asserted by looking for OUR nonce rather than by counting pages: a
        human may well be using this browser at the same time, and their tabs
        opening or closing mid-test is not a failure of ours.
        """
        shared_browser.fetch_html(PROBE_URL)
        browser = shared_browser._browser
        assert browser is not None, "the fetch should have left a connection open"
        left_behind = [
            page.url for page in browser.contexts[0].pages
            if PROBE_NONCE in page.url
        ]
        assert not left_behind, (
            f"pages we opened are still in the shared profile: {left_behind}"
        )


class TestKeylessMouser:
    """The path that had no automated coverage anywhere before this."""

    def test_a_keyless_client_prices_a_part(self, keyless_mouser_product):
        product = keyless_mouser_product
        assert product["provider"] == "mouser"
        assert product["mpn"] or product["productCode"]
        assert isinstance(product["stock"], int)
        assert product["prices"], "a stocked part with no price breaks is a parse failure"
        for tier in product["prices"]:
            assert isinstance(tier["qty"], int) and tier["qty"] > 0
            assert isinstance(tier["price"], (int, float)) and tier["price"] > 0

    def test_the_per_carrier_ladders_come_back(self, keyless_mouser_product):
        """`packagings` is the reason this path exists; the API has no equivalent."""
        packagings = keyless_mouser_product["packagings"]
        assert packagings, (
            "no per-carrier ladders -- table.pricing-table was absent or its "
            "markup changed, which is exactly the drift this tier exists to catch"
        )
        for entry in packagings:
            assert entry["name"], "a carrier with no name cannot be grouped on"
            assert entry["prices"], f"carrier {entry['name']!r} has an empty ladder"

    def test_the_headline_prices_came_from_that_table(self, keyless_mouser_product):
        """Not from the loose page-wide `qty $price` regex, which cannot
        attribute a break to a carrier. `_parse_product_page` promotes the
        first (Mouser-default) carrier's ladder when the table parsed, so the
        two being equal is the observable proof the table won."""
        product = keyless_mouser_product
        assert product["prices"] == product["packagings"][0]["prices"]

    def test_the_page_the_parser_saw_is_the_page_mouser_serves(
        self, keyless_mouser_product,
    ):
        """A bot-block page has a title too; `_parse_product_page` rejects it by
        title, so reaching here at all means the block regex did not fire. Check
        the landed URL as well, so a fetch that quietly ended up somewhere other
        than a product page is not read as a success."""
        assert "mouser.com" in keyless_mouser_product["mouserUrl"].lower()
        assert not mouser_client.MouserClient._BOT_BLOCK_TITLE_RE.search(
            keyless_mouser_product["title"]
        )

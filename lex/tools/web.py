"""Web tools with source discipline: keyless search, ranked, plus fetch-as-text.

A raw DuckDuckGo list treats a SEBI filing and a stock-tip blog as equals. The
research passes are told to weigh evidence, so they need to know which is
which — every result carries its domain, a source tier and any date we can
recover.
"""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

from lex.tools.common import _ok, _err

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (lex-agent)"}
_MAX_CHARS = 8000

# tier -> (rank, domains). Lower rank sorts first. Rank is also what the model
# sees, so it can say "this is a forum claim, not a filing" without guessing.
_TIERS = {
    "primary": (0, (
        "nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in",
        "mca.gov.in", "irdai.gov.in", "trai.gov.in", "pib.gov.in",
        "screener.in", "annualreports.com")),
    "press": (1, (
        "moneycontrol.com", "economictimes.indiatimes.com", "livemint.com",
        "business-standard.com", "thehindubusinessline.com",
        "financialexpress.com", "ndtvprofit.com", "cnbctv18.com",
        "bloomberg.com", "reuters.com", "ft.com", "business-today.in",
        "bqprime.com", "zeebiz.com", "thehindu.com")),
    "aggregator": (3, (
        "reddit.com", "quora.com", "tradingview.com", "stocktwits.com",
        "medium.com", "blogspot.com", "wordpress.com", "facebook.com",
        "x.com", "twitter.com", "youtube.com", "telegram.me", "t.me")),
}
_DEFAULT_TIER = ("other", 2)  # everything unrecognised sits between press and forums

# DDGS takes a coarse time bucket, not a day count.
_TIME_LIMITS = ((1, "d"), (7, "w"), (31, "m"), (366, "y"))

_DATE_IN_URL = re.compile(r"/(20\d{2})[/-](\d{1,2})(?:[/-](\d{1,2}))?")
_DATE_IN_TEXT = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2}"
    r"|20\d{2}-\d{2}-\d{2})\b")


def _ddgs():
    from ddgs import DDGS
    return DDGS()


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def classify_source(url: str) -> tuple[str, int]:
    """(tier, rank) for a URL — matches on domain suffix so subdomains count."""
    host = domain_of(url)
    if not host:
        return _DEFAULT_TIER
    for tier, (rank, domains) in _TIERS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return tier, rank
    return _DEFAULT_TIER


def _date_hint(result: dict) -> str | None:
    """Best-effort publication date: the feed's own field, the URL, then the text."""
    for key in ("date", "published", "publishedDate"):
        if result.get(key):
            return str(result[key])
    url_match = _DATE_IN_URL.search(str(result.get("href") or result.get("url") or ""))
    if url_match:
        year, month, day = url_match.groups()
        return f"{year}-{int(month):02d}" + (f"-{int(day):02d}" if day else "")
    text_match = _DATE_IN_TEXT.search(str(result.get("body") or ""))
    return text_match.group(1) if text_match else None


def _time_limit(days) -> str | None:
    if not days:
        return None
    try:
        days = int(days)
    except (TypeError, ValueError):
        return None
    for threshold, bucket in _TIME_LIMITS:
        if days <= threshold:
            return bucket
    return None


def rank_results(results: list) -> list:
    """Annotate with domain/tier/date and sort primary sources first.

    Sort is stable, so within a tier DuckDuckGo's own relevance order survives.
    """
    annotated = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        url = str(result.get("href") or result.get("url") or "")
        tier, rank = classify_source(url)
        annotated.append({**result, "domain": domain_of(url), "source_tier": tier,
                          "source_rank": rank, "date_hint": _date_hint(result)})
    return sorted(annotated, key=lambda r: r["source_rank"])


def handle_web_search(args: dict) -> str:
    try:
        n = int(args.get("limit", 5))
        limit = _time_limit(args.get("days"))
        query = args["query"]
        try:
            raw = _ddgs().text(query, max_results=n, timelimit=limit) if limit \
                else _ddgs().text(query, max_results=n)
        except TypeError:  # older ddgs without timelimit — recency is a nicety
            logger.warning("ddgs rejected timelimit; retrying without it")
            raw = _ddgs().text(query, max_results=n)
        return _ok(rank_results(raw))
    except Exception as e:
        return _err(e)


class _Text(HTMLParser):
    _SKIP = {"script", "style", "noscript"}
    _DATE_META = {"article:published_time", "og:published_time",
                  "publishdate", "date", "dc.date", "pubdate"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0
        self.published = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "meta" and self.published is None:
            attrs = dict(attrs)
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in self._DATE_META and attrs.get("content"):
                self.published = attrs["content"]
        elif tag == "time" and self.published is None:
            datetime_attr = dict(attrs).get("datetime")
            if datetime_attr:
                self.published = datetime_attr

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def handle_web_fetch(args: dict) -> str:
    try:
        url = args["url"]
        r = requests.get(url, headers=_UA, timeout=15)
        r.raise_for_status()
        p = _Text()
        p.feed(r.text)
        text = " ".join(p.parts)
        tier, rank = classify_source(url)
        return _ok({"url": url, "domain": domain_of(url), "source_tier": tier,
                    "source_rank": rank, "published": p.published,
                    "truncated": len(text) > _MAX_CHARS,
                    "text": text[:_MAX_CHARS]})
    except Exception as e:
        return _err(e)

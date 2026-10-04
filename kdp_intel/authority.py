"""How much a source counts as evidence, decided by its domain.

Tier 1 is the State and the EU (laws, official statistics, agency guidance).
Tier 2 is professional and consumer press. Tier 3 is everything else,
including forums and reviews: good for the reader's language and pain, never
for a number or a legal rule.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .models import SourceKind

_LAW = {"gazzettaufficiale.it", "normattiva.it", "eur-lex.europa.eu"}
_OFFICIAL = {
    "agenziaentrate.gov.it", "inps.it", "istat.it", "bancaditalia.it", "covip.it",
    "camera.it", "senato.it", "parlamento.it", "governo.it", "lavoro.gov.it",
    "mef.gov.it", "notariato.it", "europa.eu", "agid.gov.it", "acn.gov.it",
    "garanteprivacy.it", "consob.it", "ivass.it", "salute.gov.it",
}
_PRESS = {
    "ilsole24ore.com", "fiscooggi.it", "ipsoa.it", "pmi.it", "altroconsumo.it",
    "money.it", "investireoggi.it", "ecnews.it", "diritto.it", "brocardi.it",
    "assolombarda.it", "italiaoggi.it", "corriere.it", "repubblica.it",
    "agendadigitale.eu", "informazionefiscale.it", "fiscoetasse.com", "ansa.it",
    "lavoripubblici.it", "orizzontescuola.it", "quotidianopiu.it",
}
_COMMUNITY = {"reddit.com", "quora.com", "facebook.com", "forumfree.it"}
_REVIEW = {"amazon.it", "amazon.com", "goodreads.com"}


def domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, names: set[str]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def classify(url: str) -> tuple[SourceKind, int]:
    host = domain(url)
    if _matches(host, _LAW):
        return "law", 1
    if _matches(host, _OFFICIAL) or host.endswith(".gov.it"):
        return "official", 1
    if _matches(host, _PRESS):
        return "press", 2
    if _matches(host, _COMMUNITY):
        return "community", 3
    if _matches(host, _REVIEW):
        return "review", 3
    return "other", 3

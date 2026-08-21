"""Single normalized-product factory shared by all distributor clients/normalizers.

Every distributor client/normalizer (`lcsc_client.py`, `mouser_client.py`,
`pololu_client.py`, `digikey_normalizer.py`) hand-built the same ~16-key
normalized product dict. This module is the single place that assembles it,
so the shapes can't drift again.

The per-provider URL key (`lcscUrl` / `digikeyUrl` / `mouserUrl` / `pololuUrl`)
is derived from `provider` as `f"{provider}Url"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.packaging import carrier_of, is_reel


@dataclass
class NormalizedProduct:
    """The normalized product shape every distributor client emits."""

    product_code: str
    title: str
    manufacturer: str
    mpn: str
    package: str
    description: str
    stock: int
    prices: list[dict[str, Any]]
    provider: str
    provider_url_key: str
    image_url: str = ""
    pdf_url: str = ""
    url: str = ""
    category: str | None = None
    subcategory: str | None = None
    attributes: list[dict[str, str]] = field(default_factory=list)
    # Per-packaging price ladders, when the distributor publishes them. Each
    # entry: {"name", "partNumber", "prices": [{"qty", "price"}], "carrier",
    # "isReel"}. Empty when the distributor only exposes a single ladder.
    packagings: list[dict[str, Any]] = field(default_factory=list)
    # Factory reel/package quantity — the multiple a whole reel is sold in.
    # None when unknown; 0 is never meaningful and is normalized to None.
    reel_qty: int | None = None
    # Surcharge for custom-reeling a non-whole-reel quantity (LCSC reelPrice,
    # DigiKey Digi-Reel, Mouser MouseReel). None when the distributor does not
    # offer or does not publish one.
    reel_fee: float | None = None
    debug: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "productCode": self.product_code,
            "title": self.title,
            "manufacturer": self.manufacturer,
            "mpn": self.mpn,
            "package": self.package,
            "description": self.description,
            "stock": self.stock,
            "prices": self.prices,
            "imageUrl": self.image_url,
            "pdfUrl": self.pdf_url,
            self.provider_url_key: self.url,
            "category": self.category,
            "subcategory": self.subcategory,
            "attributes": self.attributes,
            "packagings": self.packagings,
            "reelQty": self.reel_qty,
            "reelFee": self.reel_fee,
            "provider": self.provider,
            "_debug": self.debug,
        }


def _clean_reel_qty(value: Any) -> int | None:
    """Coerce a scraped reel quantity to a positive int, else None.

    Distributors variously report this as "3,000", 3000.0, "" or 0; a zero or
    unparseable value means "not published", which is None, not 0 — a 0 would
    read downstream as "reels of zero parts".
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return None
    try:
        qty = int(float(value))
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def _clean_reel_fee(value: Any) -> float | None:
    """Coerce a reeling surcharge to a positive float, else None.

    LCSC reports 0 for parts it will not custom-reel; 0 and None mean the same
    thing to a caller pricing a reel option, so both collapse to None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        if not value:
            return None
    try:
        fee = float(value)
    except (TypeError, ValueError):
        return None
    return fee if fee > 0 else None


def annotate_packagings(
    packagings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Tag each packaging entry with its normalized carrier and reel-ness.

    Clients supply the vendor's own ``name``; the ``carrier``/``isReel`` keys
    are derived here so every distributor agrees on the vocabulary (see
    domain/packaging.py). Entries already carrying the keys are left alone so
    a client with better information than the name string can override.
    """
    if not packagings:
        return []
    out: list[dict[str, Any]] = []
    for entry in packagings:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        name = item.get("name")
        item.setdefault("carrier", carrier_of(name))
        item.setdefault("isReel", is_reel(name))
        out.append(item)
    return out


def build_product(
    *,
    product_code: str,
    title: str,
    manufacturer: str,
    mpn: str,
    package: str,
    description: str,
    stock: int,
    prices: list[dict[str, Any]],
    provider: str,
    image_url: str | None = None,
    pdf_url: str | None = None,
    url: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    attributes: list[dict[str, str]] | None = None,
    packagings: list[dict[str, Any]] | None = None,
    reel_qty: int | None = None,
    reel_fee: float | None = None,
    debug: Any = None,
    url_key: str | None = None,
) -> dict[str, Any]:
    """Assemble the normalized product dict emitted by every distributor.

    ``url_key`` overrides the derived ``<provider>Url`` key name; normally
    omitted since ``provider`` (``"lcsc"``, ``"digikey"``, ``"mouser"``,
    ``"pololu"``) already determines it.
    """
    product = NormalizedProduct(
        product_code=product_code,
        title=title,
        manufacturer=manufacturer,
        mpn=mpn,
        package=package,
        description=description,
        stock=stock,
        prices=prices,
        provider=provider,
        provider_url_key=url_key or f"{provider}Url",
        image_url=image_url if image_url is not None else "",
        pdf_url=pdf_url if pdf_url is not None else "",
        url=url if url is not None else "",
        category=category,
        subcategory=subcategory,
        attributes=attributes if attributes is not None else [],
        packagings=annotate_packagings(packagings),
        reel_qty=_clean_reel_qty(reel_qty),
        reel_fee=_clean_reel_fee(reel_fee),
        debug=debug,
    )
    return product.to_dict()

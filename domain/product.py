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
            "provider": self.provider,
            "_debug": self.debug,
        }


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
        debug=debug,
    )
    return product.to_dict()

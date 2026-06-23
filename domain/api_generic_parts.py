"""Generic parts facade — create/manage generic parts and saved searches."""

from __future__ import annotations

from typing import Any

import domain.generic_parts


class GenericPartsFacade:
    def __init__(self, api) -> None:
        self._api = api

    def create_generic_part(self, name: str, part_type: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return domain.generic_parts.create_generic_part_api(
            self._api._get_cache(), self._api.events_dir, name, part_type, spec_json, strictness_json,
        )

    def resolve_bom_spec(self, part_type: str, value: float,
                          package: str) -> dict[str, Any] | None:
        return domain.generic_parts.resolve_bom_spec(self._api._get_cache(), part_type, float(value), package)

    def list_generic_parts(self) -> list[dict[str, Any]]:
        return domain.generic_parts.list_generic_parts_with_member_specs(self._api._get_cache())

    def add_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return domain.generic_parts.add_member_api(
            self._api._get_cache(), self._api.events_dir, generic_part_id, part_id,
        )

    def remove_generic_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return domain.generic_parts.remove_member_api(
            self._api._get_cache(), self._api.events_dir, generic_part_id, part_id,
        )

    def exclude_generic_member(self, generic_part_id: str, part_id: str) -> None:
        return domain.generic_parts.exclude_member(
            self._api._get_cache(), self._api.events_dir, generic_part_id, part_id,
        )

    def set_preferred_member(self, generic_part_id: str, part_id: str) -> list[dict[str, Any]]:
        return domain.generic_parts.set_preferred_api(
            self._api._get_cache(), self._api.events_dir, generic_part_id, part_id,
        )

    def update_generic_part(self, generic_part_id: str, name: str,
                             spec_json: str, strictness_json: str) -> dict[str, Any]:
        return domain.generic_parts.update_generic_part_api(
            self._api._get_cache(), self._api.events_dir, generic_part_id, name,
            spec_json, strictness_json,
        )

    def extract_spec(self, part_key: str) -> dict[str, Any]:
        return domain.generic_parts.extract_spec_for_part(self._api._get_cache(), part_key)

    def extract_spec_from_value(self, part_type: str, value_str: str, package_str: str) -> dict[str, Any]:
        import spec_extractor
        desc = part_type + " " + value_str + " " + package_str
        spec = spec_extractor.extract_spec(desc, package_str)
        spec["type"] = part_type
        return spec

    def list_saved_searches(self, generic_part_id: str) -> list[dict[str, Any]]:
        import saved_searches
        return saved_searches.list_for_group(self._api._get_cache(), generic_part_id)

    def create_saved_search(self, generic_part_id: str, name: str,
                            tag_state_json: str, search_text: str,
                            frozen_members_json: str) -> dict[str, Any]:
        import json

        import saved_searches
        tag_state = json.loads(tag_state_json) if isinstance(tag_state_json, str) else tag_state_json
        frozen = json.loads(frozen_members_json) if isinstance(frozen_members_json, str) else frozen_members_json
        return saved_searches.create(
            self._api._get_cache(), self._api.base_dir, generic_part_id, name,
            tag_state, search_text, frozen)

    def delete_saved_search(self, search_id: str) -> None:
        import saved_searches
        saved_searches.delete(self._api._get_cache(), self._api.base_dir, search_id)

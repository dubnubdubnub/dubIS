"""FileIO facade — file dialogs and column detection."""

from __future__ import annotations

from typing import Any

import csv_io
import file_dialogs


class FileIOFacade:
    def __init__(self, api) -> None:
        self._api = api

    def detect_columns(self, headers_json: str | list[str]) -> dict[str, str]:
        """Auto-detect column mapping for purchase CSV import."""
        return file_dialogs.detect_columns(headers_json)

    def save_file_dialog(self, content: str, default_name: str = "export.csv",
                         default_dir: str | None = None,
                         links_json: str | list | None = None) -> dict[str, str] | None:
        """Open native Save As dialog and write content to the chosen path."""
        return file_dialogs.save_file_dialog(content, default_name, default_dir, links_json)

    def convert_xls_to_csv(self, path: str) -> dict[str, Any] | None:
        """Convert a binary XLS file to CSV text for the import panel."""
        return csv_io.convert_xls_to_csv(path)

    def open_file_dialog(self, title: str = "Select CSV file",
                         default_dir: str | None = None) -> dict[str, Any] | None:
        """Open native file dialog, return {name, content, directory, path} or None."""
        return file_dialogs.open_file_dialog(title, default_dir)

    def load_file(self, path: str) -> dict[str, Any] | None:
        """Load a file by path, return {name, content, directory, path, links?} or None."""
        return file_dialogs.load_file(path)

"""Baseline tests for the package bootstrap."""

from importlib import import_module

import pytest

import effectrecon


def test_package_imports() -> None:
    assert effectrecon.__name__ == "effectrecon"


@pytest.mark.parametrize(
    "module_name",
    [
        "_canonical",
        "identity",
        "unknown",
        "evidence",
        "outcomes",
        "reconciler",
        "retry",
        "errors",
        "testing",
    ],
)
def test_placeholder_modules_import(module_name: str) -> None:
    assert import_module(f"effectrecon.{module_name}") is not None

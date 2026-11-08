"""#5056: no provider-specific inventory refresh facades in Core."""
import ast
from pathlib import Path

from cifereca.core import source_refresh_policy


def test_source_refresh_exposes_only_generic_eligibility():
    assert callable(source_refresh_policy.inventory_source_eligible)
    assert not hasattr(source_refresh_policy, "kobo_inventory_source_eligible")
    assert not hasattr(source_refresh_policy, "numilog_inventory_source_eligible")


def test_source_refresh_defines_no_concrete_provider_dispatch():
    source = Path(source_refresh_policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"kobo", "numilog", "bnf-pret", "paris"}
    compared = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            compared.update(
                value.value for value in (node.left, *node.comparators)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                and value.value in forbidden
            )
    assert not compared

"""P1 #4281: Loans GTK must not scan SQLite once per displayed loan.

Execute the actual _render_loans AST without gi or real providers. The
synthetic catalogue counts local reads; no p95 or network speed is claimed.
"""
from __future__ import annotations

import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import history_language_projection
from cifereca.gui.loan_view import (
    LOAN_FILTER_LABELS, loan_context_label, loan_due_presentation,
    loan_medium_counts, loan_row_metadata,
)
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.models import Loan
from cifereca.core.loan_cover_fallback import loan_local_cover


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


class Row:
    def __init__(self, key, title, subtitle, metadata, badge, cover, *,
                 badge_style=None):
        self.key = key
        self.title = title


def _renderer():
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in module.body
              if isinstance(node, ast.FunctionDef) and node.name == "_render_loans")
    display_helper = next(node for node in module.body
                          if isinstance(node, ast.FunctionDef) and node.name == "_loan_display_cover")
    namespace = {
        "Loan": Loan,
        "copy": copy,
        "loan_local_cover": loan_local_cover,
        "BookListItem": Row,
        "loan_source_key": loan_source_key,
        "loan_due_presentation": loan_due_presentation,
        "loan_row_metadata": loan_row_metadata,
        "loan_medium_counts": loan_medium_counts,
        "loan_context_label": loan_context_label,
        "LOAN_FILTER_LABELS": LOAN_FILTER_LABELS,
        "history_language_projection": history_language_projection,
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[display_helper, fn], type_ignores=[]),
    ), str(SOURCE), "exec"), namespace)
    return namespace["_render_loans"]


def _loan(i, *, identity="identity-A", provider="paris",
          medium="digital", raw=None, record=None):
    return Loan(
        id=f"loan-{i}", record_id=record or f"edition-{i}",
        title=f"Synthetic loan {i}", provider="DILICOM",
        medium=medium, identity_id=identity,
        source_provider=provider, source_credential="card-A",
        source_account_id="reader-A", raw=raw or {},
    )


def _host(loans, *, languages=(), medium="all", catalog=()):
    reads, views, captions, facets = [], [], [], []
    storage = NS(catalog_entries=lambda: (
        reads.append("catalog_entries") or list(catalog)
    ))
    host = NS(
        service=NS(
            current_identity=NS(identity_id="identity-A"),
            last_loans=list(loans), storage=storage,
        ),
        loan_filter=medium,
        loan_languages=NS(values=lambda: list(languages)),
        loan_objects={loan_source_key(x): x for x in loans},
        loan_page=NS(show_rows=lambda rows, **kwargs:
                     views.append((list(rows), kwargs))),
        loan_scope_count=NS(set_label=captions.append),
        _known_item_languages=lambda *_: (_ for _ in ()).throw(
            AssertionError("per-loan catalogue query is forbidden")
        ),
        _attach_row_facets=lambda *args: facets.append(args),
        search_controls=None,
    )
    return host, reads, views, captions, facets


@pytest.mark.parametrize("count", [0, 1, 4, 20])
def test_unfiltered_loans_render_without_catalogue_read(count):
    host, reads, views, captions, _facets = _host(
        [_loan(i) for i in range(count)] + [_loan(999, identity="identity-B")]
    )
    _renderer()(host)
    assert reads == []
    assert [row.title for row in views[-1][0]] == [
        f"Synthetic loan {i}" for i in range(count)
    ]
    assert views[-1][1]["total_count"] == count
    assert len(captions) == 1


@pytest.mark.parametrize("count", [0, 1, 4, 20])
def test_filtered_loans_use_one_catalogue_snapshot_not_one_per_loan(count):
    loans = [_loan(i) for i in range(count)]
    catalog = [
        {"provider_id": "paris", "item_id": f"edition-{i}",
         "metadata": {"language": "fr" if i % 2 == 0 else "de"}}
        for i in range(count)
    ]
    host, reads, views, _captions, _facets = _host(
        loans, languages=("fr",), catalog=catalog,
    )
    _renderer()(host)
    assert reads == (["catalog_entries"] if count else [])
    assert {row.title for row in views[-1][0]} == {
        f"Synthetic loan {i}" for i in range(count) if i % 2 == 0
    }


def test_loan_language_uses_direct_metadata_and_exact_provider_record():
    paris_catalog = [
        {"provider_id": "paris", "item_id": "edition-0",
         "metadata": {"languages": ["de"]}},
        {"provider_id": "bnf-pret", "item_id": "edition-1",
         "metadata": {"language": "fr"}},
        {"provider_id": "paris", "item_id": "edition-3",
         "metadata": {"language": "fr"}},
    ]
    loans = [
        _loan(0, raw={"language": "fr"}),  # direct loan evidence
        _loan(1),  # only foreign provider's matching record: excluded
        _loan(2, raw={"language": "fr", "_cifereca": {"language": "de"}}),
        _loan(3),  # catalogue match: included
        _loan(4, identity="identity-B", raw={"language": "fr"}),
    ]
    host, reads, views, _captions, _facets = _host(
        loans, languages=("fr",), catalog=paris_catalog,
    )
    _renderer()(host)
    assert reads == ["catalog_entries"]
    assert {row.title for row in views[-1][0]} == {
        "Synthetic loan 0", "Synthetic loan 3",
    }


def test_medium_filter_excludes_all_without_loading_catalogue():
    host, reads, views, _captions, _facets = _host(
        [_loan(i, medium="physical") for i in range(20)],
        medium="digital", languages=("fr",),
    )
    _renderer()(host)
    assert reads == []
    assert views[-1][0] == []


def test_filtered_rerender_reflects_new_local_catalogue_evidence():
    catalog = [{"provider_id": "paris", "item_id": "edition-0",
                "metadata": {"language": "de"}}]
    host, reads, views, _captions, _facets = _host(
        [_loan(0)], languages=("fr",), catalog=catalog,
    )
    render = _renderer()
    render(host)
    assert views[-1][0] == []
    catalog[0]["metadata"] = {"language": "fr"}
    render(host)
    assert [row.title for row in views[-1][0]] == ["Synthetic loan 0"]
    assert reads == ["catalog_entries", "catalog_entries"]


def test_pure_batch_projection_matches_prior_per_row_lookup():
    loans = [
        _loan(0, raw={"language": "fr"}),
        _loan(1, raw={"_cifereca": {"languages": ["de", "fr"]}}),
        _loan(2, provider="bnf-pret"),
        _loan(3, raw={"language": "es"}),
    ]
    catalog = [
        {"provider_id": "paris", "item_id": "edition-2",
         "metadata": {"language": "fr"}},
        {"provider_id": "bnf-pret", "item_id": "edition-2",
         "metadata": {"language": "nl"}},
        {"provider_id": "paris", "item_id": "edition-3",
         "metadata": {"languages": ["de", "es"]}},
    ]
    wanted = {"fr", "nl"}
    expected = []
    for loan in loans:
        raw = dict(loan.raw or {})
        meta = (
            dict(raw.get("_cifereca") or {})
            if isinstance(raw.get("_cifereca"), dict) else raw
        )
        languages = history_language_projection.item_languages(
            loan.source_provider, loan.record_id or loan.id, meta,
            catalog_rows=catalog,
        )
        if languages.intersection(wanted):
            expected.append(loan.id)
    result = history_language_projection.loan_rows_with_languages(
        loans, wanted, catalog,
    )
    assert [loan.id for loan in result] == expected

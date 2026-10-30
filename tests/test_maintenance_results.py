"""Maintenance results remain safe and revocable without the retired reconciliation UI."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_result_view import result_text
from cifereca.plugin_api import MaintenanceResult
from tests.test_maintenance_cache_row_callbacks import mounted, execute, flush


def test_details_are_additive_and_facts_are_never_stringified():
    class Private:
        def __str__(self):
            raise AssertionError('private execution facts rendered')
    result = MaintenanceResult(True, summary='Analyse terminée',
                               details=('Fichiers observés : 524',),
                               warnings=('Un ISBN ne prouve pas un droit.',),
                               facts={'secret': Private()})
    assert result_text(result) == ('Analyse terminée\n\nRésultats :\n• Fichiers observés : 524'
                                   '\n\nAvertissements et limites :\n• Un ISBN ne prouve pas un droit.')
    assert MaintenanceResult(True).details == ()


def test_malformed_and_oversized_plugin_output_is_bounded():
    result = NS(summary=object(), details=['x' * 10000] * 1000, warnings=object())
    text = result_text(result)
    assert text.startswith('Outil terminé.')
    assert len(text) < 140000
    assert 'object at' not in text


@pytest.mark.parametrize("ok", [True, False])
def test_generic_maintenance_result_is_consultable_without_private_facts(ok):
    m = mounted("covers.audit")
    args = execute(m)
    args[2](MaintenanceResult(
        ok, summary="Bilan local", details=("Fichiers vérifiés : 3",),
        warnings=("Audit borné.",), facts={"secret": "/private"},
    ))
    flush(m)
    view = m.host._maintenance_result_views[0]
    assert view.button is not None and view.button.visible
    assert view.dialog is None  # No implicit popup for ordinary Maintenance tools.
    view.button.click()
    assert "Fichiers vérifiés : 3" in view.dialog.extra_child.children[0].text
    assert "/private" not in view.dialog.extra_child.children[0].text


@pytest.mark.parametrize("invalidated", ["identity", "generation", "detached", "closed", "refresh"])
def test_generic_maintenance_result_revoked_after_context_invalidation(invalidated):
    m = mounted("covers.audit")
    args = execute(m)
    args[2](MaintenanceResult(True, summary="Result"))
    view = m.host._maintenance_result_views[0]
    if invalidated == "identity":
        m.host.service.current_identity = NS(identity_id="other")
    elif invalidated == "generation":
        m.host._identity_context_generation += 1
    elif invalidated == "detached":
        m.preview.root = None
    elif invalidated == "closed":
        m.host._closed = True
    else:
        m.refresh()
    view.button.click()
    assert view.dialog is None


def test_cancelled_generic_maintenance_never_publishes():
    m = mounted("covers.audit")
    args = execute(m)
    m.cancel.click()
    args[2](MaintenanceResult(True, details=("Stale",)))
    view = m.host._maintenance_result_views[0]
    assert not view.text


def test_incomplete_reconciliation_limits_are_visible_before_long_counters():
    # #2565 real audit: user initially saw 320/120/408 but had to scroll to
    # discover 120s inventory and 10s metadata-candidate limitations.
    result = MaintenanceResult(
        False,
        summary="Analyse incomplète. 320 fichier(s) observé(s), 120 lien(s) confirmé(s).",
        details=tuple(f"Compteur {i} : {i}" for i in range(60)),
        warnings=(
            "Durée maximale de l’inventaire atteinte.",
            "La recherche de correspondances bibliographiques reste incomplète.",
            "Un SHA ou un ISBN ne prouve pas un droit.",
        ),
        facts={"private_path": "/HOME_PRIVATE_2565",
               "private_sha": "PRIVATE_HASH_2565"},
    )
    rendered = result_text(result)
    assert rendered.startswith("Analyse incomplète.")
    assert rendered.index("Avertissements et limites") < rendered.index("Résultats")
    assert rendered.index("Durée maximale de l’inventaire atteinte") < rendered.index("Compteur 0")
    assert "Compteur 59 : 59" in rendered
    assert "PRIVATE" not in rendered
    assert len(rendered) < 12000


def test_other_maintenance_result_order_is_unchanged_and_malformed_status_is_safe():
    complete = MaintenanceResult(True, summary="Analyse terminée",
                                 details=("Fichiers observés : 1",),
                                 warnings=("Le droit reste indéterminé.",))
    text = result_text(complete)
    assert text.index("Résultats") < text.index("Avertissements et limites")
    unrelated = NS(summary="Outil interrompu", details=("Résultat",),
                   warnings=("Attention",), facts={"secret": object()})
    assert result_text(unrelated).index("Résultats") < result_text(unrelated).index("Avertissements")


def test_other_maintenance_tools_retain_bounded_message_dialog_contract():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/maintenance_result_view.py").read_text(encoding="utf8")
    assert 'dialog = self.Adw.MessageDialog.new(self.parent, self.title, "")' in source


def test_other_maintenance_results_still_clear_on_retry():
    m = mounted('covers.audit')
    args = execute(m)
    args[2](MaintenanceResult(True, summary='Ancien audit des couvertures'))
    view = m.host._maintenance_result_views[0]
    assert view.text
    m.preview.click()
    assert not view.text and not view.button.visible


def test_other_maintenance_refresh_still_cancels_pending_work():
    m = mounted('covers.audit')
    _pending = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    m.refresh()
    assert control.cancelled()

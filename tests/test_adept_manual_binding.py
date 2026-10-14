from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage


GUI_SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service.execute_acquisition_request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("manual binding must never acquire")
    )
    service.execute_drm_mutation = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("manual binding must never return a loan")
    )
    service._provider_loan_active_for_acquisition = lambda row: False
    return service


def _artifact(tmp_path: Path, name: str = "Livre.epub") -> Path:
    path = tmp_path / "EBooks" / "alexandre" / "paris" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"preserve-this-artifact")
    return path


def _acquisition(service: LibraryService, path: Path, *, acquisition_id: str = "acq-1") -> str:
    return service.storage.acquisition_put({
        "acquisition_id": acquisition_id,
        "identity_id": "default",
        "provider_id": "paris",
        "credential_id": "reader",
        "account_id": "account",
        "item_id": "book",
        "title": "Titre fournisseur",
        "status": "complete",
        "executor": "kobo-adept",
        "metadata": {
            "path": str(path),
            "adept_binding": {
                "adept_profile_id": None,
                "adept_profile_name": "ancien-profil",
                "adept_loan_id": "ancien-id",
                "binding_source": "public_inventory",
                "return_available": False,
            },
        },
    })


def _runtime(loan: dict):
    calls = {"loan_inventories": [], "loans": []}

    def loan_inventories(names):
        calls["loan_inventories"].append(list(names))
        return {
            "complete": True,
            "profiles": [{"profile": "alexandre", "loans": [dict(loan)]}],
            "errors": [],
        }

    def loans(name):
        calls["loans"].append(name)
        return {"profile": name, "loans": [dict(loan)]}

    return SimpleNamespace(loan_inventories=loan_inventories, loans=loans), calls


def _configure_profile(service: LibraryService, runtime) -> None:
    service._kobo_adapter = lambda: runtime
    service.kobo_profiles = lambda: [{"profile_id": "profile-1", "name": "alexandre"}]
    service.storage.adept_profile_binding_set("identity", "default", "profile-1")


def _method_source(name: str) -> str:
    tree = ast.parse(GUI_SOURCE.read_text())
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.unparse(method)


def test_manual_binding_dialog_starts_without_selected_loan():
    controller = GUI_SOURCE.with_name("adept_loan_choice_controller.py")
    tree = ast.parse(controller.read_text(encoding="utf-8"))
    method = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "present_adept_loan_choices")
    source = ast.unparse(method)
    assert "adept_loan_choice_controller.present_adept_loan_choices" in _method_source("_present_adept_loan_choices")

    assert "dropdown.set_selected(Gtk.INVALID_LIST_POSITION)" in source
    calls = [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_response_enabled"
        and len(node.args) >= 2
    ]
    assert any(
        isinstance(call.args[0], ast.Constant) and call.args[0].value == "attach"
        and isinstance(call.args[1], ast.Constant) and call.args[1].value is False
        for call in calls
    )


def test_manual_binding_revalidates_exact_id_and_preserves_artifact_and_previous_binding(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    loan = {
        "id": "selected-id",
        "title": "Titre totalement différent",
        "local_book": "/ancien/chemin/qui/ne-correspond-pas.epub",
        "returned": False,
        "expired": False,
        "token_present": True,
        "validity": "2026-12-31",
    }
    runtime, calls = _runtime(loan)
    _configure_profile(service, runtime)
    service._adept_return_capability = lambda: True

    try:
        before = path.read_bytes()
        result = service.attach_adept_loan(acquisition_id, "profile-1", "selected-id")

        assert result == {
            "status": "resolved",
            "acquisition_id": acquisition_id,
            "return_available": True,
        }
        row = service.storage.acquisition_get(acquisition_id)
        binding = row["metadata"]["adept_binding"]
        previous = row["metadata"]["previous_adept_binding"]

        assert binding["adept_profile_id"] == "profile-1"
        assert binding["adept_profile_name"] == "alexandre"
        assert binding["adept_loan_id"] == "selected-id"
        assert binding["binding_source"] == "manual_selection"
        assert binding["return_available"] is True
        assert previous["adept_loan_id"] == "ancien-id"
        assert previous["adept_profile_name"] == "ancien-profil"
        assert path.read_bytes() == before
        assert calls["loans"] == ["alexandre"]
    finally:
        service.close()


def test_old_title_and_path_are_never_automatic_manual_binding_proof(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    loan = {
        "id": "selected-id",
        "title": "Titre fournisseur",
        "local_book": str(path),
        "returned": False,
        "expired": False,
        "token_present": True,
        "validity": None,
    }
    runtime, _calls = _runtime(loan)
    _configure_profile(service, runtime)
    service._adept_return_capability = lambda: True

    try:
        choices = service.adept_loan_choices(acquisition_id)
        assert [option["loan_id"] for option in choices["options"]] == ["selected-id"]

        # Publication still requires the exact ID chosen by the user.
        try:
            service.attach_adept_loan(acquisition_id, "profile-1", "different-id")
        except Exception as exc:
            assert getattr(exc, "code", "") == "adept_loan_selection_stale"
        else:
            raise AssertionError("title/path similarity must not replace exact loan-id revalidation")

        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["adept_loan_id"] == "ancien-id"
        assert binding["binding_source"] == "public_inventory"
    finally:
        service.close()


def test_returned_loan_remains_an_informative_manual_choice(tmp_path: Path):
    service = _service(tmp_path)
    path = _artifact(tmp_path)
    acquisition_id = _acquisition(service, path)
    loan = {
        "id": "returned-id",
        "title": "Ancien prêt",
        "local_book": None,
        "returned": True,
        "expired": False,
        "token_present": False,
        "validity": None,
    }
    runtime, _calls = _runtime(loan)
    _configure_profile(service, runtime)
    service._adept_return_capability = lambda: True

    try:
        choices = service.adept_loan_choices(acquisition_id)
        assert len(choices["options"]) == 1
        assert choices["options"][0]["loan_id"] == "returned-id"
        assert choices["options"][0]["state"] == "Restitué"

        result = service.attach_adept_loan(acquisition_id, "profile-1", "returned-id")
        assert result["return_available"] is False

        binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
        assert binding["loan_state"] == "returned"
        assert binding["token_present"] is False
        assert binding["return_available"] is False
        assert binding["binding_source"] == "manual_selection"
        assert path.read_bytes() == b"preserve-this-artifact"
    finally:
        service.close()


def test_expired_tokenless_and_disabled_return_capability_bind_informatively(tmp_path: Path):
    cases = [
        ("expired", {"returned": False, "expired": True, "token_present": True}, True),
        ("tokenless", {"returned": False, "expired": False, "token_present": False}, True),
        ("capability-off", {"returned": False, "expired": False, "token_present": True}, False),
    ]

    for index, (name, state, capability) in enumerate(cases):
        case_dir = tmp_path / name
        case_dir.mkdir(parents=True, exist_ok=True)
        service = _service(case_dir)
        path = _artifact(case_dir, f"{name}.epub")
        acquisition_id = _acquisition(service, path, acquisition_id=f"acq-{index}")
        loan = {
            "id": f"loan-{index}",
            "title": name,
            "local_book": None,
            "validity": None,
            **state,
        }
        runtime, _calls = _runtime(loan)
        _configure_profile(service, runtime)
        service._adept_return_capability = lambda value=capability: value

        try:
            result = service.attach_adept_loan(acquisition_id, "profile-1", loan["id"])
            assert result["status"] == "resolved"
            assert result["return_available"] is False

            binding = service.storage.acquisition_get(acquisition_id)["metadata"]["adept_binding"]
            assert binding["adept_loan_id"] == loan["id"]
            assert binding["binding_source"] == "manual_selection"
            assert binding["return_available"] is False
            assert path.exists()
        finally:
            service.close()

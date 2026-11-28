from io import BytesIO
from zipfile import ZipFile
from hashlib import sha256
import json

from cifereca_bnf_pret_health_replay import compare_bundle


def _zip(members):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for key, value in members.items():
            archive.writestr(key, value)
    return buffer.getvalue()


def _capture(html, ref, *, producer="provider.bnf-pret"):
    body = html.encode()
    index = {"schema": "cifereca.http-trace/1", "session_ref": ref,
             "omitted_exchanges": 0, "health_observation": {
                 "schema": "cifereca.health-observation/1", "capability": "loans", "version": "1"},
             "exchanges": [{"task_ref": ref, "outcome": "complete",
                 "request": {"producer": producer, "method": "GET",
                             "url": "https://pret.bnf.fr/my_profile/activity"},
                 "request_body": {"observed_bytes": 0},
                 "response": {"status_code": 200, "headers": [["content-type", "text/html"]]},
                 "response_body": {"state": "complete", "stored_bytes": len(body),
                     "observed_bytes": len(body), "blob": "body", "sha256": sha256(body).hexdigest()}}]}
    return _zip({"index.json": json.dumps(index), "body": body})


def test_offline_comparison(tmp_path):
    empty = '<section class="active-loans"></section>'
    positive = ('<section class="active-loans"><div class="js-loan">'
                '<a href="/loans/loan1">Prêt</a><a href="/resources/book1">Livre</a>'
                '</div></section>')
    def bundle(second):
        captures = (_capture(empty, "a"), second)
        members = {f"captures/{i:02d}.zip": data for i, data in enumerate(captures, 1)}
        manifest = {"schema": "cifereca.provider-health-bundle/1",
                    "scope": {"provider_id": "provider.bnf-pret", "single_private_account": True},
                    "captures": [{"member": name, "capability": "loans", "observed_provider_version": "1",
                                  "sha256": sha256(data).hexdigest()} for name, data in members.items()]}
        path = tmp_path / "bundle.zip"
        path.write_bytes(_zip(dict(members, **{"manifest.json": json.dumps(manifest)})))
        return compare_bundle(path)
    assert bundle(_capture(empty, "b"))["outcome"] == "equal"
    result = bundle(_capture(positive, "b"))
    assert result["outcome"] == "different"
    assert result["added"] == 1
    assert bundle(_capture(positive, "b", producer="other")) == {"outcome": "unknown"}
    assert bundle(_capture("<html>login</html>", "b")) == {"outcome": "unknown"}

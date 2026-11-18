"""Public award admission is not a claim of durable C indexing."""
import pytest
from cifereca.core.public_search_projection import project_public_search_results
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_public_search_projection import row, project


def test_only_public_declaration_supplies_awards():
    private = row(owned=True, source_account_id="owner", metadata={
        "awards": ["PRIVATE ANNOTATION"],
    })
    public = row(metadata={"awards": ["Prix du Quai"], "prizes": "Prix du jury",
                           "literary_prizes": ("Prix du roman",), "account": "owner"})
    response = ProviderSearchResponse([private], public_notices=(PublicSearchNotice(public),))
    single = project(response, private)
    batch = project_public_search_results(response)[id(private)]
    assert single == batch
    assert single.metadata == {"catalogue_access": "shared_read_only",
                               "awards": ["Prix du Quai"], "prizes": "Prix du jury",
                               "literary_prizes": ["Prix du roman"]}
    assert single.owned is False and single.source_account_id is None
    single.metadata["awards"].append("changed")
    assert public.metadata["awards"] == ["Prix du Quai"]


@pytest.mark.parametrize("value", [
    ["good", "Bearer PRIVATE"], {"title": "Prix"}, ["Prix"] * 33,
    "x" * 241, "https://private.invalid/?token=secret", ["user@example.invalid"],
])
@pytest.mark.parametrize("name", ["awards", "prizes", "literary_prizes"])
def test_invalid_award_cannot_be_public(name, value):
    private = row()
    public = row(metadata={name: value})
    response = ProviderSearchResponse([private], public_notices=(PublicSearchNotice(public),))
    assert project(response, private) is None
    assert project_public_search_results(response) == {}

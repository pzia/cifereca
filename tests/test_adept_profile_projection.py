"""Read-only reference/name lookup through the real service facade."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.services import LibraryService


@pytest.fixture
def service():
    service = LibraryService.__new__(LibraryService)
    service.storage = NS(adept_profile_binding_get=Mock(return_value=None))
    service.kobo_profiles = Mock(return_value=[])
    service.current_identity = NS(identity_id='unrelated-active-identity')
    return service


@pytest.mark.parametrize('row,reference,name', [
    ({'profile_id':'uuid','id':'legacy','name':'named','profile':'old'},'uuid','named'),
    ({'id':'legacy','profile':'old'},'legacy','old'),
    ({'name':' named '},'named','named'),
    ({'profile':'old'},'old','old'),
    ({'profile_id':'uuid'},'uuid','uuid'),
    ({},'',''),
])
def test_reference_and_display_name_keep_distinct_precedence(service,row,reference,name):
    snapshot=deepcopy(row)
    assert service._adept_profile_ref(row)==reference
    assert service._adept_profile_name(row)==name
    assert row==snapshot


@pytest.mark.parametrize('key',['profile_id','id','name','profile'])
def test_explicit_profile_list_matches_each_alias_without_runtime_enumeration(service,key):
    row={key:'matched'}; rows=[row,{'profile_id':'other'}]; snapshot=deepcopy(rows)
    assert service._adept_profile_row(' matched ',rows) is row
    assert rows==snapshot
    service.kobo_profiles.assert_not_called()


def test_empty_reference_and_explicit_empty_list_do_not_enumerate(service):
    assert service._adept_profile_row(None) is None
    assert service._adept_profile_row(' ') is None
    assert service._adept_profile_row('missing',[]) is None
    service.kobo_profiles.assert_not_called()


def test_missing_profile_list_is_enumerated_once_and_ambiguous_alias_is_rejected(service):
    rows=[{'name':'same','profile_id':'one'},{'name':'same','profile_id':'two'}]
    service.kobo_profiles.return_value=rows
    assert service._adept_profile_row('same') is None
    service.kobo_profiles.assert_called_once_with()
    assert service._adept_profile_row('one',rows) is rows[0]


def test_cli_name_uses_facade_lookup_and_keeps_existing_unresolved_reference_fallback(service):
    row={'profile_id':'uuid','name':'selected'}
    service._adept_profile_row=Mock(return_value=row)
    service._adept_profile_name=Mock(return_value='injected-name')
    profiles=[row]
    assert service._adept_profile_cli_name(' uuid ',profiles)=='injected-name'
    service._adept_profile_row.assert_called_once_with('uuid',profiles)
    service._adept_profile_name.assert_called_once_with(row)
    service._adept_profile_row.return_value=None
    assert service._adept_profile_cli_name(' unresolved ',profiles)=='unresolved'
    assert service._adept_profile_cli_name(None,profiles) is None


def test_origin_binding_is_identity_scoped_and_never_enumerates_other_profiles(service):
    service.storage.adept_profile_binding_get.return_value='persisted-origin'
    assert service._originating_adept_profile_id('alice','runtime-name')=='persisted-origin'
    service.storage.adept_profile_binding_get.assert_called_once_with('identity','alice')
    service.kobo_profiles.assert_not_called()


def test_origin_without_binding_requires_one_exact_named_profile(service):
    service.kobo_profiles.return_value=[{'name':'historical','profile_id':'origin'}]
    assert service._originating_adept_profile_id('alice','historical')=='origin'
    service.kobo_profiles.return_value=[{'name':'historical','profile_id':'one'},
                                       {'name':'historical','profile_id':'two'}]
    assert service._originating_adept_profile_id('alice','historical') is None
    assert service._originating_adept_profile_id('alice','missing') is None


def test_runtime_failure_is_suppressed_only_for_historical_origin_lookup(service):
    service.kobo_profiles.side_effect=RuntimeError('synthetic unavailable runtime')
    assert service._originating_adept_profile_id('alice','historical') is None
    with pytest.raises(RuntimeError,match='synthetic unavailable'):
        service._adept_profile_row('explicit')

"""Capture only published ADEPT result fields, separately from provider IDs.

#1120: a runtime result cannot attest an Adobe loan/profile through objects
whose Python coercion or comparisons execute untrusted extension code.
"""


def capture_binding(result, *, profile_name, profile_id):
    if result.get('ok') is not True:
        raise ValueError('Acquisition ADEPT non confirmée')
    if type(profile_name) is not str or (
        profile_id is not None and type(profile_id) is not str
    ):
        raise ValueError('Profil ADEPT du résultat incohérent')
    returned_profile = result.get('profile')
    if returned_profile is not None and (
        type(returned_profile) is not str or returned_profile != profile_name
    ):
        raise ValueError('Profil ADEPT du résultat incohérent')
    loan_id = result.get('loan_id')
    if loan_id is not None and (
        type(loan_id) is not str or not loan_id.strip() or loan_id.startswith('-')
    ):
        raise ValueError('Loan ID ADEPT invalide')
    return {'adept_profile_id': profile_id, 'adept_profile_name': profile_name,
            'adept_loan_id': loan_id, 'loan_state': 'active' if loan_id else 'unavailable',
            'return_available': bool(loan_id and profile_id)}

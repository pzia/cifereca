"""Capture only published ADEPT result fields, separately from provider IDs."""
def capture_binding(result, *, profile_name, profile_id):
    if result.get('ok') is not True:
        raise ValueError('Acquisition ADEPT non confirmée')
    returned_profile = result.get('profile')
    if returned_profile is not None and returned_profile != profile_name:
        raise ValueError('Profil ADEPT du résultat incohérent')
    loan_id = result.get('loan_id')
    if loan_id is not None and (not isinstance(loan_id, str) or not loan_id.strip() or loan_id.startswith('-')):
        raise ValueError('Loan ID ADEPT invalide')
    return {'adept_profile_id': profile_id, 'adept_profile_name': profile_name,
            'adept_loan_id': loan_id, 'loan_state': 'active' if loan_id else 'unavailable',
            'return_available': bool(loan_id and profile_id)}

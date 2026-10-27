"""Explicit local statements, separate from supplier-attested placements."""
from __future__ import annotations

from ..task_queue import TaskPriority


# Reviewed, privacy-safe local messages. Never render a raw Core exception,
# provider response or path in an error, even inside this private dialog.
_REFUSAL_MESSAGES = {
    'account_scope_changed': 'Ce compte ne correspond plus au profil courant. Actualisez les connexions.',
    'identity_not_active': 'Le profil actif a changé. Relancez la réconciliation.',
    'source_scope_changed': 'La session ou les connexions ont changé. Relancez la réconciliation.',
    'right_scope_changed': 'Le droit de cette édition a changé ou disparu. Relancez la synchronisation.',
    'file_scope_changed': 'Ce fichier ne se trouve plus dans le profil sélectionné.',
    'file_revalidation_failed': 'Le fichier a changé ou ne peut plus être vérifié. Relancez l’inspection.',
    'file_changed_before_commit': 'Le fichier a changé avant la confirmation. Relancez l’inspection.',
    'declaration_conflict': 'Une autre déclaration utilise déjà ce droit ou ce fichier. Consultez les associations déclarées.',
    'placement_conflict': 'Un placement SQLite existant contredit ce choix. Examinez les placements enregistrés.',
    'placement_account_ambiguous': 'Plusieurs comptes peuvent correspondre à ce placement. Vérifiez le compte.',
    'placement_coverage_incomplete': 'Les placements concernés ne peuvent pas être vérifiés entièrement.',
    'catalogue_conflict': 'Une notice de catalogue associée au fichier contredit le choix.',
    'preview_changed': 'Les données ont changé depuis la prévisualisation. Recommencez.',
    'acquisition_busy': 'Une acquisition est en cours. Réessayez après sa fin.',
    'storage_busy': 'La base est occupée par une autre opération. Réessayez.',
    'local_storage_unavailable': 'La base locale est momentanément inaccessible.',
    'schema_upgrade_required': 'Une mise à niveau de la base est nécessaire.',
    'directory_migration_pending': 'La migration du répertoire de bibliothèque doit se terminer.',
    'declaration_budget': 'La limite des déclarations enregistrées est atteinte.',
    'declaration_store_invalid': 'L’historique local des déclarations est incohérent. Aucune écriture.',
    'relation_scope_changed': 'La déclaration sélectionnée a changé ou n’est plus accessible.',
    'invalid_local_context': 'Le contexte local est invalide. Relancez l’inspection.',
}


def declaration_refusal_message(reason):
    """Human guidance derived from a closed Core code, never from exception text."""
    return _REFUSAL_MESSAGES.get(
        reason if type(reason) is str else None,
        'Déclaration refusée ou indisponible. Relancez la vérification locale.',
    )


class DeclarationActions:
    """Private GTK selection/diff/consent; the Core retains every action token."""

    def __init__(self, *, Gtk, Adw, window, runner, session, selection, alive):
        self.Adw, self.window, self.runner = Adw, window, runner
        self.session, self.selection, self.alive = session, selection, alive
        self.generation = 0
        self.expander = Gtk.Expander(label='Association déclarée · sans preuve fournisseur')
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.status = Gtk.Label(xalign=0)
        self.status.set_wrap(True)
        self.status.set_selectable(True)
        self.declare = Gtk.Button(label='Déclarer ce lien…')
        self.revoke = Gtk.Button(label='Révoquer la déclaration…')
        self.declare.set_tooltip_text(
            'Prévisualiser une correspondance choisie par vous, sans reçu '
            'd’acquisition. Aucun droit ni placement fournisseur ne sera créé.'
        )
        self.revoke.set_visible(False)
        # Show the private explanation in this expandable section, while
        # the explicit actions are placed in the persistent pair-action bar.
        # GTK widgets are single-parented; no duplicate callbacks or choices.
        body.append(self.status)
        self.expander.set_child(body)
        self.declare.connect('clicked', lambda *_: self.preview(False))
        self.revoke.connect('clicked', lambda *_: self.preview(True))
        self.reset()

    def reset(self):
        self.generation += 1
        self.revoke.set_visible(False)
        self.expander.set_expanded(False)
        pair = self.selection()
        context = self.session.declaration_context(*pair) if all(pair) else None
        self.declare.set_sensitive(context is not None)
        self.status.set_text(
            'Enregistrer votre choix de correspondance locale. '
            'Confiance : déclaration utilisateur. Ce lien ne prouve ni achat '
            'ni validité d’un prêt et ne sera pas compté comme lien attesté.'
            if context is not None else
            'Déclaration indisponible : choisissez un droit et un fichier '
            'du même profil avec un compte local identifié.'
        )

    def _current(self, pair, generation, *, require_fresh=True):
        return (self.alive() and self.generation == generation
                and self.selection() == pair
                and (not require_fresh or self.session.valid()))

    def preview(self, revoke):
        pair, generation = self.selection(), self.generation
        if not all(pair) or not self._current(pair, generation):
            return
        context = self.session.declaration_context(*pair)
        if context is None:
            return
        self.declare.set_sensitive(False)
        self.revoke.set_sensitive(False)
        self.expander.set_expanded(True)
        self.status.set_text('Vérification locale de la déclaration… Aucune écriture.')

        def read():
            return self.session.preview_declared_link(
                *pair, revoke=revoke,
                # Worker cancellation reads only a scalar generation. GTK
                # selection/window and session.valid() are checked on
                # the UI thread and independently by Core at commit.
                cancelled=lambda: self.generation != generation,
            )

        def completed(result):
            if not self._current(pair, generation):
                return
            self.declare.set_sensitive(True)
            self.revoke.set_sensitive(True)
            status = result.get('status') if type(result) is dict else None
            if status in ('preview', 'revoke_preview'):
                self._consent(pair, generation, context, revoke)
            elif status in ('already_declared', 'needs_review'):
                self.revoke.set_visible(result.get('revoke_available') is True)
                self.status.set_text(
                    'Cette déclaration nécessite une nouvelle vérification : '
                    'fichier local modifié ou indisponible, état du droit ou '
                    'compte changé, ou conflit de provenance. Aucune présence '
                    'locale confirmée ; la déclaration reste révocable.'
                    if result.get('presence_state') == 'declared_needs_review'
                    else
                    'Cette correspondance est déjà déclarée par vous. '
                    'Fichier local revérifié (octets et périmètre exact) ; '
                    'association manuelle présente pour le droit enregistré. '
                    'Ceci n’atteste ni achat auprès du fournisseur ni '
                    'acquisition officielle. Déclaration révocable.'
                    if result.get('presence_state') == 'present_declared'
                    else
                    'Cette correspondance est déjà déclarée par vous. '
                    'Sa présence actuelle n’est pas attestée ; elle peut être révoquée.'
                )
            elif status == 'blocked':
                self.status.set_text(
                    declaration_refusal_message(result.get('reason_code'))
                    + ' Aucune écriture.'
                )
            else:
                failed(None)

        def failed(_error):
            if self._current(pair, generation):
                self.declare.set_sensitive(True)
                self.revoke.set_sensitive(True)
                self.status.set_text(
                    declaration_refusal_message(None) + ' Aucune écriture.'
                )

        self.runner.submit('Prévisualisation de déclaration locale', read, completed, failed,
                           priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection')

    def _consent(self, pair, generation, context, revoke):
        # These values are already private inspection fields. Plain text only;
        # no source exception, markup, token or receipt enters this dialogue.
        coordinates = '\n'.join((
            'Profil : ' + context['identity'],
            'Source déclarée : ' + context['provider'],
            'Compte : ' + context['account'],
            'Édition : ' + context['edition'],
            'Droit sélectionné : ' + context['right'],
            'Fichier : ' + context['path'],
        ))
        change = (
            'Modification : révoquer uniquement votre déclaration. '
            'Son historique sera conservé.' if revoke else
            'Modification : enregistrer uniquement cette correspondance '
            'comme déclaration utilisateur, sans preuve fournisseur.'
        )
        dialog = self.Adw.MessageDialog.new(
            self.window, 'Révoquer cette déclaration ?' if revoke else 'Déclarer cette correspondance ?',
            coordinates + '\n\n' + change
            + '\nAucun livre, droit, reçu ou placement fournisseur ne sera modifié. '
            'Un prêt reste temporaire ; sa validité n’est pas attestée ici.',
        )
        dialog.set_body_use_markup(False)
        dialog.add_response('cancel', 'Annuler')
        dialog.add_response('apply', 'Révoquer la déclaration' if revoke else 'Confirmer la déclaration')
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def response(_dialog, response_id):
            if not self._current(pair, generation):
                return
            if response_id != 'apply':
                self.session.revoke_manual_attachment_preview()
                self.status.set_text('Déclaration annulée. Aucune écriture.')
                return
            self.declare.set_sensitive(False)
            self.revoke.set_sensitive(False)
            self.status.set_text('Confirmation reçue · vérification locale finale…')

            def commit():
                return self.session.confirm_declared_link(
                    *pair, user_confirmed=True,
                    # The worker must not read GTK selection or session.valid().
                    cancelled=lambda: self.generation != generation,
                )

            def completed(result):
                # A committed mutation remains successful even if its DB
                # stamp has just invalidated the old inspection projection.
                if not self._current(pair, generation, require_fresh=False):
                    return
                status = result.get('status') if type(result) is dict else None
                self.declare.set_sensitive(self.session.valid())
                self.revoke.set_sensitive(True)
                self.revoke.set_visible(result.get('revoke_available') is True)
                if status == 'blocked':
                    self.status.set_text(
                        declaration_refusal_message(result.get('reason_code'))
                        + ' Aucune écriture.'
                    )
                    return
                self.status.set_text({
                    'declared': 'Correspondance enregistrée comme déclaration utilisateur. '
                                'Aucun droit ou lien attesté créé. Déclaration révocable.',
                    'already_declared': 'Correspondance déjà déclarée. Aucune nouvelle écriture.',
                    'revoked': 'Déclaration révoquée, historique conservé. Livre et droits inchangés.',
                    'already_revoked': 'Déclaration déjà révoquée. Aucune nouvelle écriture.',
                }.get(status, 'Confirmation refusée ou indisponible. Relancez la vérification locale.'))

            def failed(_error):
                if self._current(pair, generation, require_fresh=False):
                    self.declare.set_sensitive(self.session.valid())
                    self.revoke.set_sensitive(True)
                    self.status.set_text('Confirmation indisponible. Relancez la vérification locale.')

            self.runner.submit('Confirmation de déclaration locale', commit, completed, failed,
                               priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection')

        dialog.connect('response', response)
        dialog.present()

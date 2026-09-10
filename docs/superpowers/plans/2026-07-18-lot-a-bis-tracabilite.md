# Lot A-bis — traçabilité de l'octroi : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** enregistrer *qui* a offert ou révoqué l'accès payant, *quand* et *pourquoi*, et rendre le motif saisissable depuis les deux back-offices.

**Architecture :** TrainingManager réutilise son app `audit/` existante (deux nouveaux codes d'action, appel best-effort après la mutation). Poker, qui n'a aucune infrastructure d'audit, reçoit un petit modèle append-only local à `accounts/`, consultable en lecture seule dans l'admin Django. Les deux SPA gagnent un champ de saisie du motif à côté de l'interrupteur.

**Tech Stack :** Django 6 + DRF + pytest (backends) ; Angular 21 standalone + signals + PrimeNG 21 + Transloco + Vitest (frontends).

## Contexte : pourquoi ce lot existe

Le lot A enregistre *quand* l'accès a été offert (`bypass_granted_at`) et *pourquoi* (`bypass_note`), mais jamais *qui*. Une révocation ne laisse aucune trace. Et comme aucun des deux back-offices n'expose de champ de saisie du motif, toute activation faite depuis l'UI produit une note vide — l'audit prévu par la spec §A.1 est donc largement théorique.

## Global Constraints

- **Prérequis :** le lot A est livré sur les branches `feat/bypass-and-tables` (`Poker_server`) et `feat/subscription-bypass` (les trois autres repos). Ce lot s'empile **sur les mêmes branches**, rien n'est encore poussé.
- **Journaliser uniquement sur changement effectif du flag.** Un PATCH qui ne modifie que la note ne doit produire aucune entrée. C'est la règle qui distingue un journal utile d'un journal bruyant.
- **Best-effort, jamais bloquant côté TM :** `audit.services.record` ne lève jamais (try/except + savepoint). Ne pas contourner cette garantie.
- **Convention TM pour un basculement : deux codes d'action distincts**, jamais un booléen en métadonnée. Précédent : `session_shared` / `session_unshared`.
- **Les valeurs de `AuditAction` sont un contrat persisté** : ne jamais renommer une valeur existante (contrat écrit en tête de la classe).
- **Poker :** ne pas créer d'app `audit`. Le repo n'a aucune infrastructure de ce type ; un modèle local à `accounts/` est proportionné. Ne pas se rabattre sur un `logger.info` : le `LOGGING` du repo est un handler console sans formatter (les `extra` ne sont pas rendus) et Sentry ne capte pas les `info` — ce serait une traçabilité de façade.
- **Append-only :** une révocation ajoute une ligne, elle n'en modifie ni n'en supprime aucune.
- **i18n :** toute nouvelle clé dans les **cinq** catalogues `public/i18n/{fr,nl,en,it,es}.json` des deux fronts. Sur Poker, `src/app/i18n-parity.spec.ts` casse le build en cas d'oubli.
- **`npm run build` ne typecheck pas les specs.** Lancer **`npm test`** en plus, systématiquement (leçon du lot A).
- **TrainingManager :** toute modification de serializer ou de choices impose `python manage.py spectacular --file openapi-schema.yaml --validate` et le commit du YAML **dans le même commit**. Une erreur préexistante (`DiscussionsUnreadView`, `operation_id` dupliqué) est hors périmètre ; le compteur de **warnings doit rester à 0**.
- **Aucun re-vendorage OpenAPI côté frontend TM n'est nécessaire :** `PatchedStaffUserRequest` accepte déjà `bypass_note`, la forme du contrat staff ne change pas.
- **Fichiers hors périmètre à ne jamais committer** (travail du propriétaire, modifié-non-commité) : dans `Poker_frontend`, `src/app/features/about/contact.ts` ; dans `trainingmanager_frontend`, `src/app/shared/contact.ts`, `src/app/shared/contact.spec.ts`, `src/app/features/about-page/about-page.component.spec.ts`. Stager explicitement par chemins, jamais `git add -A`.
- **Ne rien pousser :** les quatre repos auto-déploient sur push de leur branche par défaut.

---

### Task 1 : journal d'audit TrainingManager

**Repo :** `D:\Projects\PycharmProjects\trainingmanager_server`, branche `feat/subscription-bypass`

**Files:**
- Modify: `audit/models.py` (enum `AuditAction` + constante d'exclusion de purge)
- Create: `audit/migrations/0002_*.py` (généré — `AlterField` sur les choices)
- Modify: `customuser/views/staff.py` (`StaffUserDetailView.patch`)
- Modify: `audit/management/commands/purge_audit_log.py`
- Modify: `openapi-schema.yaml` (régénéré)
- Test: `tests/test_audit.py`, `tests/test_audit_purge.py` (ajouts)

**Interfaces:**
- Consomme : `audit.services.audit_event(request, action, **kwargs)` ; `audit.services.record(action, *, actor=None, team=None, target_repr="", metadata=None, request=None)` ; `StaffUserDetailView` du lot A, qui possède déjà une variable `was_granted` capturant l'état avant mutation.
- Produit : `AuditAction.SUBSCRIPTION_BYPASS_GRANTED`, `AuditAction.SUBSCRIPTION_BYPASS_REVOKED`, et `audit.models.NON_PURGEABLE_ACTIONS`.

**Décision arrêtée à respecter :** les entrées portent `team=None`. Conséquence assumée — `audit/views.py` scope la lecture par équipes managées, donc une entrée sans équipe n'est visible **que des superusers**. C'est le comportement voulu pour un acte staff.

- [ ] **Step 1 : écrire les tests qui échouent**

Dans `tests/test_audit.py`, section wiring (réutiliser les imports et fixtures déjà présents dans le fichier ; ajouter `get_user_model` s'il manque) :

```python
def test_PATCH_staff_user_grant_then_revoke_records_two_audit_entries(admin_client):
    User = get_user_model()
    target = User.objects.create_user(email="audited@local.test", password="Sup3rS@fePass!")

    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": False}, format="json"
    )

    actions = list(
        AuditLogEntry.objects.filter(target_repr__contains=f"User #{target.pk}")
        .order_by("created_at")
        .values_list("action", flat=True)
    )
    assert actions == [
        AuditAction.SUBSCRIPTION_BYPASS_GRANTED,
        AuditAction.SUBSCRIPTION_BYPASS_REVOKED,
    ]


def test_bypass_audit_entry_records_actor_and_reason(admin_client, admin_user):
    User = get_user_model()
    target = User.objects.create_user(email="audited2@local.test", password="Sup3rS@fePass!")
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    entry = AuditLogEntry.objects.get(action=AuditAction.SUBSCRIPTION_BYPASS_GRANTED)
    assert entry.actor_id == admin_user.pk
    assert entry.actor_label == admin_user.email
    assert entry.metadata == {"reason": "asso X"}


def test_no_audit_entry_when_flag_is_unchanged(admin_client):
    """Un PATCH qui ne touche que la note ne doit rien journaliser."""
    User = get_user_model()
    target = User.objects.create_user(email="audited3@local.test", password="Sup3rS@fePass!")
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"bypass_note": "note seule"}, format="json"
    )
    assert not AuditLogEntry.objects.filter(action__startswith="subscription_bypass_").exists()
```

Dans `tests/test_audit_purge.py` (réutiliser les imports du fichier, notamment `call_command`, `timezone`, `timedelta`) :

```python
def test_purge_keeps_subscription_bypass_entries():
    """Les octrois d'acces offert ont une valeur commerciale : ils survivent a la purge."""
    old = timezone.now() - timedelta(days=800)
    kept = record(AuditAction.SUBSCRIPTION_BYPASS_GRANTED, target_repr="User #1 (a@b.c)")
    purged = record(AuditAction.MEMBER_REMOVED, target_repr="Member #1 (X)")
    AuditLogEntry.objects.filter(pk__in=[kept.pk, purged.pk]).update(created_at=old)

    call_command("purge_audit_log")

    assert AuditLogEntry.objects.filter(pk=kept.pk).exists()
    assert not AuditLogEntry.objects.filter(pk=purged.pk).exists()
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/test_audit.py tests/test_audit_purge.py -q`
Expected: FAIL — `AttributeError: SUBSCRIPTION_BYPASS_GRANTED`

- [ ] **Step 3 : ajouter les deux codes d'action**

Dans `audit/models.py`, à la fin de la classe `AuditAction` :

```python
    SUBSCRIPTION_BYPASS_GRANTED = "subscription_bypass_granted", _("Offered access granted")
    SUBSCRIPTION_BYPASS_REVOKED = "subscription_bypass_revoked", _("Offered access revoked")
```

puis, sous la classe :

```python
# Actions conservees indefiniment : elles tracent l'octroi d'un droit payant, dont la
# valeur commerciale survit a la fenetre de retention ordinaire (purge_audit_log).
NON_PURGEABLE_ACTIONS = (
    AuditAction.SUBSCRIPTION_BYPASS_GRANTED,
    AuditAction.SUBSCRIPTION_BYPASS_REVOKED,
)
```

- [ ] **Step 4 : journaliser dans la vue staff**

Dans `customuser/views/staff.py`, `StaffUserDetailView.patch`, après le bloc qui horodate `bypass_granted_at` et avant le `return`. La vue possède déjà `was_granted`, capturé avant la mutation :

```python
        # Audit de l'octroi/revocation (best-effort; never breaks the action).
        if user.subscription_bypass != was_granted:
            from audit.models import AuditAction
            from audit.services import audit_event

            audit_event(
                request,
                AuditAction.SUBSCRIPTION_BYPASS_GRANTED
                if user.subscription_bypass
                else AuditAction.SUBSCRIPTION_BYPASS_REVOKED,
                team=None,
                target_repr=f"User #{user.id} ({user.email})",
                metadata={"reason": user.bypass_note} if user.bypass_note else {},
            )
```

Import local dans le corps de la fonction : c'est la convention du repo sur les six autres call-sites d'audit.

- [ ] **Step 5 : exclure ces actions de la purge**

Dans `audit/management/commands/purge_audit_log.py` : importer `NON_PURGEABLE_ACTIONS` depuis `audit.models` et ajouter `.exclude(action__in=NON_PURGEABLE_ACTIONS)` au queryset de sélection des entrées à supprimer. Documenter l'exclusion dans le docstring de la commande, et la mentionner dans la sortie `--dry-run` pour qu'un opérateur comprenne pourquoi le compte diffère de ce qu'il attend.

- [ ] **Step 6 : générer la migration**

Run: `python manage.py makemigrations audit`
Expected: `audit/migrations/0002_*.py` contenant un `AlterField` sur `action`. Ne pas l'écrire à la main.

- [ ] **Step 7 : lancer les tests**

Run: `pytest tests/test_audit.py tests/test_audit_purge.py tests/test_staff_users.py tests/test_entitlements.py -q`
Expected: PASS

- [ ] **Step 8 : régénérer le schéma**

Run: `python manage.py spectacular --file openapi-schema.yaml --validate`
Expected: 0 warning. L'erreur unique sur `DiscussionsUnreadView` est préexistante et hors périmètre : elle ne doit ni être corrigée ici, ni avoir augmenté.

- [ ] **Step 9 : commit**

```bash
git add audit/ customuser/views/staff.py tests/test_audit.py tests/test_audit_purge.py openapi-schema.yaml
git commit -m "feat(audit): journalise l'octroi et la revocation de l'acces offert"
```

---

### Task 2 : journal d'octroi Poker

**Repo :** `D:\Projects\PycharmProjects\Poker_server`, branche `feat/bypass-and-tables`

**Files:**
- Modify: `accounts/models.py` (nouveau modèle en fin de fichier)
- Create: `accounts/migrations/0004_*.py` (généré)
- Modify: `accounts/api_staff_views.py`
- Modify: `accounts/admin.py`
- Test: `accounts/tests/test_staff_users.py` (ajouts)

**Interfaces:**
- Consomme : `User.subscription_bypass`, `StaffUserDetailView` du lot A (qui possède déjà `was_granted`).
- Produit : `accounts.models.BypassGrantLog`.

- [ ] **Step 1 : écrire les tests qui échouent**

Dans `accounts/tests/test_staff_users.py`, qui possède déjà les fixtures `staff` et `member` et le helper `_client(user)` :

```python
@pytest.mark.django_db
def test_grant_then_revoke_writes_two_log_rows(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/staff/users/{member.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    _client(staff).patch(
        f"/api/staff/users/{member.pk}/", {"subscription_bypass": False}, format="json"
    )

    rows = list(BypassGrantLog.objects.order_by("created_at"))
    assert [r.granted for r in rows] == [True, False]
    assert rows[0].actor_id == staff.pk and rows[0].target_id == member.pk
    assert rows[0].actor_label == staff.email
    assert rows[0].note == "asso X"


@pytest.mark.django_db
def test_unchanged_flag_writes_no_log_row(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/staff/users/{member.pk}/", {"bypass_note": "note seule"}, format="json"
    )
    assert not BypassGrantLog.objects.exists()


@pytest.mark.django_db
def test_log_survives_actor_deletion(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/staff/users/{member.pk}/", {"subscription_bypass": True}, format="json"
    )
    staff.delete()
    row = BypassGrantLog.objects.get()
    assert row.actor_id is None and row.actor_label == "staff@example.com"
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_staff_users.py -q`
Expected: FAIL — `ImportError: cannot import name 'BypassGrantLog'`

- [ ] **Step 3 : écrire le modèle**

À la fin de `accounts/models.py` :

```python
class BypassGrantLog(models.Model):
    """Journal append-only des octrois/revocations d'acces offert (spec lot A-bis).

    Le User porte l'ETAT courant (subscription_bypass / bypass_note / bypass_granted_at) ;
    ce modele porte l'HISTOIRE, y compris l'acteur, que l'etat courant ne dit pas.
    Jamais modifie ni supprime : une revocation ajoute une ligne, elle n'en efface aucune.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bypass_grants_made",
    )
    # Snapshot de l'email : la trace survit a la suppression du compte staff.
    actor_label = models.CharField(max_length=254, blank=True)
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bypass_grants_received"
    )
    granted = models.BooleanField()  # True = octroi, False = revocation
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        verb = "grant" if self.granted else "revoke"
        return f"{verb} #{self.target_id} by {self.actor_label}"
```

Vérifier que `settings` est bien importé en tête du fichier (`from django.conf import settings`) ; l'ajouter sinon.

- [ ] **Step 4 : écrire la ligne de journal dans la vue**

Dans `accounts/api_staff_views.py`, `StaffUserDetailView.patch`, après l'horodatage de `bypass_granted_at` et avant le `return` :

```python
        # Journal append-only : l'etat courant ne dit pas QUI a bascule le flag.
        if user.subscription_bypass != was_granted:
            BypassGrantLog.objects.create(
                actor=request.user,
                actor_label=request.user.email,
                target=user,
                granted=user.subscription_bypass,
                note=user.bypass_note,
            )
```

et compléter l'import en tête : `from .models import BypassGrantLog, User`.

- [ ] **Step 5 : exposer le journal en lecture seule dans l'admin**

Dans `accounts/admin.py` :

```python
@admin.register(BypassGrantLog)
class BypassGrantLogAdmin(admin.ModelAdmin):
    """Journal append-only : consultable, jamais modifiable depuis l'admin."""

    list_display = ("created_at", "target", "granted", "actor_label", "note")
    list_filter = ("granted", "created_at")
    search_fields = ("actor_label", "target__email", "note")
    readonly_fields = ("actor", "actor_label", "target", "granted", "note", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

et compléter l'import du modèle en tête du fichier.

- [ ] **Step 6 : générer la migration**

Run: `.venv/Scripts/python manage.py makemigrations accounts`
Expected: une migration `0004_*` créant `BypassGrantLog`. Ne pas l'écrire à la main.

- [ ] **Step 7 : lancer la suite complète**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, aucune régression

- [ ] **Step 8 : commit**

```bash
git add accounts/
git commit -m "feat(accounts): journal append-only des octrois d'acces offert"
```

---

### Task 3 : saisie du motif — back-office TrainingManager

**Repo :** `D:\Projects\WebstormProjects\trainingmanager_frontend`, branche `feat/subscription-bypass`

**Files:**
- Modify: `src/app/features/admin/users/users-list/users-list.component.ts`
- Modify: `src/app/features/admin/users/users-list/users-list.component.html`
- Modify: `src/app/features/admin/users/users-list/users-list.component.scss`
- Modify: `src/app/features/admin/users/users-list/users-list.component.spec.ts`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`

**Interfaces:**
- Consomme : `StaffService.staffUsersPartialUpdate({ id: number, patchedStaffUserRequest?: PatchedStaffUserRequest })` ; `PatchedStaffUserRequest = { subscription_bypass?: boolean; bypass_note?: string }` ; `StaffUser = { readonly id, readonly email, readonly first_name, readonly last_name, subscription_bypass?, bypass_note?, readonly bypass_granted_at }`.
- Produit : rien pour les tâches suivantes.

**Aucun re-vendorage OpenAPI n'est nécessaire :** `bypass_note` est déjà dans le contrat généré.

- [ ] **Step 1 : écrire le test qui échoue**

Le spec doit prouver que le motif **saisi** part dans la requête, et non la note préexistante :

```ts
it('envoie le motif saisi avec la bascule', () => {
  access(component).search();
  access(component).setNote(1, 'asso X');
  access(component).toggle({ id: 1, bypass_note: '' } as StaffUser, true);
  expect(staffMock.staffUsersPartialUpdate).toHaveBeenCalledWith({
    id: 1,
    patchedStaffUserRequest: { subscription_bypass: true, bypass_note: 'asso X' },
  });
});
```

Compléter l'interface locale `ProtectedFields` avec le membre exercé. Le nom exact et la forme de l'état d'édition (`setNote(id, value)` sur un dictionnaire de signaux, ou équivalent) sont laissés à l'implémentation : choisir la forme la plus simple qui reste testable **sans DOM**, conformément à la convention du repo, et aligner le test dessus.

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test -- users-list`
Expected: FAIL

- [ ] **Step 3 : implémenter la saisie**

Remplacer la cellule « Motif » en lecture seule par un `<input pInputText>` lié à un état d'édition local par ligne, dont la valeur est envoyée avec la bascule. La note doit rester cohérente après un rechargement (elle revient du serveur). Conserver le BEM `.users-list__`, les couleurs uniquement via les variables de `src/styles/_tokens.scss`, et `ChangeDetectionStrategy.OnPush`.

- [ ] **Step 4 : clés i18n dans les cinq catalogues**

Ajouter `admin.users.fields.note_placeholder` :

| Langue | Valeur |
|---|---|
| fr | Pourquoi cet accès est offert |
| en | Why this access is offered |
| nl | Waarom deze toegang wordt aangeboden |
| it | Perché questo accesso è offerto |
| es | Por qué se ofrece este acceso |

- [ ] **Step 5 : lancer les tests et le build**

Run: `npm test`
Expected: PASS

Run: `npm run build`
Expected: succès

- [ ] **Step 6 : commit**

```bash
git add src/app/features/admin/users/ public/i18n/
git commit -m "feat(admin): saisie du motif a l'octroi de l'acces offert"
```

---

### Task 4 : saisie du motif — back-office Poker

**Repo :** `D:\Projects\WebstormProjects\Poker_frontend`, branche `feat/subscription-bypass`

**Files:**
- Modify: `src/app/features/staff/staff-users.component.ts`
- Modify: `src/app/core/staff/staff.service.spec.ts`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`

**Interfaces:**
- Consomme : `StaffService.setBypass(id: number, bypass: boolean, note: string): Observable<StaffUser>` du lot A — la signature accepte **déjà** la note ; seule l'interface de saisie manque. Le service appelle `getRuntimeConfig().apiBaseUrl` (le SPA Poker parle à son API en cross-origin) : ne pas revenir à un chemin relatif.
- Produit : rien.

- [ ] **Step 1 : écrire le test qui échoue**

`StaffService.setBypass` accepte déjà la note, donc le test utile porte sur ce que le **composant** transmet. Le repo n'a pas de TestBed : extraire la construction de l'appel dans une fonction pure testable, sur le modèle de `src/app/core/billing/gating.ts` (créé au lot A), et l'importer **à la fois** dans le composant et dans le spec. Ne pas redéfinir la logique dans le spec — ce serait tester une copie.

Exemple de forme attendue, à adapter au nom retenu :

```ts
import { describe, expect, it } from 'vitest';
import { bypassPatch } from './bypass-patch';

describe('bypassPatch', () => {
  it('transmet le motif saisi plutot que la note existante', () => {
    expect(bypassPatch(true, 'asso X')).toEqual({ subscription_bypass: true, bypass_note: 'asso X' });
  });

  it('transmet une note vide telle quelle', () => {
    expect(bypassPatch(false, '')).toEqual({ subscription_bypass: false, bypass_note: '' });
  });
});
```

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test`
Expected: FAIL — module introuvable

- [ ] **Step 3 : implémenter la saisie**

Ajouter une colonne « Motif » éditable (`<input pInputText>`) dans la table du back-office, dont la valeur part avec la bascule. Styles contre les tokens de `src/styles/_tokens.scss`. Conserver `ChangeDetectionStrategy.OnPush` et les modules PrimeNG (`XxxModule`), convention de ce repo.

- [ ] **Step 4 : clés i18n dans les cinq catalogues**

Ajouter `staff.users.fields.note_placeholder`, avec les mêmes traductions que la Task 3.

- [ ] **Step 5 : lancer les tests et le build**

Run: `npm test`
Expected: PASS, y compris `src/app/i18n-parity.spec.ts`

Run: `npm run build`
Expected: succès

- [ ] **Step 6 : commit**

```bash
git add src/app/core/staff/ src/app/features/staff/ public/i18n/
git commit -m "feat(staff): saisie du motif a l'octroi de l'acces offert"
```

---

## Ordre d'exécution

Task 1 (backend TM) → Task 3 (front TM), et Task 2 (backend Poker) → Task 4 (front Poker). Les deux paires sont indépendantes l'une de l'autre.

## Vérification finale du lot A-bis

- [ ] Les quatre suites vertes (`pytest -q` sur les deux backends, `npm test && npm run build` sur les deux fronts)
- [ ] Scénario manuel : offrir un accès depuis chaque back-office **avec un motif**, le révoquer, puis vérifier que le journal contient deux lignes nommant l'acteur et le motif — dans `/api/v1/audit-log/` côté TM (compte superuser requis, les entrées portent `team=None`), dans l'admin Django côté Poker.

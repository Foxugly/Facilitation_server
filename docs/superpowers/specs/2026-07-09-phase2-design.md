# Delegation Poker — Phase 2 design & decomposition (paid offer)

**Date :** 2026-07-09
**Statut :** design fondateur (à valider avant code). Phase 1 (backend + frontend) est **live**.
Source : `delegation-poker-scope.md` §4.1 (offre payante), §5 (phasage), §9 (décisions). Le scope
insiste : **Phase 2 = projet distinct**, à construire par **sous-projets incrémentaux**.

> Ce document fige le **découpage**, l'**ordre de build** (graphe de dépendances), les **ajouts au
> modèle de données** par sous-projet, les **points transverses** (JWT, Turnstile, email, CSP), et
> liste les **décisions produit à confirmer**. Chaque sous-projet aura ensuite son plan d'implémentation.

---

## 1. Principe & garde-fous

- **Incrémental, livrable par livrable.** Chaque sous-projet est déployable seul et ne casse pas la
  Phase 1 gratuite (les salles anonymes restent le chemin par défaut).
- **Ancrages déjà posés en Phase 1** (migrations additives faciles) : `accounts.User` existe (email-only) ;
  `Participant.user` (FK nullable) existe. À ajouter en additif : `Deck.team`, `Room.team`,
  `Subject.dimension`, `VoteSession.deck_snapshot` propre à la session.
- **Gratuit vs payant = une frontière d'autorisation**, pas deux bases de code : mêmes modèles
  runtime (`Room`/`VoteSession`/…), gating par appartenance à une équipe + abonnement.
- **⚠️ Invité anonyme interdit dans une salle d'équipe** (scope §4.2) : une salle rattachée à une
  `Team` n'accepte que des membres authentifiés.

---

## 2. Ordre de build (graphe de dépendances)

```
P2.1 Auth / accounts ─┬─> P2.2 Teams & membership ─┬─> P2.3 Salles d'équipe + votes préparés
                      │                            ├─> P2.4 Historique + email
                      │                            ├─> P2.5 Delegation Board (AS-IS/TO-BE) + export
                      │                            └─> P2.6 Decks custom + dos uploadé
                      └─> (transverse) Turnstile, JWT rotation, chrome authentifié
P2.2 ─> P2.7 Billing (Stripe) ── gate les features payantes
P2.8 Publier /features + nav authentifiée  (petit, à la fin)
```

**Recommandation : démarrer par P2.1 (auth)** — tout en dépend. Puis P2.2 (teams). Ensuite P2.4/P2.5/P2.6
sont largement parallélisables ; P2.7 (billing) peut venir tôt (dès P2.2) ou tard selon la priorité business.

---

## 3. Sous-projets & modèle de données

### P2.1 — Auth / accounts (fondation)
Réutiliser le pattern flotte §3.16 (email-only, simplejwt, **pas d'allauth**) + les leçons mémoire.
- **`accounts.User`** : ajouter `display_name` (nom d'affichage **distinct de l'email**, ≠ le
  `Participant.display_name` éphémère anonyme). `email_confirmed` existe déjà.
- **Endpoints** : register (Turnstile), login (`EmailConfirmedTokenObtainPairView`), refresh
  (**rotation + blacklist** — cf. `[[fleet-jwt-refresh-rotation-clients]]`), confirm-email,
  resend, password-reset (Django token generators), magic-link *(optionnel — à confirmer)*.
- **Frontend** : écrans login/register/reset (cartes centrées, pas de `app-page-header`, §3.15),
  `AuthService.bootstrap()`, `authInterceptor`, **persistance du refresh roté** (piège flotte),
  garde de routes, topmenu mode `authenticated`.
- **⚠️ Migration email-unique Postgres** : base quasi vierge (1 superuser) → trivial, mais valider
  sur Postgres (cf. `[[fleet-django-migration-postgres-gotchas]]`).

### P2.2 — Teams & membership
- **`teams.Team`** : `name`, `owner (FK User)`, `created_at`, (Phase billing) `subscription`.
- **`teams.TeamMembership`** : `team`, `user`, `role ∈ {owner, admin, member}`, `joined_at` ;
  `UniqueConstraint(team, user)`.
- **`teams.Invitation`** : `team`, `email`, `token`, `role`, `expires_at`, `accepted_at` (invite par
  email, lien à login requis).
- **Frontend** : liste d'équipes (grille de cartes §3.15), création, page membres + invitations.
- **Ancre** : `Room.team` (AddField null) + `Participant.user` (déjà là).

### P2.3 — Salles d'équipe + votes préparés + transfert facilitateur
- **`Room.team`** (FK null) : salle rattachée → membres uniquement, non éphémère (pas d'expiration 8h).
- **`Subject` préparés** : `Subject.prepared (bool)` + `title` optionnel, créés à l'avance pour une
  session d'équipe (le scope parle de « votes pré-créés avec titres »).
- **Transfert *volontaire* du facilitateur** (contrat WS §9, Phase 2) : nouvel événement
  `facilitator.transfer {targetParticipantId}` (autorité facilitateur) — distinct du garde-fou 60s
  déjà livré. Réassigne `VoteSession.facilitator`.

### P2.4 — Historique (résultats actés) + email
- **`history.HistoryEntry`** : `team`, `date`, `room (nullable)`, snapshot des `Result` actés du jour.
  Historique = **résultats actés uniquement** (pas les votes bruts, scope §3.11).
- **Email** : lien vers l'historique d'un jour, envoyé à **tous les membres**, **login requis**.
  Service transactionnel **Graph** (comme la flotte `GRAPH_*`) + **Celery** + templates traduits (5 langues).
- **Consultation par date** (frontend : liste datée).

### P2.5 — Delegation Board (AS-IS / TO-BE) + export  *(livrable phare)*
- **Vue d'agrégation** sur les `Result` actés d'une équipe (scope §6, §9-B) — **pas un écran édité
  à la main**. Domaines de décision = **les sujets de vote** (aucun référentiel à produire).
- **`Subject.dimension ∈ {as_is, to_be}`** (AddField null) : chaque case cochée = **un tour de vote**
  (« où en est-on ? » puis « où veut-on aller ? »). Une `Team` a un board **persistant**.
- **Modèle** : probablement calculé à la volée depuis `Result` (+ un `Board`/`BoardRow` léger pour
  l'ordre/regroupement des sujets) — **à trancher** (table vs vue calculée).
- **Export** : **CSV/Excel** (matrice, quasi gratuit) **+ PNG/PDF** (rendu **asynchrone Celery**,
  HTML→PDF ; attention mise en page 5 langues + dark/light, scope risques).

### P2.6 — Decks custom + dos personnalisé
- **`Deck.team`** (FK null) : deck custom rattaché à une équipe (payant uniquement).
- **CRUD** `Deck`/`Card`/`TextLayer` côté app (éditeur de position **par formulaire de coordonnées**
  en v1, pas de drag&drop — scope §4.2).
- **Dos personnalisé** : liste prédéfinie **+ upload**, **validation serveur** (jpg/png/webp, <5 Mo,
  dimensions bornées, contenu réel — voir `docs/card-assets-spec.md` §5 + scope §10).
- **`VoteSession.deck_snapshot`** devient **propre à la session** (decks variables) — au lieu du
  snapshot au niveau `Room` de la Phase 1.

### P2.7 — Billing (Stripe)
- **Facturation par équipe** (forfait, membres illimités — scope §9-A). `teams.Subscription` :
  `team (OneToOne)`, `stripe_customer_id`, `stripe_subscription_id`, `status`, `current_period_end`.
- Checkout Stripe + **webhooks** (statut d'abonnement). **Gating** : les features payantes
  (P2.2–P2.6) exigent un abonnement actif.
- **À confirmer** : structure de prix (un seul forfait ? essai gratuit ?), gestion des impayés.

### P2.8 — Publier /features + chrome authentifié
- La page `/features` existe déjà (Phase 1) — la **publier** (elle décrit des features réelles).
- Nav authentifiée (§3.15) : Dashboard / Équipes / Historique + cloches messages/notifs
  *(à confirmer si Poker a besoin des cloches — probablement non en Phase 2 initiale)*.

---

## 4. Points transverses

- **JWT** : rotation + blacklist des refresh (`token_blacklist`), TTL longs façon flotte ; **le client
  DOIT persister le refresh roté** sinon éjection (`[[fleet-jwt-refresh-rotation-clients]]`). Beat de
  purge des tokens expirés.
- **Turnstile** : captcha sur register/forgot-password (login exclu), gating sur la clé secrète SSM
  (rollout sûr) — pattern flotte.
- **Email** : `GRAPH_*` (SSM) + Celery + templates traduits ; jamais de SMTP maison.
- **CSP frontend** : ajouter `frame-src`/`script-src https://challenges.cloudflare.com` (Turnstile) +
  `connect-src` Stripe si checkout embarqué. Mettre à jour `deploy/nginx/poker-frontend.conf`.
- **SSM** : nouvelles clés `/poker/prod` (JWT_*, GRAPH_*, TURNSTILE_SECRET_KEY, STRIPE_*) et
  `/poker-frontend/prod` (TURNSTILE_SITE_KEY, STRIPE_PUBLISHABLE_KEY) — via CloudShell.
- **RGPD** (scope §10) : l'offre payante stocke emails + données d'équipe → registre minimal
  (finalité, rétention, **droit à l'effacement**, hébergement UE) ; l'export du board fait **sortir**
  de la donnée d'équipe → couvrir. À cadrer avant mise en vente.

---

## 5. Décisions produit à confirmer (avant/pendant P2.1–P2.2)

| # | Question | Défaut proposé |
|---|----------|----------------|
| A | **Magic-link** login en plus de mot de passe ? | Oui (comme tm/quizonline) — faible coût |
| B | **Rôles d'équipe** : owner/admin/member suffisent ? | Oui, 3 rôles |
| C | **Board** : table persistée vs vue calculée à la volée ? | Vue calculée + `Board` léger pour l'ordre |
| D | **Stripe** : un forfait unique ? essai gratuit ? | 1 forfait équipe, essai 14 j — **à confirmer** |
| E | **Cloches** messages/notifs dans la nav authentifiée ? | Non en P2 initiale (pas de besoin identifié) |
| F | **Ordre business** : billing tôt (P2.2) ou après les features (P2.6) ? | Après — livrer la valeur d'abord |

---

## 6. Prochaine étape

Valider ce découpage + les décisions §5, puis **plan d'implémentation P2.1 (auth)** task-by-task et
exécution. P2.1 est la fondation ; rien d'autre ne démarre avant.

*Fin du design Phase 2.*

# Delegation Poker Online — Dossier de handoff pour Claude Code

**Rôle de ce document :** point d'entrée unique. À lire **en premier**. Il indexe les specs,
digère les conventions de flotte à respecter, fige l'ordre de construction, et liste ce que
Claude Code doit produire lui-même avant de coder.

**Repos (vides, greenfield) :**
- Backend : `Foxugly/Poker_server` — Django + DRF + **Django Channels** (ASGI)
- Frontend : `Foxugly/Poker_frontend` — Angular 21 SPA

**Guidelines de flotte (sur la machine de dev) :** `Foxugly/foxugly-ops` → `OPERATIONS.md`.
**À lire avant tout code** (sections critiques) : §3.15 (layout & design frontend), §3.16 (auth
email-only), §3.12 (onboarding d'un nouveau site), §3.13 (PostgreSQL `DB_*`), §3.5 (secrets SSM),
§3.11 (CI/CD OIDC→SSM), §3.3/§3.4 (process gunicorn/ports), §3.8 (Sentry), §3.9 (health).

---

## 1. Les documents du dossier (dans l'ordre de lecture)

| # | Document | Ce qu'il fige | Statut |
|---|----------|---------------|--------|
| 1 | `delegation-poker-scope.md` | Périmètre, phasage, offres gratuit/payant, modèle de données (esquisse), hypothèses, risques. **Toutes décisions produit tranchées.** | ✅ Verrouillé |
| 2 | `delegation-poker-design-phase1.md` | Design UI de la Phase 1, mappé sur la flotte (emerald-chrome, composants partagés, machine à états du vote). | ✅ Verrouillé |
| 3 | `delegation-poker-realtime-contract.md` | Protocole WebSocket : frontière HTTP/WS, enveloppe, événements 2 sens, `state.sync`, autorité, cas limites. | ✅ Verrouillé |
| 4 | `README-handoff-claude-code.md` | Ce document : index + conventions + ordre de build. | — |

> **Le modèle de données *détaillé* n'est pas encore écrit** (seule l'esquisse existe, scope §6).
> C'est le **livrable n°0** de Claude Code (voir §5), à produire **sur la machine de dev** avec
> les conventions backend (django-parler, Postgres, email-only) et les repos réels en main.

---

## 2. Stack (figée)

**Backend** : Django + DRF + **Django Channels** (WebSocket) + **Redis** (transport Channels) +
**Celery** (async : emails, rendu export board) + **django-parler** (champs traduits en DB) +
**PostgreSQL** (prod) + **simplejwt** (auth Phase 2) + **Stripe** (facturation Phase 2).

**Frontend** : Angular 21 (standalone, signals, `inject()`, `input()`/`output()`) + PrimeNG 21
(preset Aura via `definePreset`) + Tailwind 4 + **Transloco 8** (i18n) + Vitest 4 + TS 5.9 strict.

---

## 3. Conventions de flotte qui *mordent* (digest — la source reste `OPERATIONS.md`)

- **§3.15 — En-tête unique.** `app-page-header` est le **seul** en-tête, sur **toutes** les pages
  routées (liste, form, detail, admin). **Il n'existe PAS de `app-detail-header`.** Structure :
  `[icon]` emerald + `<h1>` **à gauche**, actions projetées **à droite** via `<ng-content>` ;
  bouton « Retour » (`pi-arrow-left`) en 1ʳᵉ action des vues detail. Seules les pages pré-login
  (login/reset/magic-link) en sont exemptes (carte centrée).
- **§3.16 — Auth email-only. ⚠️ PAS de champ `username` sur le User.** `USERNAME_FIELD="email"`,
  pas d'allauth, login simplejwt sur l'email, gate `email_confirmed`. Le « username » des
  participants de Delegation Poker est un **nom d'affichage éphémère**, PAS un identifiant d'auth :
  ne jamais le mapper sur un champ `username` authentifiant.
- **§3.13 — PostgreSQL** en prod, convention `DB_*` 6 variables via SSM ; sqlite en dev seulement.
  **Gotcha** : les tests sqlite passent là où Postgres (NOT NULL / unique) échoue → valider les
  migrations **sur Postgres**.
- **§3.15 — Design.** Accent **emerald** (`#10b981`/`#059669`), topbar sombre, **pas** de mauve/bleu.
  Boutons « add » = `severity="success"` + `pi-plus` en haut-droite. Aide de champ = tooltip
  `pi-info-circle` sur le label (pas de help-text inline). Form system `_forms-meta.scss`
  (`.meta-grid` mono-colonne par défaut, `.cols-2/3/4` en opt-in). Skeletons (pas de spinner nu).
  Responsive mobile-first. **Parité dark-mode obligatoire.**
- **§3.15 — Topmenu.** Ordre d'actions imposé : thème → langue → user (cloches messages/notifs
  **authentifié seulement**). Nav publique standard : Accueil / **Fonctionnalités** / Contribuer /
  À propos → la page `/features` est déjà prévue par la convention.
- **§3.15 — i18n.** 5 catalogues `public/i18n/{fr,nl,en,it,es}.json`, spec `i18n-parity` (jeux de
  clés identiques), **`fr` = source de vérité**. *(Le scope prévoit un repli runtime EN ; à
  arbitrer avec la convention fr-source au moment de l'implémentation — non bloquant.)*
- **§3.12 — Onboarding** : gunicorn `127.0.0.1:8007` (prochain port libre), `/health/` avec check DB,
  Sentry `poker-backend` + `poker-frontend`, secrets SSM `/<app>/prod` (noms nus, SecureString),
  CI/CD **OIDC→SSM** sur `main`, PostgreSQL box-local.

---

## 4. ⚠️ Le seul écart d'infra vs la flotte : ASGI / WebSocket

Toute la flotte tourne en **gunicorn (WSGI) + Celery** derrière nginx. **Aucun site n'utilise ASGI.**
Or **Django Channels exige un serveur ASGI** (daphne ou uvicorn) et une conf nginx WebSocket
(`proxy_set_header Upgrade/Connection`). C'est le **seul point** où ce projet sort du modèle ops
existant. À cadrer explicitement à l'onboarding :

- process **ASGI** dédié (systemd `User=django`, `UMask=0027`, `127.0.0.1:8007`) — nouveau type
  vs le gunicorn WSGI habituel ;
- nginx : bloc `location /ws/` avec upgrade WebSocket vers l'ASGI ;
- **Redis** est déjà présent dans la flotte (pushit) → couche de transport Channels disponible ;
- Celery reste WSGI-compatible (tâches async classiques) — indépendant de l'ASGI.

Garder cette brique **isolée** (`core/realtime/` côté front, une app/consumer dédiée côté back).

---

## 5. Ordre de construction recommandé (Phase 1 = chemin critique)

**Livrable n°0 — Modèle de données détaillé** *(à produire par Claude Code AVANT de coder)*
À partir de l'esquisse (scope §6) + conventions §3.13/§3.16/parler. Doit préciser : tables, champs,
relations, clés ; `TextLayer` + traductions (parler ou table `{code_langue→texte}`, jamais de
colonnes `label_fr`) ; états de session (`idle/open/revealed/acted`) ; mécanique du **snapshot** de
deck ; `Result` (résultat acté) ; `Room` + expiration 8 h ; `participantToken` + rôle. **Cibler
Postgres**, valider migrations sur Postgres.

Puis, dans l'ordre :

1. **Échafaudage backend** conforme §3.12 : app Django, settings `DB_*`, `/health/`, Sentry, ASGI
   (Channels) + Redis, Celery. CI/CD OIDC→SSM.
2. **Échafaudage frontend** : Angular 21 + PrimeNG (preset Aura emerald) + Tailwind + Transloco 5
   langues, chrome de flotte (`app-topmenu` mode `public`, `app-footer`, `app-page-header`,
   `app-language-switcher`), `<p-toast>`.
3. **Deck & cartes** : modèle `Deck/Card/TextLayer` + admin de saisie (Django admin suffit en
   Phase 1) pour créer les 7 cartes + calques + traductions. *(Les illustrations originales sont
   une dépendance de contenu non technique — le code peut être prêt avant le jeu.)*
4. **HTTP salle** : `POST /api/rooms`, `POST /api/rooms/{code}/join`, `GET /api/rooms/{code}`
   (contrat §1) → émission `participantToken` + `deckSnapshot`.
5. **WebSocket** (contrat §4–§8) : consumer Channels, `session.join` → `state.sync`, cycle de vote,
   autorité facilitateur, secret des votes, cas limites, heartbeat, reconnexion, garde-fou
   facilitateur (60 s / premier venu / définitif).
6. **Écrans** E1 Accueil / E2 Rejoindre / E3 Salle (design §3) + `app-delegation-card/deck` +
   panneau de participation + machine à états (design §4).
7. **i18n** : 5 catalogues, libellés des 7 cartes, `vote_state.*`, erreurs. Parité des clés.
8. **Tests** (Vitest + back) : cycle de vote, reconnexion/restauration d'état, secret des votes,
   parité i18n. Le temps réel est la zone à risque → le couvrir en priorité.

**Definition of done Phase 1** : voir scope §5 / design §9 (deux appareils, code+URL, vote temps
réel « X/N » live, révélation simultanée, acter, reset, coupure réseau sans perte, 5 langues,
expiration 8 h, parité dark-mode, zéro markup/CSS dupliqué).

---

## 6. Ce qu'il ne faut PAS construire en Phase 1 (YAGNI / hors périmètre)

Comptes/équipes/auth · historique · **delegation board** + export (CSV/PNG/PDF) · decks custom ·
dos personnalisable · délégation *volontaire* du facilitateur (seul le garde-fou §6.f réassigne) ·
éditeur de carte à la souris (formulaire de coordonnées en admin) · gravure d'image serveur ·
page `/features` (conçue en Phase 1, **publiée en Phase 2**) · Planning Poker / autres types de vote.

Tout ce bloc est **Phase 2/3** — voir scope §5. Le board = **vue d'agrégation** sur les résultats
actés (domaines = sujets de vote), AS-IS/TO-BE = flag par sujet (1 case = 1 tour), cadre visuel
séparant AS-IS/TO-BE.

---

## 7. Décisions déjà tranchées (ne pas ré-ouvrir)

Temps réel WebSocket · 7 niveaux fixes mais type de vote abstrait en DB · identité nommée (nom
d'affichage) éphémère anonyme / email-auth en payant · résolution en code, données en DB · snapshot
deck dans la session · carte = image + N calques texte (overlay CSS/SVG, pas de gravure serveur) ·
jeu de cartes original (pas d'artwork Management 3.0) · deck custom = payant · board = Phase 2 ·
Stripe par équipe · 5 langues (FR/NL/EN/IT/ES), UI par participant · expiration salle 8 h · code
salle insensible à la casse, caractères ambigus exclus · vote modifiable tant que non révélé ·
révélation dès ≥1 vote · reconnexion restaure l'état · upload dos jpg/png/webp <5 Mo SVG exclu
validation serveur · export board CSV/Excel + PNG/PDF (rendu async Celery) · AzureAD abandonné.

---

*Fin du dossier de handoff.*

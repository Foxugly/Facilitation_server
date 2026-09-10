# Delegation Poker Online — Spécification de périmètre (Scope)

> Document de cadrage. Version consolidée issue de la phase d'analyse.
> Objectif : figer le périmètre, le phasage et le modèle de données avant tout développement.

---

## 1. Contexte & objectif

Application web de **Delegation Poker** (framework Management 3.0) permettant à une équipe de voter, en **temps réel**, sur un niveau de délégation pour une décision donnée, puis de révéler les votes simultanément pour ouvrir la discussion et **acter un résultat**.

- **Backend** : Django (+ Django Channels)
- **Frontend** : Angular
- **Temps réel** : WebSocket (Django Channels + **Redis** comme couche de transport)
- **Multilingue** : oui, dès la v1
- **Public visé** : équipes agiles, managers, facilitateurs

Le produit se décline en **deux offres** :
- **Offre gratuite** : salles anonymes, éphémères, sans compte, deck Delegation Poker fixe.
- **Offre payante** : équipes, membres inscrits, historique, votes pré-créés, decks custom, delegation board, envoi d'historique par email.

---

## 2. Principe directeur

> **La DB décrit à quoi ressemble un type de vote ; le code décide comment il se comporte.**

On **abstrait** dès maintenant la notion de « type de vote » (pour accueillir plus tard Planning Poker, decks custom…), mais on **n'implémente** que le **Delegation Poker** en v1. Les données (types, decks, cartes, traductions) vivent en base ; les comportements (logique de révélation / résolution) vivent dans le code.

---

## 3. Décisions d'architecture actées

| # | Décision | Statut |
|---|----------|--------|
| 1 | Temps réel via **WebSocket / Django Channels** (pas de polling), **Redis** en couche de transport | ✅ Acté |
| 2 | **7 niveaux de délégation fixes** en v1, mais modèle abstrait « type de vote » | ✅ Acté |
| 3 | **Identité nommée** (nom d'affichage) pour tous les participants — **≠ identifiant d'auth** (voir note ⚠️) | ✅ Acté |
| 4 | Identité **éphémère** côté anonyme (localStorage/session), **persistée en base** côté compte authentifié | ✅ Acté |
| 5 | Types de vote / decks / cartes **stockés en DB** ; stratégie de résolution **portée par le code** | ✅ Acté |
| 6 | **Snapshot** du deck dans la session au moment du vote → historique immuable (pas de versionnement) | ✅ Acté |
| 7 | Carte = **image de fond + N calques texte** (valeur unique *ou* traductions, position, police, taille) ; pas de calque spécial « numéro » | ✅ Acté |
| 8 | Texte **superposé à l'affichage** (CSS/SVG), pas de gravure d'image côté serveur | ✅ Acté |
| 9 | **Jeu de cartes original à produire** (illustrations maison), pas d'usage de l'artwork Management 3.0 | ✅ Acté |
| 10 | Éditeur de position **par formulaire de coordonnées** en v1 ; édition visuelle à la souris = extension ultérieure | ✅ Acté |
| 11 | **Historique = résultat acté** (niveau retenu par sujet après discussion), pas les votes bruts | ✅ Acté |
| 12 | **Transfert de rôle** : le facilitateur peut déléguer son rôle à un autre membre (en direct) | ✅ Acté |
| 13 | **Deck custom = offre payante uniquement** | ✅ Acté |
| 14 | **Delegation Board (AS-IS / TO-BE)** = livrable phare de la **Phase 2** (persistance d'équipe requise) | ✅ Acté |
| 15 | **Lien public / iframe** du board = sous-phase séparée (2b / 3), avec cadrage sécurité dédié | ✅ Acté |
| 16 | **AzureAD / SSO** : abandonné pour le moment | ✅ Acté |

---

## 4. Périmètre

### 4.1 DANS le périmètre (IN)

**Offre gratuite (Phase 1)**
- Page d'accueil : **créer une salle** / **rejoindre une salle**.
- Rejoindre via **code de salle** (6–8 caractères) **ou via URL directe**.
- Saisie d'un **username** par participant (éphémère).
- Le créateur peut donner un **titre** à la salle et définir un **sujet de vote**.
- **Deck Delegation Poker fixe** : 7 cartes (Tell / Sell / Consult / Agree / Advise / Inquire / Delegate — libellés maison), image de fond + texte traduit, **dos de carte imposé**.
- Cycle de vote temps réel :
  - le créateur **ouvre le vote** ;
  - les participants **votent** (vote caché), **modifiable tant que non révélé** ;
  - un **retardataire peut rejoindre et voter** pendant le tour en cours ;
  - affichage en direct de l'**état de participation** (qui a voté / combien) ;
  - le créateur **révèle** tous les résultats simultanément ;
  - possibilité d'**acter le résultat** du sujet (niveau retenu) ;
  - le créateur **réinitialise** (clean) pour un nouveau tour.
- **Reconnexion WebSocket** : à la reprise réseau, le participant **retrouve sa salle et son vote**.
- **Expiration** de la salle après **8 h d'inactivité**.

**Offre payante (Phase 2)**
- **Comptes authentifiés** (username persisté, email).
- Création d'une **équipe** et inscription de **membres**.
- **Décks custom (n)** : création de decks personnalisés (cartes, fonds, calques texte, traductions).
- **Dos de carte personnalisable** : **liste prédéfinie + upload** (voir validation §10).
- **Délégation du facilitateur** à un autre membre de l'équipe (transfert de rôle en direct).
- **Votes pré-créés** avec titres, préparés en amont d'une session.
- **Historique** des sessions passées (**résultats actés**), consultable **par date**.
- **Envoi par email** du lien vers l'historique d'un jour, **à tous les membres de l'équipe** ; **lien à connexion requise**.
- **Delegation Board** : **vue d'agrégation** (pas un écran édité à la main) construite par-dessus les **résultats actés**. Les **domaines de décision = les sujets de vote** (aucun référentiel à produire). Chaque sujet peut porter un flag **AS-IS** et/ou **TO-BE** ; **chaque case cochée = un tour de vote** (« où en est-on ? » puis « où veut-on aller ? »). Board **persistant** d'une session à l'autre, avec un **cadre visuel distinguant AS-IS et TO-BE**.
- **Export du board** : **CSV / Excel** (exploiter la donnée — matrice, quasi gratuit) **+ PNG / PDF** (partager un livrable présentable — rendu serveur **asynchrone via Celery**). Concerne des **données d'équipe** → cadrage RGPD (voir §10).
- **Page publique « Features »** : lien de topbar (mode `public`) vers `/features`, **tableau comparatif gratuit / payant** (contenu = tableau IN du §4, à traduire en 5 langues). Publiée **en Phase 2** (décrit des features payantes réelles).
- **Facturation / abonnement** via **Stripe**, **par équipe** (forfait, membres illimités).

**Administration**
- Gestion des **decks** et des **cartes** (image de fond + calques texte + traductions).
- Saisie des positions de texte par **formulaire de coordonnées**.

### 4.2 HORS périmètre (OUT — pour l'instant)

- ❌ **Deck custom en offre gratuite** → payant uniquement.
- ❌ **Board en lien public / iframe** → sous-phase 2b / 3 (cadrage sécurité dédié).
- ❌ **Éditeur graphique à la souris** (drag & drop, poignées) → extension ultérieure.
- ❌ **Planning Poker** et autres types de vote → prévus dans le modèle, non implémentés.
- ❌ **Gravure d'image côté serveur** / export de cartes en fichiers autonomes.
- ❌ **Invité anonyme dans une salle d'équipe** → interdit (salle payante = membres uniquement).
- ❌ **AzureAD / SSO** → abandonné pour le moment.
- ❌ **Carte « légende » votable** → non ; explication traitée comme texte d'aide hors deck.
- ❌ **Modération automatique des uploads** → non pour l'instant (à réactiver avec le board public).

---

## 5. Phasage

### Phase 1 — MVP gratuit (chemin critique)
Salle anonyme + temps réel + Delegation Poker complet + révélation simultanée + acter le résultat + reset + reconnexion.
**But : prouver que le temps réel fonctionne.** Produit démontrable de bout en bout.

**Critère de « fini » :** deux personnes sur **deux appareils différents** rejoignent une même salle (par code *et* par URL) ; le créateur ouvre le vote ; les deux votent ; l'état « 2/2 » s'affiche **en direct sans rafraîchir** ; la révélation montre les votes **simultanément** ; le résultat peut être **acté** ; le reset relance un tour ; une **coupure réseau ne perd ni la salle ni le vote** ; le tout dans la langue de l'utilisateur.

### Phase 2 — Offre payante (projet à part entière)
Comptes + équipes + facturation + historique (résultats actés) + votes pré-créés + decks custom + dos personnalisable + délégation de facilitateur + **delegation board AS-IS/TO-BE** + envoi email.
⚠️ À traiter comme un **projet distinct**, pas comme un bonus de la v1.

**Critère de « fini » :** un utilisateur crée un compte, monte une équipe, invite des membres ; prépare des votes à l'avance ; une session actée s'archive dans l'historique daté ; l'email de lien part à tous les membres et le lien exige un login ; le rôle de facilitateur se transfère en direct ; un deck custom (fond + dos perso) est créable ; le board AS-IS/TO-BE persiste d'une session à l'autre ; un abonnement se souscrit de bout en bout.

### Phase 2b / 3 — Extensions
Board en **lien public + iframe** (jeton, expiration, révocation, `frame-ancestors`) · éditeur de carte à la souris · Planning Poker / autres types de vote.

---

## 6. Modèle de données (esquisse)

**Configuration (référentiel)**
- `VoteType` — ex. `delegation_poker`. Référence une **stratégie de résolution** (identifiant routé côté code).
- `Deck` — appartient à un VoteType ; contient des cartes ; peut être **standard** ou **custom** (rattaché à une équipe).
- `Card` — appartient à un Deck ; porte une **image de fond**, un **dos** (imposé en gratuit ; prélist/upload en payant) + une liste de **calques texte**.
- `TextLayer` — pour une carte : `{ position (x, y en %), police, taille, couleur, contenu }` où le contenu est soit une **valeur unique**, soit un **jeu de traductions** par langue.

**Sessions & votes**
- `Room` (salle) — code public, titre, créateur, **expiration (8 h d'inactivité en gratuit)**, éphémère (gratuit) ou rattachée à une équipe (payant).
- `Subject` (sujet de vote) — la décision votée ; une salle peut en enchaîner plusieurs ; porte un flag **AS-IS / TO-BE** (chaque dimension cochée = un tour de vote). En Phase 2, un sujet = une **ligne du board**.
- `VoteSession` — un tour de vote ; **embarque un snapshot du deck** (immuable) ; porte le rôle **facilitateur** (transférable).
- `Vote` — `{ participant, valeur, caché → révélé }` ; **modifiable tant que non révélé**.
- `Result` — **résultat acté** d'un sujet (niveau retenu après discussion).
- `Participant` — username + rôle (facilitateur / votant) ; anonyme ou membre authentifié.

**Offre payante**
- `Account` / `User` — username persisté, email.
- `Team` — équipe ; possède des membres, des decks custom, un historique, des boards.
- `TeamMembership` — lien user ↔ équipe.
- `HistoryEntry` — session archivée (résultats actés), rattachée à une équipe, datée.
- `DelegationBoard` — **vue d'agrégation** (non éditée à la main) sur les `Result` actés d'une équipe. Chaque **ligne = un sujet** ; chaque sujet porte une valeur **AS-IS** et/ou **TO-BE** (selon les tours joués) ; **persistante**. Affichage : cadre visuel séparant AS-IS et TO-BE.

> Le **snapshot** (§3.6) garantit qu'une entrée d'historique et un board restent fidèles même si le deck source est modifié plus tard.

---

## 7. Parcours utilisateur (résumé)

1. Arrivée → **Créer** ou **Rejoindre** une salle.
2. Saisie du **username**.
3. (Rejoindre) code de salle **ou** URL.
4. Le facilitateur définit **titre** + **sujet**, puis **ouvre le vote**.
5. Les participants **votent** (caché, modifiable) ; l'état de participation s'affiche en direct ; un retardataire peut rejoindre.
6. Le facilitateur **révèle** → discussion → **acte le résultat** → **reset** ou sujet suivant.
7. (Payant) la session est **archivée** dans l'historique de l'équipe ; les résultats actés **alimentent le board** ; lien envoyable par **email** (login requis).

---

## 8. Décisions actées cette itération (rappel)

| Sujet | Décision |
|-------|----------|
| Durée de vie salles gratuites | **8 h** d'inactivité |
| Code de salle | **Insensible à la casse**, caractères ambigus exclus (O/0/o, I/l/1), **unicité + collisions gérées** |
| Changer son vote | **Autorisé tant que non révélé** |
| Rejoindre après ouverture | **Oui**, rejoint le tour en cours |
| Reconnexion WebSocket | **Restauration de l'état** (salle + vote) |
| Carte « légende » | **Non** (texte d'aide hors deck) |
| Invité anonyme en salle d'équipe | **Non** |
| Destinataires email historique | **Tous les membres** de l'équipe |
| Lien d'historique | **Connexion requise** |
| AzureAD | **Abandonné** pour le moment |
| Deck custom | **Payant uniquement** |
| Board | **Phase 2**, **vue d'agrégation** (domaines = sujets), AS-IS/TO-BE (1 case = 1 tour) ; lien public/iframe en 2b/3 |
| Volumétrie cible | **~10 participants / 10 salles simultanées**, architecture **scalable** |
| Langues v1 | **FR, NL, EN, IT, ES** ; **UI par participant** ; **repli EN** |

---

## 9. Décisions (toutes tranchées à ce jour)

| # | Question | Note |
|---|----------|------|
| A | ~~Modèle d'abonnement (équipe vs siège)~~ | ✅ Tranché : **Stripe, facturation par équipe** (forfait, membres illimités) |
| B | ~~Domaines de décision du board~~ | ✅ Tranché : **pas de référentiel** — domaines = **sujets de vote** ; board = **vue d'agrégation** sur les résultats actés ; AS-IS/TO-BE = flag par sujet, **1 case cochée = 1 tour** |
| C | ~~Formats d'upload de dos + politique SVG~~ | ✅ Tranché : **jpg/png/webp, < 5 Mo, SVG exclu, validation serveur** (voir §10) |
| D | ~~Langues couvertes en v1~~ | ✅ Tranché : **FR, NL, EN, IT, ES** ; **UI par participant** (chacun sa langue) ; **repli EN** |

---

## 10. Hypothèses

- Le **jeu de cartes original** (7 illustrations + dos imposé) est une **dépendance de production non technique** : le code peut être prêt avant le jeu. À anticiper si deadline.
- Le **concept** des 7 niveaux (framework) est réutilisable ; seul l'**artwork** de Management 3.0 ne l'est pas.
- L'**envoi d'email** suppose un service transactionnel (ex. Postmark/SendGrid), une **file asynchrone** (ex. Celery + Redis) et des **templates traduits**.
- Le **multilingue** couvre l'UI, les libellés de cartes et les emails. **Langues v1 : FR, NL, EN, IT, ES.** **Langue de l'UI choisie par participant** (détection navigateur + choix manuel), **repli EN** si langue non couverte. Chaque carte affiche ses libellés dans la langue du participant qui la regarde. Coût de contenu à anticiper : 5 langues × (UI + 7 libellés de cartes + templates d'email).
- **Extensibilité des langues (exigence de conception) :** ajouter une langue doit rester **trivial, sans refactoring**. Conditions à respecter dès le départ :
  1. **Langue = donnée** : libellés stockés en `{ code_langue → texte }` (table de traductions liée), **jamais** de colonnes en dur (`label_fr`…) → ajout de langue = insertion de lignes, **pas de migration**.
  2. **Source unique** de la liste des langues (table `Language` / config) consultée par l'UI, la validation et le fallback → **pas de liste recopiée** en dur à plusieurs endroits.
  3. **Zéro texte en dur** dans le code (Angular *et* templates d'email Django) : tout passe par une **clé de traduction**.
  4. **Fallback centralisé** : traduction manquante → **repli EN** systématique → une langue peut être livrée **incomplète** puis complétée au fil de l'eau.
  - UI via **i18n Angular standard** (un fichier de messages par langue, chargé dynamiquement). Les 5 langues de départ (FR, NL, EN, IT, ES) sont un **point de lancement, pas une limite**.
  - **Limite honnête** : l'ajout est gratuit en *développement*, mais le **contenu** (UI + 7 libellés + emails) reste à **produire et maintenir** pour chaque langue. Coût linéaire et prévisible, jamais un refactoring.
- **Volumétrie de départ** : ~10 participants par salle, ~10 salles simultanées ; architecture pensée **scalable** (Channels + Redis obligatoire dès plus d'un process).
- **⚠️ « username » = nom d'affichage, PAS un identifiant d'auth.** La flotte authentifie **par email uniquement** (§3.16 ops : `USERNAME_FIELD="email"`, **pas de champ `username`**, pas d'allauth, login simplejwt sur l'email). Conséquence pour ce projet : le « username » des participants est un **nom d'affichage éphémère** (anonyme, non authentifiant). En Phase 2, un membre d'équipe **se connecte par email** ; son nom d'affichage est un champ **distinct**. Ne PAS créer de champ `username` authentifiant sur le modèle User (violerait la convention flotte).
- **Base de données** : **PostgreSQL box-local** en prod (convention `DB_*` 6 variables via SSM, §3.13) ; sqlite seulement en dev. **Gotcha migration à anticiper** : les tests sqlite passent alors que les contraintes Postgres (NOT NULL / unique) échouent — valider les migrations **sur Postgres**, pas seulement sqlite.
- **⚠️ Temps réel = brique hors-convention ops.** La flotte tourne en **gunicorn (WSGI) + Celery** derrière nginx (§3.3) ; **aucun site n'utilise ASGI**. Or Django Channels **exige un serveur ASGI** (daphne/uvicorn) et une conf nginx WebSocket (`Upgrade`/`Connection`). C'est le **seul écart d'infrastructure** du projet vs la flotte : nouveau type de process systemd + proxy WS à cadrer au moment de l'onboarding (§3.12). Redis est déjà présent dans la flotte (pushit), donc la couche de transport Channels est disponible.
- **Onboarding flotte** (§3.12) : port gunicorn `127.0.0.1:8007` (prochain libre après tm/8005), endpoint `/health/` avec check DB, Sentry `poker-backend` + `poker-frontend`, secrets SSM `/<app>/prod` (noms nus, SecureString), CI/CD OIDC→SSM, deploy sur `main`.
- **Upload de dos** : **validation technique obligatoire, côté serveur** — liste blanche **jpg / png / webp** (SVG exclu), poids **< 5 Mo**, **dimensions en pixels bornées** (anti-« décompression bombe »), contrôle sur le **contenu réel du fichier** (pas sur l'extension). **Pas de modération de contenu** pour l'instant (contexte payant/équipe).
- **RGPD** : l'offre payante stocke emails, usernames et données d'équipe → prévoir un **registre minimal** (finalité, rétention, **droit à l'effacement**, **hébergement UE**), consentement à la création de compte. **L'export du board** (CSV/PNG/PDF) fait **sortir de la donnée d'équipe** de l'app → couvrir dans le registre.

---

## 11. Risques

| Risque | Impact | Mitigation |
|--------|--------|------------|
| **IP / licence** : usage de l'artwork Management 3.0 dans un produit payant | Élevé (juridique) | Produire des **illustrations originales** (acté). Vérifier les conditions de licence avant diffusion. **Non-juriste : à faire valider.** |
| **Écart infra ASGI** : la flotte est 100% gunicorn WSGI ; Channels impose ASGI (daphne/uvicorn) + proxy WS nginx | Élevé | Nouveau type de process systemd (ASGI) + conf nginx `Upgrade` à cadrer dès l'onboarding (§3.12). Isoler la brique WS ; Redis déjà dispo (pushit). |
| Le temps réel (cœur du produit) sous-estimé au profit de features visibles | Élevé | Garder le WebSocket sur le **chemin critique** ; éditeur d'image et board hors v1. |
| L'offre payante traitée comme un « bonus » | Élevé | La cadrer comme un **projet distinct** (Phase 2). |
| **SVG malveillant** à l'upload (XSS) | Élevé (sécurité) | **Écarté** : SVG exclu de la liste blanche (jpg/png/webp uniquement). |
| **Exposition publique** via board / iframe (données d'équipe, dos inapproprié) | Élevé | Sous-phase dédiée : **jeton + expiration + révocation**, `frame-ancestors`, **réactiver la modération** des uploads visibles publiquement. |
| **RGPD** non traité (comptes, emails, données d'équipe, hébergement) | Moyen-Élevé | Registre minimal, rétention, effacement, hébergement UE dès la Phase 2. |
| Sous-dimensionnement infra temps réel | Moyen | **Redis** dès le départ ; charge cible modeste mais archi scalable. |
| **Rendu serveur** de l'export board (PNG/PDF) sous-estimé (mise en page, polices, 5 langues, dark/light) | Moyen | Réutiliser la brique HTML→PDF/image pour les deux formats ; **génération asynchrone (Celery)** ; CSV/Excel sans rendu (quasi gratuit). |
| Emails en spam (SMTP maison) | Moyen | Service d'envoi transactionnel dédié. |
| Uploads volumineux / stockage non borné | Moyen | Validation technique (poids, dimensions), quotas de stockage. |
| Codes de salle ambigus / collisions | Faible-Moyen | Insensible à la casse, caractères non ambigus, contrôle d'unicité. |

---

*Fin du document.*

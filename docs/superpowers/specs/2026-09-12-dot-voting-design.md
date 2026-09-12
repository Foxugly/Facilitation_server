# Dot Voting — conception

**Date :** 2026-09-12
**Étape du plan :** 6 (« Dot Voting »), première activité neuve après le programme 5a → 5e.
**Statut :** conception validée avec l'utilisateur. **6a est faite et en production** (merge `0c631f6`, correctif `f0071a3`) — voir §9 pour le bilan de ce qu'elle a coûté hors du registre. 6b (weighted_dot_voting) reste à faire.

Cette étape est **l'épreuve de vérité du registre d'activités** : si la promesse « ajouter une activité ne doit toucher que le registre » est tenue, ces deux activités s'ajoutent sans toucher au domaine. Là où elles obligeront à ouvrir `services.py`, le registre sera à élargir — et chaque ouverture est à documenter comme telle, pas à subir.

---

## 1. Deux activités, pas une

| | **Dot Voting** | **Weighted Ranking** |
|---|---|---|
| Jetons reçus | **2n** jetons de poids 1 | **n** jetons de poids 1, 3, 5, 7, 9… |
| Contrainte par item | au plus **n** jetons | au plus **1** jeton |
| Contrainte globale | somme des jetons ≤ 2n | chaque poids utilisé au plus une fois |
| Dépouillement | somme des points par item | identique |
| Sortie | classement, figé à la révélation | identique |

`n` est le **nombre d'items du round**. Aucun budget ne se règle : il se déduit.

**Elles se livrent en deux temps, 6a puis 6b**, et ce découpage a une vertu de mesure : 6a porte toute la plomberie — résultat en payload, validation à l'échelle du round, totaux en direct, vue du facilitateur, activité sans cartes, composants d'interface. 6b ne devrait alors ajouter qu'une entrée de registre et un libellé. **Si 6b déborde du registre, c'est que l'abstraction fuit**, et on saura précisément où : c'est le test le plus honnête qu'on puisse faire passer à la promesse « ajouter une activité ne touche que le registre ».

Les codes suivent la langue des types existants (`delegation_poker`, `fist_of_five`) : `dot_voting` et `weighted_dot_voting`.

**Ce sont deux entrées de registre distinctes**, parce que leurs contraintes n'ont rien en commun — l'une plafonne une quantité par item, l'autre interdit de réutiliser un poids. Elles partagent en revanche l'agrégation, le classement et le rendu. La seconde est le « Weighted Ranking » que le MVP listait à part : il sort de cette conception sans travail supplémentaire.

## 2. Ce qu'un participant envoie

Une réponse porte les **points** que ce participant accorde à **cet item** : `{"points": <entier ≥ 0>}`.

Le même nom pour les deux activités, parce que l'agrégation est la même — c'est la **validation** qui diffère, et c'est le registre qui la porte. Pour Dot Voting, `points` est un nombre de jetons unitaires (0 à n). Pour Weighted Ranking, c'est le poids d'un jeton (1, 3, 5…), et le participant ne peut pas donner deux fois le même.

## 3. Trois validations, trois portées

C'est le point où le registre doit s'élargir, et il faut le voir clairement :

1. **La forme du payload** — une clé `points`, un entier. Le registre le déclare déjà (`payload_schema`).
2. **La valeur, item par item** — dans les bornes de l'activité. Le registre le déclare déjà (`validate_value`), mais son défaut actuel est « la valeur appartient au deck », inapplicable ici : ces activités **n'ont pas de cartes**. Elles le remplacent.
3. **La cohérence de l'ensemble des réponses d'un participant sur le round** — la somme ≤ 2n, ou l'unicité des poids. **Cette portée n'existe pas aujourd'hui** : `cast_response` valide une réponse à la fois. Le registre doit gagner un point d'accroche à cette échelle, faute de quoi la règle atterrirait en dur dans `services.py` et l'activité suivante devrait l'y retrouver.

La troisième est la seule ouverture réelle que cette étape demande au domaine.

## 4. Les jetons ne sont pas obligatoires

Un participant peut n'en placer qu'une partie. Conséquence : « a fini » cesse d'être déductible, et le facilitateur a besoin de savoir **qui n'a pas fini**.

- Le compteur public garde son sens actuel : combien de participants ont **contribué**.
- Le facilitateur reçoit, **lui seul**, ce qu'il reste à placer par participant. Filtré à l'émission, comme tout ce qui lui est réservé.

## 5. La visibilité, et pourquoi elle ne casse pas le secret

Le facilitateur décide si les votes sont visibles pendant le vote. Ce réglage vit dans `Round.config` — le premier usage réel de ce champ, posé en 5c.

**Ce qui devient visible, ce sont les totaux par item, jamais le lien participant → jetons.** L'invariant structurel du dépôt — le serveur ne construit pas les valeurs individuelles avant la révélation — reste donc entier : en mode visible, il construit un **agrégat**, qui ne dit rien de personne.

Le défaut est le secret. L'ouverture est un choix explicite du facilitateur, jamais un oubli de configuration.

## 6. La sortie : un classement figé

À la révélation, le classement est **figé** — somme des points par item, du plus haut au plus bas. Il devient un résultat stable, que l'historique conserve et que le chaînage peut reprendre.

C'est ce qui **allume le « top N »** du chaînage, resté inutilisable depuis 5e faute d'activité sachant classer. Le registre expose déjà la capacité (`canRank` dans l'agenda) : ces activités seront les premières à la déclarer, et l'interface s'allumera d'elle-même.

Cela exige de lever la dette identifiée en 5b : `Result` ne porte qu'une valeur de carte. Il lui faut un payload, **additif** — le poker garde son champ actuel.

## 7. Une activité sans cartes

Tout le runtime tire le type d'activité du snapshot de deck figé sur le round. Une activité sans cartes s'y range sans tordre l'architecture : un deck sans carte active produit un snapshot à `cards: []` portant le bon `voteType` et la bonne `resolutionStrategy`.

Conséquence à ne pas manquer : la règle par défaut « la valeur jouée appartient au deck » refuserait **tout**. C'est précisément le rôle du registre de la remplacer — et la preuve que l'architecture de 5c tenait.

## 8. Ce que cette étape ne fait pas

- **Pas d'état `CLOSED`.** Ces deux activités révèlent ; elles n'ont pas besoin d'un état de fin sans révélation. La question se rouvrira avec la première activité qui ne révèle rien — un brainstorming.
- **Pas de glisser-déposer.** Décision de conception du projet, prise pour le tactile.
- **Pas de changement du poker.** Son dépouillement, son `Result` et son contrat restent identiques.

## 9. Bilan de 6a — ce qu'il a fallu ouvrir hors du registre

6a est faite. Cette section mesure la promesse posée en tête de ce document :
« ajouter une activité ne doit toucher que le registre ».

**Côté serveur — la promesse tient.** Aucune condition « si l'activité est X »
ne subsiste dans `realtime/services.py` : il n'y reste que des prédicats de
CAPACITÉ interrogés sur le registre. Trois ouvertures ont été faites, et chacune
est une **extension du registre**, pas un contournement :

1. `validate_responses` — la validation à l'échelle du round, la seule ouverture
   que la conception (§3) avait annoncée. `cast_response` l'appelle avant
   d'écrire, en lui passant les réponses déjà posées par ce participant.
2. `validate_value` remplace la règle par défaut « la valeur appartient au deck »,
   inapplicable à une activité sans cartes (§7).
3. `aggregate` / `rank_value` / `freeze_results` écrivent le classement dans le
   nouveau `Result.payload`.

**Limite d'abstraction connue, à consigner telle quelle :** `freeze_results`
groupe TROIS conséquences en un seul drapeau (figer à la révélation, produire un
résultat, enregistrer une décision du facilitateur). Une activité de type ROTI —
qui voudrait figer à la révélation ET enregistrer une décision — n'est donc pas
exprimable aujourd'hui. C'est la première fuite identifiée de l'abstraction ; la
signaler vaut mieux que de la découvrir en 6c.

**Côté client — la promesse tient à ~70 %.** Ce qui a dû sortir du registre :
les verbes du service de socket (légitime : c'étaient des messages du contrat
que le poker n'avait jamais eu l'occasion d'exercer), les fichiers de traduction
(mécanique), et surtout `protocol.ts`, devenu une **union par optionalité** que
chaque activité élargira. **Avertissement pour 6b** : la variante pondérée sera
un FAUX POSITIF comme mesure — jumelle de 6a par construction, elle réutilisera
les mêmes champs. La bonne mesure sera le nombre de lignes ajoutées à
`protocol.ts`, pas le nombre de fichiers touchés. Les deux gestes qui rendraient
la promesse vraie côté front : typer l'entrée de registre par ses payloads
plutôt que par un type global, et sortir l'état d'activité du service de room.

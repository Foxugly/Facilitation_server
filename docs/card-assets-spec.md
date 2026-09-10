# Delegation Poker — Card asset specification

Spec for the **standard Delegation Poker deck** artwork (7 card fronts + 1 shared back)
and the technical constraints for uploading them. The deck data is already seeded
(`manage.py seed_delegation_deck`); only the images are a content dependency.

> The game is fully functional **without** the artwork — cards render with the number +
> translated level name overlaid on an empty surface. Uploading real backgrounds only
> replaces that surface.

---

## 1. The 7 cards (already in the database)

| # | `value` | `slug` | EN | FR | NL | IT | ES | Meaning (Management 3.0) |
|:-:|:-:|---|---|---|---|---|---|---|
| 1 | `1` | tell | Tell | Dire | Vertellen | Dire | Decir | I decide and announce |
| 2 | `2` | sell | Sell | Vendre | Verkopen | Vendere | Vender | I decide, then convince |
| 3 | `3` | consult | Consult | Consulter | Raadplegen | Consultare | Consultar | I ask input, then decide |
| 4 | `4` | agree | Agree | S'accorder | Afspreken | Concordare | Acordar | We decide together |
| 5 | `5` | advise | Advise | Conseiller | Adviseren | Consigliare | Aconsejar | I advise, they decide |
| 6 | `6` | inquire | Inquire | S'enquérir | Informeren | Informarsi | Indagar | They decide, then inform me |
| 7 | `7` | delegate | Delegate | Déléguer | Delegeren | Delegare | Delegar | Fully delegated to them |

`value` is the language-agnostic vote value; `slug` is the stable i18n key. Names are stored
as django-parler translation rows (one per language) — adding a language = inserting rows.

---

## 2. Image format & size

| Property | Value |
|---|---|
| **Aspect ratio** | **5 : 7 (portrait)** — enforced by the card component (`aspect-ratio: 5/7`) |
| **Recommended resolution** | **1000 × 1400 px** (min 750 × 1050; larger is fine, it's downscaled) |
| **File format** | **`.webp` (preferred)**, `.png`, or `.jpg` — **no SVG** |
| **Max file size** | **< 5 MB** per image |
| **Colour** | RGB (or RGBA for transparency); sRGB profile |
| **Assets needed** | **7 fronts** (one per card) + **1 back** (shared by the whole deck) |

Non-5:7 images are not rejected but will be `cover`-cropped to 5:7 — design at 5:7 to avoid
surprises.

---

## 3. ⚠️ Text is overlaid in CSS — do NOT bake it into the image

The **number** and the **level name** are drawn by the app **on top of** the background
(so they stay crisp and follow each viewer's language). The illustration must leave these two
zones clear and legible:

| Layer | Content | Centre position | Height | Style (default) |
|---|---|---|---|---|
| **Number** | `1`–`7` | **x = 12 %, y = 12 %** (top-left) | 9 % of card height | bold 700, **white `#ffffff`**, centred |
| **Level name** | Dire / Tell / … | **x = 50 %, y = 82 %** (bottom-centre) | 7 % of card height | semibold 600, **white `#ffffff`**, centred |

- Positions are the **centre** of each text block, as a **percentage of the card** (responsive).
- Default text colour is **white** → keep those two areas dark/contrasted, **or** change the
  colour per card.
- Every one of these values (position, size, weight, colour, alignment) is **editable per card
  in the Django admin** (`decks › Text layers`), so nothing here is locked in stone.

---

## 4. Upload procedure (Django admin)

1. Admin URL: **`https://facilitation-api.foxugly.com/admin/`** (requires a **superuser** — ask ops to
   create one if needed).
2. **Card fronts:** *Decks › Cards* → open each card → set **Background image**.
3. **Card back:** *Decks › Decks* → open the standard deck → set **Card back image**.
4. Media is stored under `media/decks/cards/` and `media/decks/backs/` on the API host and
   served from `https://facilitation-api.foxugly.com/media/…`.

> **⚠️ Snapshot caveat.** Each room freezes an immutable copy of the deck at creation. **Rooms
> created before the upload keep the old (placeholder) images**; create a **new room** after
> uploading to see the real artwork.

---

## 5. Upload validation constraints

- **Phase 1 (standard deck, staff-only):** uploaded through the admin by a trusted superuser.
  Django + Pillow validate that the file is a real image; recommend the format/size above.
- **Phase 2 (user-uploaded custom card backs):** stricter server-side validation (scope §10):
  - Whitelist **jpg / png / webp only** — **SVG excluded** (XSS risk).
  - **< 5 MB**, and **pixel dimensions bounded** (anti-“decompression bomb”).
  - Validate on the **real file content** (magic bytes), **not** the extension.
  - No content moderation yet (private/team context); revisit when boards go public.

---

*Fin de la spec.*

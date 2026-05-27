# Redirects & Banners — Database Schema

> Doc destinée au backend (Claude Code). Décrit les deux tables créées côté bot et la logique métier associée.
> La base de données est partagée : bot et backend lisent/écrivent dans la même instance PostgreSQL.

---

## Table `redirect_links`

### DDL

```sql
CREATE TABLE redirect_links (
    id          SERIAL PRIMARY KEY,
    domain      TEXT        NOT NULL,
    path        TEXT        NOT NULL,
    description TEXT        NOT NULL,
    added_by    BIGINT      NOT NULL,   -- Discord user ID du dev qui a ajouté
    added_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (domain, path)
);

CREATE INDEX idx_redirect_links_domain ON redirect_links(domain);
```

### Colonnes

| Colonne | Type | Notes |
|---|---|---|
| `id` | `SERIAL` | Clé primaire auto-incrémentée |
| `domain` | `TEXT` | Domaine principal, ex : `moddy.app` (sans `https://`) |
| `path` | `TEXT` | Chemin absolu, ex : `/privacy`. **Toujours préfixé par `/`** (enforced côté bot et à enforcer côté backend) |
| `description` | `TEXT` | Description lisible de la redirection |
| `added_by` | `BIGINT` | Discord user ID du dev qui a créé l'entrée |
| `added_at` | `TIMESTAMPTZ` | Date de création (auto) |

### Contraintes

- `UNIQUE (domain, path)` : pas de doublon pour un même couple domaine + path.
- Le path commence **toujours** par `/`. Le bot l'enforce à l'insertion ; le backend doit faire de même.

### Usage backend

Le backend doit :
1. **Lire** les redirections pour les servir (ex : `GET /r/:path` ou routing custom).
2. Optionnellement exposer une **API admin** (dashboard) pour créer/supprimer des entrées — dans ce cas, reproduire la contrainte `/` sur le path.
3. La colonne `added_by` est un Discord user ID (entier 64 bits), pas un ID backend.

### Exemple de ligne

```
id=1, domain='moddy.app', path='/privacy', description='Privacy policy', added_by=123456789, added_at=2026-05-27T...
```

---

## Table `banners`

### DDL

```sql
CREATE TABLE banners (
    id              SERIAL      PRIMARY KEY,
    message         TEXT        NOT NULL,           -- Markdown accepté
    type            TEXT        CHECK (type IN (
                        'announcement', 'incident', 'maintenance',
                        'information', 'warning', 'resolved'
                    )),
    icon_svg        TEXT,                           -- SVG brut (custom seulement)
    color           VARCHAR(7),                     -- Hex #RRGGBB (custom seulement)
    show_dashboard  BOOLEAN     NOT NULL DEFAULT TRUE,
    show_website    BOOLEAN     NOT NULL DEFAULT TRUE,
    is_active       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT banners_type_or_custom CHECK (
        (type IS NOT NULL AND icon_svg IS NULL AND color IS NULL)
        OR
        (type IS NULL AND icon_svg IS NOT NULL AND color IS NOT NULL)
    )
);

-- Une seule bannière active à la fois
CREATE UNIQUE INDEX idx_banners_single_active
    ON banners (is_active) WHERE is_active = TRUE;
```

### Colonnes

| Colonne | Type | Notes |
|---|---|---|
| `id` | `SERIAL` | Clé primaire |
| `message` | `TEXT` | Contenu du bandeau. Markdown supporté côté frontend |
| `type` | `TEXT \| NULL` | Type prédéfini (voir valeurs ci-dessous). `NULL` si bannière custom |
| `icon_svg` | `TEXT \| NULL` | SVG brut de l'icône. `NULL` si bannière typée |
| `color` | `VARCHAR(7) \| NULL` | Couleur hex `#RRGGBB`. `NULL` si bannière typée |
| `show_dashboard` | `BOOLEAN` | Afficher sur le dashboard admin |
| `show_website` | `BOOLEAN` | Afficher sur le site vitrine |
| `is_active` | `BOOLEAN` | Une seule bannière peut être active à la fois |
| `created_at` | `TIMESTAMPTZ` | Date de création |
| `updated_at` | `TIMESTAMPTZ` | Date de dernière modification |

### Types prédéfinis (`type`)

| Valeur | Usage typique |
|---|---|
| `announcement` | Annonce générale |
| `incident` | Incident en cours |
| `maintenance` | Maintenance planifiée |
| `information` | Info neutre |
| `warning` | Avertissement |
| `resolved` | Incident résolu |

### Logique métier — deux modes mutuellement exclusifs

**Bannière typée** (`type` défini) :
- `type` ∈ valeurs ci-dessus
- `icon_svg` = `NULL`
- `color` = `NULL`
- Le frontend utilise l'icône et la couleur associées au type (définis dans le design system)

**Bannière custom** (`type` = `NULL`) :
- `type` = `NULL`
- `icon_svg` = SVG brut (obligatoire)
- `color` = `#RRGGBB` (obligatoire)
- Le frontend rend directement le SVG et applique la couleur

La contrainte `CHECK` en base enforce cette exclusivité — toute tentative d'insertion mixte sera rejetée par PostgreSQL.

### Unicité de la bannière active

L'index partiel `WHERE is_active = TRUE` garantit qu'**une seule ligne peut avoir `is_active = TRUE` à un instant donné**. Pour activer une bannière :

```sql
-- Dans une transaction
UPDATE banners SET is_active = FALSE, updated_at = NOW() WHERE is_active = TRUE;
UPDATE banners SET is_active = TRUE,  updated_at = NOW() WHERE id = $1;
```

Ne pas oublier la transaction pour éviter un état intermédiaire sans bannière active.

### Endpoint API suggéré pour le frontend

```
GET /api/banners/active
```

Réponse quand une bannière est active :

```json
{
  "id": 3,
  "message": "Maintenance prévue le 28 mai de 02h à 04h UTC.",
  "type": "maintenance",
  "icon_svg": null,
  "color": null,
  "show_dashboard": true,
  "show_website": true,
  "is_active": true,
  "updated_at": "2026-05-27T10:00:00Z"
}
```

Réponse quand aucune bannière n'est active :

```json
null
```

Le frontend doit **cacher le bandeau** si la réponse est `null` ou si le champ correspondant à sa surface (`show_dashboard` / `show_website`) est `false`.

### Cache Redis (recommandé)

Clé suggérée : `moddy:banner:active`  
TTL : 60 secondes (ou invalider à chaque `activate`/`deactivate`/`edit`).

Le bot ne lit pas la table `banners` en temps réel — c'est une table backend/frontend uniquement. Le bot peut écrire via les commandes staff `d.banner *`, mais la consommation est côté backend/frontend.

---

## Résumé des droits d'accès

| Table | Bot | Backend |
|---|---|---|
| `redirect_links` | Écriture (via `d.redirect`) + Lecture (liste/info) | Lecture (pour servir les redirects) + optionnellement écriture via dashboard admin |
| `banners` | Écriture (via `d.banner`) | Lecture (pour servir l'API active banner) + écriture via dashboard admin |

---

## Colonnes liées à l'abonnement ajoutées sur `users`

La migration suivante a également été appliquée (nécessaire pour la commande `/subscription`) :

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_interval TEXT;
-- Valeurs attendues : 'month' | 'year' | NULL
-- Écrit par le backend lors de la création/mise à jour d'un abonnement Stripe
```

Le bot lit cette colonne pour afficher "Monthly" ou "Annual" dans `/subscription`. Le backend doit la remplir au moment de la création ou du changement de plan Stripe.

# Components V2 — guide complet (discord.py ≥ 2.6, testé sur 2.7.1)

> Les Components V2 remplacent les embeds : un message est un **arbre de
> composants** (containers, textes, séparateurs, images, boutons, menus) au lieu
> d'un `content` + des embeds + des lignes de boutons. C'est le seul format
> d'interface utilisé chez Moddy.
>
> Règles de mise en forme (titres, couleurs, icônes…) : [DESIGN.md](DESIGN.md).

```bash
pip install -U "discord.py>=2.6"
```

---

## 1. V1 vs V2 en un coup d'œil

| V1 (ancien) | V2 (Moddy) |
|---|---|
| `ui.View` | `ui.LayoutView` (via `BaseView`) |
| `discord.Embed` pour le texte | `ui.TextDisplay` dans un `ui.Container` |
| Boutons uniquement dans des lignes sous l'embed | Boutons et menus **dans** le container, à côté du texte |
| Pas de séparateurs | `ui.Separator` |
| `send(content=..., embed=..., view=...)` | `send(view=layout_view)` — et **rien d'autre** |

---

## 2. Le principe

```python
import discord
from discord import ui

class HelloView(BaseView):          # BaseView hérite de ui.LayoutView
    def __init__(self):
        super().__init__()           # timeout=None (voir DESIGN.md §1)
        container = ui.Container(accent_colour=discord.Colour(0x5865F2))
        container.add_item(ui.TextDisplay("### <:info:123> Bonjour"))
        container.add_item(ui.TextDisplay("Ceci est une carte Components V2."))
        self.add_item(container)

await interaction.response.send_message(view=HelloView(), ephemeral=True)
```

Quand tu passes un `LayoutView` à `send`, discord.py met le flag
`IS_COMPONENTS_V2` sur le message. Conséquences (imposées par Discord) :

- **Pas de `content=`, pas d'`embed(s)=`, pas de sticker, pas de sondage** dans
  le même message. Tout le texte va dans des `TextDisplay`.
- **On ne peut pas revenir en arrière** : un message V2 ne peut pas être édité
  en message V1 (et inversement). Pour « vider » une carte, édite-la avec une
  autre `LayoutView`.
- Les **pièces jointes** ne s'affichent que si un composant les référence
  (`ui.File("attachment://x.txt")`, `ui.MediaGallery`, `ui.Thumbnail`).

---

## 3. Les composants

### 3.1 Composants de mise en page

| Composant | Rôle | Peut contenir |
|---|---|---|
| `ui.Container(*children, accent_colour=, spoiler=)` | Le bloc principal, avec une barre de couleur à gauche | TextDisplay, Separator, Section, ActionRow, MediaGallery, File |
| `ui.Section(*textes, accessory=)` | 1 à 3 textes à gauche + **un** accessoire à droite | `TextDisplay` (ou `str`) ; accessoire = `ui.Thumbnail` **ou** `ui.Button` |
| `ui.ActionRow(*children)` | Une ligne interactive | jusqu'à **5 boutons** ou **1 select** |
| `ui.Separator(visible=True, spacing=small)` | Espace + trait horizontal | — |

### 3.2 Composants de contenu

| Composant | Rôle |
|---|---|
| `ui.TextDisplay("markdown")` | Texte, markdown complet (titres `###`, `-#`, `>`, listes, blocs de code, liens masqués) |
| `ui.Thumbnail(media=url, description=None)` | Petite image, uniquement comme accessoire d'une `Section` |
| `ui.MediaGallery(discord.MediaGalleryItem(url), …)` | Galerie de 1 à 10 images/vidéos |
| `ui.File(media="attachment://nom.ext")` | Affiche une pièce jointe envoyée avec le message |

### 3.3 Composants interactifs

| Composant | Où |
|---|---|
| `ui.Button(label=, emoji=, style=, custom_id=, url=, disabled=)` | Dans un `ActionRow`, ou accessoire de `Section` |
| `ui.Select(options=[discord.SelectOption(...)])` | Dans un `ActionRow` |
| `ui.UserSelect`, `ui.RoleSelect`, `ui.ChannelSelect(channel_types=[...])`, `ui.MentionableSelect` | Dans un `ActionRow` |

> **Règle simple chez Moddy : chaque select va dans son propre `ActionRow`**,
> et l'`ActionRow` va dans le container. Les boutons d'action globaux
> (Retour / Enregistrer / Annuler) vont dans un `ActionRow` **hors** du
> container, en dessous.

---

## 4. Exemples

### 4.1 Texte + séparateur + pied gris

```python
c = ui.Container(accent_colour=discord.Colour(0x5865F2))
c.add_item(ui.TextDisplay(f"### {emojis.INFO} Statistiques"))
c.add_item(ui.TextDisplay(f"**Serveurs** · `{len(bot.guilds)}`\n**Latence** · `{round(bot.latency*1000)} ms`"))
c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
c.add_item(ui.TextDisplay("-# Mis à jour à l'instant"))
view.add_item(c)
```

### 4.2 Section avec avatar à droite

```python
c.add_item(ui.Section(
    ui.TextDisplay(f"### {emojis.USER} **{user.display_name}**"),
    ui.TextDisplay(f"**ID** · `{user.id}`\n**Créé** · <t:{int(user.created_at.timestamp())}:R>"),
    accessory=ui.Thumbnail(media=user.display_avatar.url),
))
```

### 4.3 Section avec bouton à droite (ligne de réglage)

```python
toggle = ui.Button(label="Activé", style=discord.ButtonStyle.success, custom_id="mybot:cfg:toggle")
toggle.callback = self.on_toggle
c.add_item(ui.Section(
    ui.TextDisplay("**Messages de bienvenue**\n-# Envoie un message à chaque arrivée"),
    accessory=toggle,
))
```

### 4.4 Selects dans le container

```python
c.add_item(ui.TextDisplay(f"**Salon**{emojis.REQUIRED_FIELDS}\n-# Où envoyer les messages"))
sel = ui.ChannelSelect(
    placeholder="Choisis un salon",
    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
    min_values=0, max_values=1,
    custom_id="mybot:cfg:welcome:channel",
)
if current_channel:                       # la valeur actuelle se voit dans le select
    sel.default_values = [current_channel]
sel.callback = self.on_channel
row = ui.ActionRow(); row.add_item(sel); c.add_item(row)
```

Lecture des valeurs dans le callback :

```python
async def on_channel(self, interaction: discord.Interaction):
    values = interaction.data.get("values", [])     # liste d'IDs (str)
    channel_id = int(values[0]) if values else None
```

(Sur un select attaché à `self`, `select.values` donne directement les objets
résolus.)

### 4.5 Select à options

```python
sel = ui.Select(
    placeholder="Mode",
    custom_id="mybot:cfg:mode",
    options=[
        discord.SelectOption(label="Automatique", value="auto",
                             description="Transcrit chaque message vocal",
                             emoji=emojis.SYNC, default=mode == "auto"),
        discord.SelectOption(label="Sur demande", value="button",
                             description="Affiche un bouton sous le message",
                             emoji=emojis.PLAY, default=mode == "button"),
    ],
)
```

### 4.6 Liste en citations

```python
for r in reminders:
    c.add_item(ui.TextDisplay(f"> **{r.title}**\n> -# <t:{r.ts}:R> · <#{r.channel_id}>"))
```

### 4.7 Image et fichier joint

```python
file = discord.File(io.BytesIO(png_bytes), filename="chart.png")
c.add_item(ui.MediaGallery(discord.MediaGalleryItem("attachment://chart.png")))
await interaction.response.send_message(view=view, file=file)

txt = discord.File(io.BytesIO(data), filename="export.json")
c.add_item(ui.File(media="attachment://export.json"))
```

### 4.8 Barre d'actions sous le container

```python
c = ui.Container(...)
...
self.add_item(c)

actions = ui.ActionRow()
back = ui.Button(label=t("buttons.back", locale=locale), emoji=discord.PartialEmoji.from_str(emojis.BACK),
                 style=discord.ButtonStyle.secondary, custom_id="mybot:cfg:welcome:back",
                 disabled=self.has_changes)
back.callback = self.on_back
actions.add_item(back)
self.add_item(actions)
```

---

## 5. Rafraîchir une carte

```python
# en réponse à un clic sur la carte elle-même
await interaction.response.edit_message(view=NewView(...))

# après un defer / une carte de chargement
await interaction.edit_original_response(view=NewView(...))

# un message déjà envoyé
await message.edit(view=NewView(...))
```

Construis **une nouvelle vue** à chaque rafraîchissement plutôt que de muter
`self` et renvoyer `view=self` (indispensable pour les vues persistantes, voir
[PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md) §6).

Pattern Moddy : une méthode `build_view()` qui fait `self.clear_items()` puis
reconstruit tout depuis l'état.

---

## 6. Limites à connaître

| Limite | Valeur |
|---|---|
| Composants par message (**tous niveaux confondus**, y compris ceux dans les containers et les rows) | **40** |
| Caractères de texte affichés dans tout le message (somme des `TextDisplay`) | **4000** |
| Boutons par `ActionRow` | 5 |
| Selects par `ActionRow` | 1 |
| Options d'un `Select` | 25 |
| Textes dans une `Section` | 1 à 3 |
| Éléments d'une `MediaGallery` | 1 à 10 |
| `custom_id` | 100 caractères |
| Label de bouton | 80 caractères |
| Placeholder de select | 150 caractères |
| Description d'option | 100 caractères |

discord.py lève `ValueError: maximum number of children exceeded (40)` si tu
dépasses. Pour les longues listes : **pagination** (boutons Précédent/Suivant
avec la page dans le `custom_id`).

Pense à couper les textes dynamiques (`[:80]` sur un label, `[:100]` sur une
description d'option) : une chaîne traduite trop longue fait échouer tout
l'envoi.

---

## 7. Pièges

1. **`content=` + `view=LayoutView`** → erreur 400. Tout le texte dans des `TextDisplay`.
2. **Embed + LayoutView** → impossible. Si tu as vraiment besoin d'un embed
   (cas très rare), utilise une `ui.View` classique pour ses boutons, pas une
   `LayoutView`.
3. **Un `RoleSelect`/`ChannelSelect`/`UserSelect` directement dans un
   container** : mets-le dans un `ActionRow`.
4. **Webhooks** : un webhook qui n'appartient pas à ton application ne peut
   envoyer que des composants **non interactifs** (texte, images, séparateurs),
   et discord.py doit savoir que c'est du V2 (`webhook.send(view=layout_view)`).
5. **Éphémères** : marchent normalement (`ephemeral=True`).
6. **Mentions** : une mention dans un `TextDisplay` notifie comme dans un
   message normal → `allowed_mentions=discord.AllowedMentions.none()` pour du
   texte d'utilisateur.
7. **Émojis dans un bouton** : `emoji=discord.PartialEmoji.from_str("<:name:id>")`
   (ou directement la chaîne `"<:name:id>"`).
8. **Le texte compte pour la limite de 4000** même dans des containers
   différents : une carte très longue doit être découpée en pages.

---

## 8. Résumé

- `BaseView(ui.LayoutView)` → `ui.Container` → `TextDisplay` / `Separator` /
  `Section` / `ActionRow(select ou boutons)`.
- Envoi : `send(view=...)` seul.
- Rafraîchir : nouvelle vue + `edit_message(view=...)`.
- 40 composants, 4000 caractères.
- Le reste (titres, couleurs, icônes, ton) : [DESIGN.md](DESIGN.md).

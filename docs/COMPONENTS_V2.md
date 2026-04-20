
# 📌 **Documentation : Components V2 dans discord.py (2025+)**

Les **Components V2** sont une nouvelle manière de construire des interfaces interactives dans Discord, directement **structurées en “containers”**.
Contrairement aux anciennes Views (V1), où tout était dans des `ActionRow`, les Components V2 permettent :

* des **conteneurs organisés** (`ui.Container`)
* du **texte décoratif** (`ui.TextDisplay`)
* des **séparateurs visuels** (`ui.Separator`)
* des **layouts propres** via `ui.LayoutView`
* des **menus et boutons dans le même container**
* des éléments **désactivés / grisés** pour guider l’utilisateur

L’objectif : **rendre les interfaces plus lisibles et plus proches d’un menu GUI**.

---

## ✅ **1. La base : `ui.LayoutView`**

C’est la nouvelle classe de View.

```python
class MyView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)
```

Elle remplace `ui.View` quand tu veux utiliser des containers / textdisplay / separators.

> ⚠️ **Dans Moddy, on n'hérite jamais directement de `ui.LayoutView`.** Toute
> View doit hériter de `BaseView` (qui étend `ui.LayoutView`) pour bénéficier
> du handler d'erreurs centralisé et du `timeout=None` par défaut.
> Si la View doit survivre à un redémarrage du bot, voir
> **[docs/PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md)** pour la convention
> `custom_id` et le pattern `register_persistent`.

---

## 🧱 **2. Le `ui.Container`**

C’est **le bloc principal** dans lequel tu ajoutes ton contenu.

```python
container = ui.Container()
self.add_item(container)
```

Tu peux y ajouter :

| Type d’élément    | Classe                                    | Utilité                             |
| ----------------- | ----------------------------------------- | ----------------------------------- |
| Texte             | `ui.TextDisplay("texte")`                 | Affichage d’un titre ou explication |
| Séparateur        | `ui.Separator()`                          | Espace visuel entre sections        |
| Ligne interactive | `ui.ActionRow()`                          | Contient boutons / menus            |
| Select / Button   | `ui.RoleSelect`, `ui.Select`, `ui.Button` | Interactions utilisateurs           |

---

## 🧾 **3. Ajouter du texte (titres, descriptions)**

```python
container.add_item(ui.TextDisplay("## Titre\nExplication du module."))
```

**Markdown est supporté.**

---

## ───────── **4. Séparateurs**

```python
container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.large))
```

Deux espacements possibles :

* `large`
* `small`

---

## 🎛 **5. ActionRow (pour menus & boutons)**

```python
row = ui.ActionRow()
container.add_item(row)
```

Ou directement des sous-classes comme dans ton code.

---

## 🎚️ **6. Menus déroulants V2**

Exemple : **Sélecteur de rôles**

```python
role_select = ui.RoleSelect(
    placeholder="Sélectionne des rôles",
    min_values=0,
    max_values=10
)

role_select.callback = callback_function
row.add_item(role_select)
```

Pour pré-sélectionner des valeurs :

```python
role_select.default_values = [role1, role2, ...]
```

---

## 🤖 **7. Boutons dans Components V2**

Tu peux créer une **ligne de boutons** définie dans la vue.

```python
button_row = ui.ActionRow()

@button_row.button(label="Enregistrer", style=discord.ButtonStyle.blurple)
async def save(self, interaction, button):
    ...
```

Puis l’ajouter **à la fin de la vue** :

```python
self.add_item(button_row)
```

---

## 🖼️ **8. Section avec Thumbnail (image à droite)**

Un `ui.Section` permet d'afficher du texte **à gauche** et une image miniature **à droite**, comme une carte.
Il s'ajoute directement dans un `ui.Container`.

```python
container.add_item(
    ui.Section(
        ui.TextDisplay("### Titre"),
        ui.TextDisplay("Ligne de description"),
        accessory=ui.Thumbnail(media="https://cdn.discordapp.com/avatars/123/abc.png?size=256"),
    )
)
```

> La propriété `accessory` accepte un `ui.Thumbnail(media=url)`.
> Le thumbnail est affiché à droite de tout le contenu textuel de la section.

**Cas d'usage typique :** afficher l'avatar d'un utilisateur à côté de ses informations.

**Helper disponible dans `utils/components_v2.py` :**

```python
from utils.components_v2 import create_section_with_thumbnail

section = create_section_with_thumbnail(
    title="### Nom de l'utilisateur",
    thumbnail_url=avatar_url,
    description="Description optionnelle",
)
container.add_item(section)
```

---

## 🌫️ **9. Texte grisé / note / sous-titre**

Tu utilises simplement markdown `-#` ou `>`, par exemple :

```python
ui.TextDisplay("**Rôles utilisateurs**\n-# Sélectionne les rôles ajoutés aux nouveaux membres")
```

`-#` affiche **du texte grisé** automatiquement.

---

## 🔄 **9. Rafraîchir une interface**

Pour modifier l’UI **sans changer d’embed** :

```python
await interaction.response.edit_message(view=self)
```

Si tu fais un follow-up discret :

```python
await interaction.followup.send("✅ Modifié !", ephemeral=True)
```

---

## 🏁 Exemple simple minimal

```python
class ExampleView(ui.LayoutView):
    def __init__(self):
        super().__init__()

        container = ui.Container()
        container.add_item(ui.TextDisplay("## Sélectionne un rôle"))
        row = ui.ActionRow()

        select = ui.RoleSelect(max_values=1)
        select.callback = self.on_select

        row.add_item(select)
        container.add_item(row)

        self.add_item(container)

    async def on_select(self, interaction):
        await interaction.response.send_message("Rôle mis à jour ✅", ephemeral=True)
```

---

# 🎉 Résumé à retenir pour Claude

| Ancien système (V1)                    | Nouveau système (V2)        |
| -------------------------------------- | --------------------------- |
| `ui.View`                              | `ui.LayoutView`             |
| Tout dans des ActionRows               | Organisation en `Container` |
| Impossible d’afficher du texte interne | `TextDisplay` intégré       |
| UI peu structurée                      | UI structurée & claire      |
| Pas de séparateurs                     | `ui.Separator()`            |



# 🎛️ **Menus déroulants (Select) dans les Components V2**

Dans **Components V2**, tu peux ajouter **directement les menus déroulants dans un `ui.Container`**, ou les placer dans un `ui.ActionRow` si tu veux une ligne dédiée.

## Types de sélecteurs disponibles :

| Select                  | Classe                    | Permet de sélectionner             | Exemple d’usage |
| ----------------------- | ------------------------- | ---------------------------------- | --------------- |
| `ui.RoleSelect`         | Rôles du serveur          | Auto-role, permissions             |                 |
| `ui.UserSelect`         | Utilisateurs              | Modération, choix d'utilisateur    |                 |
| `ui.ChannelSelect`      | Salons *texte/vocal*      | Config logs, salon confessions     |                 |
| `ui.CategorySelect`     | Catégories                | Organisation de salons             |                 |
| `ui.MentionableSelect`  | Utilisateurs **et** rôles | Permissions automatiques           |                 |
| `ui.Select` (classique) | Options personnalisées    | Menu de choix “module”, navigation |                 |

---

# ✅ **Comment les ajouter dans un container**

```python
class ExampleView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)

        container = ui.Container()

        container.add_item(ui.TextDisplay("## Configuration"))

        # Rôle Select
        role_row = ui.ActionRow()
        role_select = ui.RoleSelect(
            placeholder="Sélectionner des rôles",
            min_values=0,
            max_values=5
        )
        role_select.callback = self.on_role_select
        role_row.add_item(role_select)
        container.add_item(role_row)

        # Channel Select
        channel_row = ui.ActionRow()
        channel_select = ui.ChannelSelect(
            placeholder="Choisir un salon",
            channel_types=[discord.ChannelType.text]  # Facultatif
        )
        channel_select.callback = self.on_channel_select
        channel_row.add_item(channel_select)
        container.add_item(channel_row)

        # Category Select
        category_row = ui.ActionRow()
        category_select = ui.CategorySelect(
            placeholder="Choisir une catégorie"
        )
        category_select.callback = self.on_category_select
        category_row.add_item(category_select)
        container.add_item(category_row)

        # Ajouter le container à la vue
        self.add_item(container)

    async def on_role_select(self, interaction):
        await interaction.response.edit_message(view=self)

    async def on_channel_select(self, interaction):
        await interaction.response.edit_message(view=self)

    async def on_category_select(self, interaction):
        await interaction.response.edit_message(view=self)
```

---

# 🎯 Notes importantes

| Point                                           | Explication                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| **Les selects se mettent dans des `ActionRow`** | Chaque `Select` doit être dans **une row**           |
| Tu ajoutes la row **au container**              | `container.add_item(row)`                            |
| Le callback se définit **manuellement**         | `select.callback = self.on_select`                   |
| Pas besoin d’embed pour rafraîchir              | `await interaction.response.edit_message(view=self)` |

---

# 🟣 Exemple ultra simple : Select dans container sans row

✅ **Oui, c’est possible MAIS seulement avec `ui.Select` classique**
Les `RoleSelect` / `ChannelSelect` doivent **rester dans une ActionRow**.

```python
container = ui.Container()
select = ui.Select(
    placeholder="Choisis un module",
    options=[
        discord.SelectOption(label="Auto Role", value="autorole"),
        discord.SelectOption(label="Tickets", value="tickets")
    ]
)
select.callback = self.on_select
container.add_item(select)
```

---

# 🔥 Conclusion (pour Claude)

* **Tous les selects spécialisés (`RoleSelect`, `ChannelSelect`, etc.) → dans un `ui.ActionRow`.**
* On place **l’ActionRow dans un `ui.Container`**.
* On affiche la UI via **`ui.LayoutView` + `container.add_item()`**.

-
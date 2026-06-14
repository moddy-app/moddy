# Modals V2 — discord.py (≥ 2.6 / 2.7)

## Installation

Disponible en stable depuis discord.py **2.6.0** (Label/Select/TextInput-dans-Label) et **2.7.0** (FileUpload, Checkbox, CheckboxGroup, RadioGroup). Dernière stable : **2.7.1**.

```bash
pip install -U discord.py
```

Pas besoin de version git/dev pour cette fonctionnalité.

---

## Principe général

Un Modal V2 est composé de **maximum 5 composants top-level**. Chaque composant top-level est soit :

- un `discord.ui.Label` (wrapper d'un composant interactif)
- un `discord.ui.TextDisplay` (texte/markdown statique, sans saisie)

```python
class MyModal(discord.ui.Modal, title="Mon Modal"):
    ...
```

- `title` : obligatoire, max **45 caractères**
- `custom_id` du modal : max **100 caractères**, auto-généré si absent

---

## Ouverture du modal

```python
await interaction.response.send_modal(MyModal())
```

Valide depuis :
- slash commands
- boutons
- composants (selects, etc.)

Invalide depuis :
- commandes texte classiques (`ext.commands`), car pas d'objet `Interaction`

Ne peut pas être envoyé avec un message — uniquement en réponse à une interaction.

---

## Label

Wrapper principal d'un composant top-level.

```python
discord.ui.Label(
    text="Nom",
    description="Entre ton nom",
    component=discord.ui.TextInput(...)
)
```

| Paramètre | Limite |
|---|---|
| `text` | max 45 caractères |
| `description` | max 100 caractères |
| `component` | voir composants compatibles ci-dessous |

### Composants utilisables dans un `Label`

- `TextInput`
- `Select`
- `UserSelect`
- `RoleSelect`
- `ChannelSelect`
- `MentionableSelect`
- `Checkbox`
- `CheckboxGroup`
- `RadioGroup`
- `FileUpload`

`LabelComponent` — *New in 2.6*.

---

## TextDisplay

Composant top-level statique, pour afficher du texte/markdown dans un modal (pas de saisie). Permet par exemple un modal de confirmation contenant uniquement du texte.

*New in 2.6*.

---

## TextInput

```python
discord.ui.TextInput(
    placeholder="Tape ici...",
    style=discord.TextStyle.short,  # ou .paragraph
)
```

| Paramètre | Détails |
|---|---|
| `style` | `short` ou `paragraph` |
| `placeholder` | max 100 caractères |
| `min_length` | 0 → 4000 |
| `max_length` | 1 → 4000 |
| `default` | max 4000 caractères |
| `required` | défaut `True` |
| `custom_id` | max 100 caractères, auto-généré sinon |
| `id` | entier optionnel, identifiant interne *(new in 2.6)* |
| `row` | ⚠️ comportement peu fiable en V2 (voir Pièges) |

### ⚠️ Dépréciation

Le paramètre `label` de `TextInput` est **déprécié depuis 2.6** et devenu **optionnel**. En Modals V2, c'est le `Label` parent qui porte le texte affiché — `TextInput` ne doit plus avoir de `label` propre.

### Récupération de valeur

```python
self.input_name.component.value
```

---

## Select

```python
discord.ui.Select(
    options=[
        discord.SelectOption(label="A"),
        discord.SelectOption(label="B"),
    ],
    min_values=1,
    max_values=2,
)
```

| Paramètre | Limite |
|---|---|
| options | max 25 |
| `placeholder` | max 150 caractères |
| `min_values` | 0 → 25 |
| `max_values` | 1 → 25 |
| `required` | *Only applicable within modals — new in 2.6*. Ignoré hors modal. |

### Valeurs

```python
self.select_name.component.values  # list[str]
```

### Valeur par défaut

Chaque `SelectOption` (idem pour `UserSelect`/`RoleSelect`/`ChannelSelect`/`MentionableSelect`) accepte `default=True` pour pré-sélectionner une ou plusieurs options à l'ouverture du modal.

```python
discord.SelectOption(label="A", default=True)
```

### Règle `min_values` / `required`

`min_values` doit être omis ou ≥ 1 si `required` est omis ou `True`.

---

## UserSelect / RoleSelect / ChannelSelect / MentionableSelect

```python
discord.ui.UserSelect(min_values=1, max_values=2)
discord.ui.RoleSelect()
discord.ui.ChannelSelect()
discord.ui.MentionableSelect()
```

### Valeurs retournées

| Composant | Type de retour |
|---|---|
| `UserSelect` | `list[discord.Member \| discord.User]` |
| `RoleSelect` | `list[discord.Role]` |
| `ChannelSelect` | `list[AppCommandChannel \| AppCommandThread]` (objets résolus partiels — pas garanti d'être des `discord.abc.GuildChannel` complets) |
| `MentionableSelect` | `list[discord.Role \| discord.Member \| discord.User]` |

### ChannelSelect — `channel_types`

```python
discord.ui.ChannelSelect(
    channel_types=[discord.ChannelType.text, discord.ChannelType.voice]
)
```

Filtre les types de salons proposés dans le menu.

---

## Checkbox

Case à cocher simple.

```python
discord.ui.Checkbox()
```

*New in 2.7*. Retourne `bool` (`True`/`False`).

### Valeur par défaut

```python
discord.ui.Checkbox(default=True)
```

`default` (booléen) définit l'état initial coché/décoché à l'ouverture du modal.

### ⚠️ Pas de `required`

`Checkbox` n'a pas de paramètre `required`. Pour rendre une case obligatoire, voir la section *Checkbox obligatoire* plus bas (utiliser `CheckboxGroup`).

---

## CheckboxGroup

```python
discord.ui.CheckboxGroup(
    options=[
        discord.CheckboxGroupOption(label="A"),
        discord.CheckboxGroupOption(label="B"),
    ],
    min_values=1,
    max_values=2,
)
```

| Paramètre | Limite |
|---|---|
| options | max 10 |
| `min_values` | 0 → 10, défaut 0 |
| `max_values` | 1 → 10, défaut 1 |
| `required` | défaut `True` |

Chaque `CheckboxGroupOption` : `label` / `value` / `description`, chacun max 100 caractères. `default=True` pré-sélectionne l'option à l'ouverture du modal.

### Valeurs

```python
self.checkbox_group.component.values  # list[str]
```

*New in 2.7*.

---

## RadioGroup

Sélection unique exclusive via boutons radio. Valide dans un `Label`. *New in 2.7*.

---

## Checkbox obligatoire (technique officielle)

Discord ne permet pas de rendre une `Checkbox` simple obligatoire. Solution recommandée par la doc Discord :

```python
discord.ui.CheckboxGroup(
    options=[
        discord.CheckboxGroupOption(label="J'accepte")
    ],
    min_values=1,
    max_values=1,
    required=True,
)
```

---

## FileUpload

```python
discord.ui.FileUpload(
    min_values=1,
    max_values=10,
)
```

| Paramètre | Limite |
|---|---|
| `min_values` | 0 → 10, défaut 1 |
| `max_values` | 1 → 10, défaut 1 |
| `required` | défaut `True` |

*New in 2.7* (PR #10307).

### Valeurs

```python
self.upload.component.values  # list[discord.Attachment]
```

### Contraintes importantes

- Aucun intent particulier requis.
- Discord **ne transmet pas le contenu du fichier** dans l'interaction : seulement des `discord.Attachment` (URL CDN), à télécharger soi-même.
- La taille max de fichier dépend de la **limite d'upload de l'utilisateur dans le canal** — impossible de la restreindre/valider côté composant.
- ⚠️ Sécurité : ne jamais exécuter de code provenant d'un fichier uploadé par un utilisateur.

---

## Paramètres communs (tous composants interactifs)

| Paramètre | Description |
|---|---|
| `custom_id` | max 100 caractères, auto-généré (`os.urandom(16).hex()`) si absent. Utile pour vues/modals persistants. |
| `id` | entier optionnel identifiant le composant dans le payload d'interaction *(new in 2.6)*. Différent de `custom_id`. |
| `required` | comportement variable selon le composant (voir tableaux ci-dessus) |
| `disabled` | ⚠️ **interdit dans les modals** — déclenche une erreur API si utilisé. Ne pas l'utiliser sur un composant placé dans un modal. |
| `row` | ⚠️ peu fiable en V2, voir Pièges |

---

## Limites globales

| Élément | Limite |
|---|---|
| Composants top-level par Modal | **5** (chacun = `Label` ou `TextDisplay`) |
| `Modal.title` | 45 caractères |
| `Modal.custom_id` | 100 caractères |
| `Label.text` | 45 caractères |
| `Label.description` | 100 caractères |
| `TextInput.placeholder` | 100 caractères |
| `TextInput.min_length` | 0–4000 |
| `TextInput.max_length` | 1–4000 |
| `TextInput.default` | 4000 caractères |
| `Select` options | 25 |
| `Select.placeholder` | 150 caractères |
| `CheckboxGroup` options | 10 |
| `FileUpload` fichiers | 10 |

---

## Migration depuis l'ancienne API (Modal V1)

L'ancienne forme reste **fonctionnelle mais dépréciée depuis 2.6** :

```python
# Ancien (déprécié)
class OldModal(discord.ui.Modal, title="Feedback"):
    feedback = discord.ui.TextInput(label="Ton avis", style=discord.TextStyle.paragraph)
```

```python
# Nouveau (V2, recommandé)
class NewModal(discord.ui.Modal, title="Feedback"):
    feedback = discord.ui.Label(
        text="Ton avis",
        component=discord.ui.TextInput(style=discord.TextStyle.paragraph)
    )
```

Côté Discord : « *All interactive components in modals should be placed inside Label components instead.* »

---

## Pièges connus

1. **`row` sur TextInput** : son comportement a changé en V2 et peut être ignoré (issue #10397). L'ordre d'affichage suit l'ordre d'ajout des `Label`.
2. **`disabled` en modal** → erreur API. Ne jamais l'utiliser sur des composants dans un modal.
3. **`TextInput.label`** déprécié — utiliser `Label.text` à la place.
4. Les décorateurs `@discord.ui.label` / `@discord.ui.text_input` **n'existent pas** dans discord.py (Rapptz). Seuls `@discord.ui.button` et `@discord.ui.select` existent. Les composants de modal se déclarent par attributs de classe ou `add_item`, pas par décorateur.

---

## on_submit / on_error

```python
async def on_submit(self, interaction: discord.Interaction):
    ...

async def on_error(self, interaction: discord.Interaction, error: Exception):
    ...
```

---

## Réponse à l'interaction

```python
await interaction.response.send_message("Hello", ephemeral=True)
```

---

## Exemple complet

```python
class Feedback(discord.ui.Modal, title="Feedback"):

    feedback = discord.ui.Label(
        text="Feedback",
        description="Ton avis",
        component=discord.ui.TextInput(style=discord.TextStyle.paragraph),
    )

    consent = discord.ui.Label(
        text="Conditions",
        component=discord.ui.CheckboxGroup(
            options=[discord.CheckboxGroupOption(label="J'accepte")],
            min_values=1,
            max_values=1,
            required=True,
        ),
    )

    async def on_submit(self, interaction: discord.Interaction):
        value = self.feedback.component.value
        await interaction.response.send_message(value, ephemeral=True)
```

---

## Sources

- `github.com/Rapptz/discord.py/blob/master/discord/ui/{text_input,select,label,modal}.py`
- `github.com/Rapptz/discord.py/blob/master/discord/components.py`
- PR #10307 (FileUpload), Issue #10397 (row)
- `discordpy.readthedocs.io/en/stable/interactions/api.html`
- `discordpy.readthedocs.io/en/latest/whats_new.html`
- `discord.com/developers/docs/components/reference`

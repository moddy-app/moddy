# Kit design Moddy (pour un autre bot)

Tout ce qu'il faut pour qu'un autre bot discord.py ait **exactement le même
design que Moddy** : les règles, les docs techniques et les icônes.

Ce dossier est autonome : copie-le tel quel dans le dépôt de l'autre bot (par
exemple dans `docs/design/`). Les docs ne dépendent pas du code de Moddy.

| Fichier | Contenu |
|---|---|
| [DESIGN.md](DESIGN.md) | **À lire en premier.** La logique, les règles, l'anatomie d'une carte, les couleurs, les icônes, les panneaux de config, les erreurs, l'i18n, avec les helpers à copier (`BaseView`, `BaseModal`, cartes) |
| [COMPONENTS_V2.md](COMPONENTS_V2.md) | Components V2 : tous les composants, exemples, limites (40 composants / 4000 caractères), pièges |
| [MODALS_V2.md](MODALS_V2.md) | Modals V2 : Label, TextInput, selects, Checkbox, RadioGroup, FileUpload, `.value` vs `.values`, réponses |
| [PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md) | Vues persistantes : contrat, `custom_id`, `DynamicItem`, autorisation, registre, test |
| [copy_emojis_jsk.py](copy_emojis_jsk.py) | Script **jsk à lancer sur Moddy** : copie tous les émojis de Moddy sur l'application de l'autre bot |
| [remap_emoji_ids.py](remap_emoji_ids.py) | Script local : remplace les IDs d'émojis de Moddy par ceux du nouveau bot dans des fichiers |

---

## Copier les émojis de Moddy sur l'autre bot

Les icônes de Moddy sont des **émojis d'application** : seule l'application
Moddy peut les afficher. Le script les recopie (même nom, même image) dans les
émojis d'application de l'autre bot.

### Ce que fait le script

1. Avec le token de l'autre bot : récupère son application et les émojis
   qu'elle a déjà.
2. Liste les émojis de Moddy : ceux de l'application Moddy, plus tous ceux
   cités dans le code et les traductions, plus les jumeaux `_dark` du thème clair
   (environ 500).
3. Télécharge chaque image depuis le CDN Discord (sans le token) et l'uploade
   sur l'autre application, **sous le même nom**. Un nom qui existe déjà est
   laissé tel quel. Le rate limit de Discord est respecté (attente puis reprise).
4. Tourne **en arrière-plan** et affiche sa progression dans le salon (un
   message édité tous les 10 émojis).
5. À la fin, envoie dans le salon :
   - `emoji_mapping.json` : `ID Moddy → {name, new}` pour chaque émoji ;
   - `emojis.py` : le `utils/emojis.py` de Moddy (toutes les constantes `DONE`,
     `ERROR`, `SETTINGS`…) **déjà réécrit avec les IDs du nouveau bot**, prêt à
     déposer dans l'autre bot ;
   - `echecs.txt` s'il y a eu des échecs.

Relancer le script ne crée pas de doublons : il ne copie que les noms qui
manquent.

### Lancer le script

1. Ouvre [copy_emojis_jsk.py](copy_emojis_jsk.py) et remplace
   `TOKEN_DU_BOT_CIBLE` (**avant-dernière ligne**) par le token de l'autre bot.
2. Sur Discord, dans un salon privé : **`/dev jsk`** (la version slash, qui
   ouvre un modal) et colle tout le script (3,6k caractères, la limite du
   modal est 4000).
3. Suis la progression dans le salon, récupère les fichiers à la fin.

Sécurité du token :
- **Utilise la version slash `/dev jsk`, pas `@Moddy d.jsk <code>`** : avec la
  commande message, le script (donc le token) resterait visible dans un message
  du salon.
- Le panneau de résultat de jsk réaffiche les **400 premiers caractères** du
  code : c'est pour ça que le token est tout en bas, il n'apparaît jamais.
- Les logs staff de jsk masquent les arguments (`[REDACTED]`).
- Le token n'est envoyé qu'à `discord.com/api` ; les téléchargements sur le CDN
  partent sans lui.

Limites :
- Une application peut avoir **2000 émojis** ; Moddy en utilise environ 500.
- Les messages d'erreur éventuels de Discord sont dans `echecs.txt`.

### Remplacer les IDs dans d'autres fichiers

Si tu copies aussi des fichiers qui contiennent des émojis en dur (traductions,
docs, cogs), sur ta machine, dans le dépôt de l'autre bot :

```bash
python remap_emoji_ids.py emoji_mapping.json locales/ docs/ cogs/
```

---

## Mise en route sur l'autre bot

1. Dépose le `emojis.py` généré dans `utils/emojis.py`.
2. Copie `BaseView` / `BaseModal` et les helpers de cartes depuis
   [DESIGN.md](DESIGN.md) (§1 et §6).
3. Mets en place le registre de vues persistantes et son test
   ([PERSISTENT_VIEWS.md](PERSISTENT_VIEWS.md) §8 et §9).
4. Choisis ton préfixe de `custom_id` (`mybot:` dans les exemples).
5. Mets ces règles dans le `CLAUDE.md` de l'autre bot (ou l'équivalent pour
   ton agent) pour qu'elles soient appliquées automatiquement.

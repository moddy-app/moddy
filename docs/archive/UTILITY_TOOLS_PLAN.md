# Plan — Boîte à outils Moddy (≈ 25 commandes + 2 modules automatiques)

## Contexte

Jules veut ajouter à Moddy une série d'outils pratiques pour tout le monde : texte, développement, sécurité, OSINT, images, fichiers, et deux comportements automatiques (liens musique, bouton « Traduire »). La priorité est le **design, la simplicité et l'UI/UX**. Les doublons de la liste sont retirés. Les icônes manquantes sont demandées **avant** de coder.

Constats tirés de l'exploration :

- **Places disponibles.** Le bot a 23 commandes slash globales sur 100, donc la marge est large. La doc officielle Discord autorise maintenant **15 menus contextuels « message »** et non plus 5 : les commentaires de `cogs/text_tools.py` et `cogs/ocr.py` sont périmés et seront corrigés.
- **Odesli (song.link) n'est plus utilisable.** L'API publique répond `401 PUBLIC_API_ACCESS_DEPRECATED`, je l'ai testé. L'API Spotify est aussi fermée aux nouvelles apps (Premium obligatoire, 5 utilisateurs). Il faut donc une résolution maison, décrite au §3.
- **Les options des commandes sont journalisées.** `cogs/command_logger.py:_format_options` envoie leurs valeurs vers les webhooks techniques. Tout secret (mot de passe, JWT) passe donc par un **Modal V2**, jamais par une option.
- **Protection contre les requêtes vers le réseau interne (SSRF).** `utils/safe_http.py` existe déjà (résolveur « IP publiques seulement », redirections revalidées). Tout ce qui contacte une URL ou un hôte fourni par un utilisateur passe par lui.
- **Mémoire.** La RAM fait ~93 % de la facture Railway et le bot repose sur Pillow seul. Chaque dépendance lourde est donc chargée à la demande et justifiée.

### Décisions de Jules

- **Suppression de fond** : dans le bot, avec un modèle léger.
- **`/convert`** : tous les types de fichiers, avec une API externe pour ce qui n'est pas une image.
- **`/whois`, `/dns`, `/ssl`, `/subdomains`** : quatre commandes séparées.
- **Liens musique et bouton Traduire** : actifs par défaut, et désactivables par serveur.
- **Liens musique** : il faut aussi une commande manuelle.

### Doublons retirés

- `/regex`, `/hash`, `/diff` et `/textstats` apparaissaient deux fois.
- `/resize` devient une action de `/image`.
- Pas de « .status » en préfixe : seulement `/status`, car les commandes préfixées sont réservées au staff.
- `/image` garde un bouton « Convertir », qui ouvre le même flux que `/convert`. C'est un raccourci, pas une deuxième commande.

---

## 1. Liste finale des commandes

| # | Commande (nom EN déclaré → nom FR) | Où | Réponse |
|---|---|---|---|
| 1 | `/define` → `/dico` | partout | incognito au choix |
| 2 | `/pwned` (Modal V2) | partout | toujours éphémère |
| 3 | `/tldr` | serveurs uniquement | incognito (par défaut éphémère) |
| 4 | `/regex` (Modal V2) | partout | incognito |
| 5 | `/hash` | partout | incognito |
| 6 | `/diff` (Modal V2) | partout | incognito |
| 7 | `/textstats` | partout | incognito |
| 8 | `/uuid` | partout | toujours éphémère |
| 9 | `/password` | partout | toujours éphémère |
| 10 | `/jwt` (Modal V2) | partout | toujours éphémère |
| 11 | `/color` | partout | incognito |
| 12 | `/status` | partout | incognito |
| 13 | `/company` → `/entreprise` | partout | incognito |
| 14 | `/whois` | partout | incognito |
| 15 | `/dns` | partout | incognito |
| 16 | `/ssl` | partout | incognito |
| 17 | `/subdomains` → `/sous-domaines` | partout | incognito |
| 18 | `/link-check` + menu « Vérifier les liens » | partout | incognito |
| 19 | `/username` → `/pseudo` | partout | incognito |
| 20 | `/image` | partout | incognito |
| 21 | `/convert` | partout | incognito |
| 22 | `/exif` | partout | toujours éphémère (peut contenir du GPS) |
| 23 | `/sauce` + menu « Trouver la source » | partout | incognito |
| 24 | `/music` → `/musique` | partout | incognito |
| — | Module **Liens musique** (`music_links`) | serveurs | réponse publique |
| — | Module **Bouton Traduire** (`translate_button`) | serveurs | réponse publique + traduction éphémère |

Total : 24 nouvelles commandes slash, soit ~47 sur 100, et 2 nouveaux menus « message », soit 7 sur 15.

---

## 2. Système de design commun (le cœur de l'UX)

Un seul gabarit pour tous les outils, dans `utils/tool_card.py` :

- **Structure fixe** :
  1. `### <icône> Titre de l'outil` ;
  2. le **résultat principal en premier**, gros et copiable, dans un bloc de code ;
  3. les détails en lignes `**Libellé** · \`valeur\`` ;
  4. une ligne `-# Source : … · demandé par …`, avec l'attribution des licences (Wiktionary CC BY-SA, WhatsMyName CC BY-SA, data.gouv) ;
  5. les boutons d'action en bas.
- **Couleur d'accent sémantique** :
  - `primary` pour une information ;
  - `success` / `warning` / `error` pour un verdict (pwned, link-check, âge d'un domaine, contraste WCAG, statut d'un service).
- **Chargement** : toute opération de plus d'une seconde affiche d'abord une carte avec l'icône `LOADING` animée, puis cette carte est éditée (modèle `/ocr`).
- **Sorties longues** : un aperçu, plus un fichier `.txt` / `.diff` / `.csv` joint (modèle `make_text_file`).
- **Erreurs** :
  - les erreurs attendues lèvent un `ModdyError`, ou une exception de service avec un `code` traduit en `tools.<outil>.errors.<code>` (modèle `OcrError`) ;
  - la carte d'erreur propose toujours la marche à suivre.
- **Saisie** :
  - options slash pour une valeur courte ;
  - **Modal V2** dès qu'il y a un texte long, un secret ou plusieurs champs (`Label` + `TextInput` / `RadioGroup` / `CheckboxGroup` / `FileUpload`) ;
  - chaque modal contient un `TextDisplay` d'aide en tête.
- **Vues persistantes** : chaque bouton ou menu a un `custom_id` `moddy:tools:<outil>:<action>[:params]`.
  - Si l'état tient dans le `custom_id`, c'est un `DynamicItem`.
  - Sinon (texte de regex, liste de résultats), l'état va dans Redis avec un TTL de 24 h, sous une clé courte placée dans le `custom_id`.
  - Une fois le TTL expiré, le clic répond « relance la commande ». Ce cas est documenté dans `PERSISTENT_VIEWS.md`.
- **Règles CLAUDE.md** :
  - Components V2 seulement, `BaseView` / `BaseModal` ;
  - icônes custom seulement (jamais une icône « à peu près ») ;
  - i18n 5 langues + 32 locales de commandes ;
  - valeurs dynamiques entre backticks ;
  - `AllowedMentions.none()` sur tout texte d'utilisateur republié.

---

## 3. Détail par outil

### Texte

- **`/define mot [langue]`**
  - **Source** : Wiktionary de la langue de l'utilisateur (fr, en, es, pt, de), via l'API MediaWiki `action=parse` et l'analyse des sections Définitions et Synonymes.
  - **Repli** : l'endpoint REST `en.wiktionary` `page/definition`.
  - **User-Agent** : obligatoire et conforme, sinon Wikimedia limite à 10 req/min. Je l'ai constaté : le proxy de cette session est déjà limité.
  - **Carte** : mot, classe grammaticale, 3 définitions par sens, synonymes en ligne, bouton lien « Voir sur Wiktionary ».
  - **Navigation** : un menu « Autres sens » si le mot en a plusieurs.
  - **Cache** : Redis 7 jours.
- **`/tldr [messages: 25|50|100]`**
  - Lit l'historique du salon (le membre doit pouvoir lire ce salon).
  - Fait un résumé à puces avec les liens « aller au message » vers les moments clés.
  - Réutilise la plomberie OpenAI de `cogs/text_tools.py` : nonce, `strip_mentions`, gateway, quota `tldr` (10 par jour et par utilisateur).
- **`/regex`**
  - **Modal V2** : motif, texte de test, `CheckboxGroup` des flags (i, m, s, x).
  - **Moteur** : bibliothèque `regex` avec `timeout=`, pour se protéger des regex catastrophiques (ReDoS).
  - **Carte** : texte avec les correspondances en **__gras souligné__** (le Markdown marche partout, contrairement à l'ANSI sur mobile), le nombre de correspondances, et un tableau des groupes pour les 10 premières.
  - **Bouton « Modifier »** : rouvre le modal pré-rempli, l'état étant dans Redis.
- **`/hash [texte] [fichier] [comparer]`**
  - Sans argument, il ouvre un Modal V2 (texte long + `FileUpload`).
  - Il calcule MD5, SHA-1, SHA-256 et SHA-512 en blocs copiables.
  - L'option `comparer` vérifie une empreinte connue et affiche un verdict vert ou rouge.
  - Les fichiers vont jusqu'à 25 Mo, hachés par morceaux dans un thread.
- **`/diff`**
  - **Modal V2** : « Avant », « Après », et un `RadioGroup` pour comparer par mot ou par ligne.
  - **Sortie** : un bloc `diff` (rouge/vert natif sur tous les clients) et un compteur `+x −y`.
  - **Par mot** : `~~supprimé~~` et `**ajouté**`.
  - Une sortie trop longue part en `.diff` joint.
- **`/textstats [texte]`**
  - Sans texte, il ouvre un Modal V2.
  - **Mesures** : mots, caractères (avec et sans espaces), phrases, paragraphes, temps de lecture (230 mots/min) et de parole.
  - **Score de lisibilité adapté à la langue** : Kandel-Moles (fr), Flesch (en), Fernández-Huerta (es), Amstad (de), Martins (pt). Les syllabes sont comptées via `pyphen`.
  - Le score s'affiche avec un libellé, de « Très facile » à « Très difficile ».

### Sécurité et développement

- **`/pwned`**
  - **Modal V2** avec un avertissement en tête.
  - Calcule le SHA-1 en local, n'envoie que les 5 premiers caractères à `api.pwnedpasswords.com/range/` avec l'en-tête `Add-Padding`.
  - **Verdict** : « vu `N` fois dans des fuites » (rouge) ou « jamais vu » (vert), avec un conseil.
  - Le mot de passe n'est jamais journalisé ni stocké.
- **`/password [longueur] [type: fort | sans ambigus | phrase de passe]`**
  - Utilise `secrets`.
  - Affiche l'entropie en bits et une jauge de force.
  - Bouton « Régénérer » en `DynamicItem` dont le `custom_id` porte seulement les paramètres.
  - Phrase de passe : liste EFF en anglais. Une liste française est à sourcer.
- **`/uuid [version: v4 | v7] [nombre: 1-10]`** : même modèle, avec un bouton « Régénérer ».
- **`/jwt`**
  - **Modal V2** avec un avertissement « ne collez pas un token actif ».
  - Affiche l'en-tête et le payload en JSON indenté.
  - Les dates (`exp`, `iat`, `nbf`) deviennent des timestamps Discord, avec un statut Expiré / Valide / Pas encore valide.
  - Alerte si `alg: none`.
  - Rappelle que la signature n'est pas vérifiée.
- **`/color couleur`**
  - **Entrées acceptées** : `#hex`, `rgb()`, `hsl()`, un nom CSS, un entier de rôle, ou `random`.
  - **Image générée par Pillow** : une pastille, et un « Aa » échantillon sur les fonds Discord clair et sombre. Les valeurs exactes des fonds sont à vérifier au moment du code. La police Inter (licence OFL) sera ajoutée au repo.
  - **Conversions** : HEX, RGB, HSL, HSV, CMYK, entier Discord.
  - **Contraste WCAG** : ratios contre les fonds Discord et contre le texte blanc/noir, avec les badges AA / AAA.
  - Bouton « Couleur aléatoire ».

### Web et OSINT

- **`/status [service]`**
  - **Registre** de ~15 pages Statuspage vérifiées : Discord, GitHub, Cloudflare, OpenAI, Reddit, Twitch, Dropbox, Atlassian, Vercel, Netlify, etc.
  - **Sans argument** : une vue d'ensemble avec une pastille de couleur par service, un menu pour ouvrir le détail (composants dégradés, incidents en cours, maintenances) et un bouton « Actualiser ».
  - Autocomplétion sur le nom du service.
  - Cache de 60 s.
- **`/company recherche`**
  - API `recherche-entreprises.api.gouv.fr`, gratuite et sans clé. Un nom, un SIREN ou un SIRET est détecté automatiquement.
  - Un menu affiche jusqu'à 10 résultats ; chaque option a le SIREN pour valeur, ce qui rend la vue sans état et persistante.
  - **Fiche** : nom, SIREN/SIRET, état actif/fermé, forme juridique, code NAF et libellé, date de création et âge, adresse, tranche d'effectif.
  - Bouton lien vers l'Annuaire des Entreprises.
- **`/whois`, `/dns`, `/ssl`, `/subdomains`**
  - Quatre commandes séparées, adossées à un même service `tools/domain/`.
  - Chacune accepte un domaine ou une URL, et convertit les noms internationalisés en punycode.
  - **`/whois`** :
    - données RDAP (amorce IANA puis le registre) : registrar, création, expiration, statut, serveurs de noms, DNSSEC ;
    - **alerte « domaine créé il y a N jours »** à partir de 30 jours ou moins (couleur warning), et plus forte à 7 jours ou moins.
  - **`/dns`** : enregistrements A, AAAA, CNAME, MX, NS, TXT, CAA et SOA via DNS-over-HTTPS (JSON Cloudflare), sans nouvelle dépendance. Un menu « Type » permet de filtrer.
  - **`/ssl`** :
    - négociation TLS sur le port 443, en vérifiant d'abord que l'IP est publique (même résolveur que `safe_http`) ;
    - affiche le sujet, l'émetteur, le nombre de SAN, la validité et les jours restants, la version TLS, et si la chaîne est valide.
  - **`/subdomains`** :
    - source : `crt.sh` en JSON. Le service est instable (j'ai eu une erreur 502 en le testant), d'où un délai de 30 s, un cache de 6 h et une erreur claire avec un bouton « Réessayer » ;
    - liste dédupliquée (jokers retirés), pagination, export `.txt`.
- **`/link-check url`** et le menu « Vérifier les liens »
  - Suit les redirections pas à pas via `safe_http`, jusqu'à 10 sauts, et affiche la **chaîne** (code HTTP de chaque étape).
  - Vérifications rapides sur le domaine final : âge via RDAP (même service que `/whois`), punycode / homoglyphes, HTTPS.
  - **VirusTotal** :
    - on cherche d'abord un rapport existant, sinon on soumet l'URL ;
    - l'offre gratuite autorise 4 requêtes/min et ses conditions **interdisent l'usage commercial**. Moddy a un Premium : je le signale, et c'est à toi de décider.
  - **urlscan.io** : scan en visibilité `unlisted`. La **capture d'écran** est jointe dans une MediaGallery ; la carte s'édite quand le scan finit (10 à 30 s).
  - Passe par la gateway, avec une limite de débit et un quota de 10 par jour et par utilisateur.
- **`/username pseudo`**
  - **Source** : jeu de données WhatsMyName `wmn-data.json` (~700 sites), mis en cache 24 h.
  - **Requêtes** : 40 en parallèle, 6 s par site, 45 s au total.
  - **Progression** : une barre en direct (« 312/712 sites »).
  - **Résultat** : seulement les profils trouvés, regroupés par catégorie, avec une page par catégorie et un export `.txt`.
  - **Exclusions et limites** : la catégorie NSFW est exclue. Il y a un délai d'attente par utilisateur et 2 recherches simultanées au maximum dans tout le bot.
  - **Limite honnête** : certains sites bloquent les IP de datacenter, donc il peut manquer des profils. La carte l'indique.

### Images et fichiers

- **`/image fichier`**
  - Affiche l'aperçu, le format, les dimensions, le poids et la transparence.
  - Quatre boutons :
    - **Redimensionner** : Modal V2 avec largeur, hauteur ou %, et un `RadioGroup` pour le mode d'ajustement ;
    - **Compresser** : `RadioGroup` léger / équilibré / fort ;
    - **Convertir** : ouvre le flux de `/convert` ;
    - **Retirer le fond**.
  - **Résultat** : poids et dimensions avant → après, et le fichier produit, avec les mêmes boutons pour enchaîner les actions.
  - **Persistance** : l'image source est relue depuis `interaction.message` (ses pièces jointes), donc sans état.
  - **Animations** : les GIF et WebP animés sont gérés, avec un plafond de frames.
  - **Protection** : garde-fou contre les images piégées qui explosent en mémoire au décodage (« bombes de décompression »), en réutilisant le décodage sûr de `automod/image_hash.py`.
  - **Exécution** : traitement dans `to_thread`, avec un sémaphore de 3.
- **Retirer le fond (choix de Jules : dans le bot, modèle léger)**
  - Le modèle **u2netp** (~4,7 Mo, celui de rembg) est exécuté **directement avec `onnxruntime` + `numpy`**.
  - On n'installe pas le paquet `rembg` lui-même : il tire scipy, scikit-image, pymatting et numba, beaucoup trop lourds.
  - Le modèle est téléchargé une fois (empreinte sha256 vérifiée), chargé à la demande, puis **déchargé après 10 min sans usage**.
  - Un seul calcul à la fois.
  - **Qualité** : correcte pour un sujet bien détouré, moins bonne sur les cheveux. La carte ne promet pas plus.
- **`/convert fichier [format]`**
  - Détecte le type et propose un menu **des seuls formats compatibles**, groupés.
  - **Images** : en local via Pillow, avec HEIC d'iPhone (`pillow-heif`) et AVIF.
  - **En local aussi (mise à jour après la question de Jules)** :
    - données : JSON, YAML, TOML, CSV, XML ;
    - tableurs : CSV, XLSX, ODS (`openpyxl`, `odfpy`) ;
    - Markdown ↔ HTML, DOCX → texte/Markdown (`markdown`, `markdownify`, `mammoth`) ;
    - PDF → PNG/texte (`pypdfium2`, `pypdf`) ;
    - archives : zip, tar, 7z (`py7zr`) ;
    - audio et petite vidéo/GIF via `imageio-ffmpeg` : binaire livré par pip, 0 RAM au repos, une conversion à la fois, taille et durée plafonnées.
  - **Seulement la bureautique avec mise en page (DOCX/ODT/PPTX ↔ PDF) et les ebooks** : **API CloudConvert** via la gateway. Sans clé, ces formats sont masqués.
    - Le fichier est importé par URL depuis le CDN Discord, et le résultat re-téléversé sur Discord.
    - Au-delà de la limite d'upload du serveur, un lien de téléchargement valable 24 h est donné à la place.
  - **Coût** : 25 conversions gratuites par jour, puis environ 8 à 9 $ les 500 minutes.
  - **Quota** : 5 conversions non-image par jour et par utilisateur.
- **`/exif fichier`**
  - **Images** : EXIF (appareil, objectif, date, logiciel), GPS avec un bouton lien OpenStreetMap, XMP, texte PNG, profil ICC.
  - **PDF** : via `pypdf`.
  - **Documents Office** : `docProps` lus dans le zip, avec la bibliothèque standard.
  - **Audio** : via `mutagen`.
  - **Si rien n'est trouvé**, la carte le dit honnêtement : Discord retire souvent les métadonnées des images à l'envoi, mais pas toujours. **Avant de promettre quoi que ce soit, on teste** avec un JPEG géolocalisé envoyé normalement, puis en pièce jointe brute ; le résultat sera noté dans la doc.
- **`/sauce [image] [lien de message]`** et le menu « Trouver la source »
  - **SauceNAO** (illustrations) et **trace.moe** (anime : titre, **épisode**, **timestamp**, similarité) sont appelés en parallèle.
  - Seuils de pertinence : 60 % pour SauceNAO, 87 % pour trace.moe.
  - **Boutons liens** « Google Lens », « Yandex », « Bing », « TinEye » qui passent l'URL de l'image (pas d'API publique pour Lens).
  - **Clés** : SauceNAO (clé gratuite) et trace.moe (clé gratuite conseillée : sans clé, c'est seulement 100 recherches par mois et par IP).
  - Passe par la gateway, avec un quota de 15 par jour et par utilisateur.

### Musique

- **`/music lien`**, et le module **`music_links`** (actif par défaut, désactivable)
  - **Détection** : liens `open.spotify.com/(intl-xx/)?(track|album|artist)/…` et `spotify.link/…` (lien court résolu via `safe_http`).
  - **Résolution** :
    - Spotify : lecture des balises `og:` / `music:` de la page publique (titre, artiste, album, durée, date ; testé, ça fonctionne) ;
    - Deezer : API publique, recherche puis meilleure correspondance (titre et artiste normalisés, durée à ±3 s) ;
    - Apple Music : API iTunes Search, même logique ;
    - YouTube Music : **un lien de recherche** `music.youtube.com/search?q=…`, car il n'y a pas d'API gratuite. C'est honnête, mais ça ouvre une recherche et non le titre exact.
  - **Rendu**, volontairement discret :
    - une réponse sans mention, avec `-# **Titre** — Artiste` ;
    - une rangée de boutons liens Deezer / Apple Music / YouTube Music avec les logos ;
    - les boutons liens ne demandent aucune persistance.
  - Cache Redis de 30 jours par identifiant Spotify.
  - **Configuration `/config`** : plateformes affichées (`CheckboxGroup`) et salons ignorés.
  - **Limites** : les playlists sont ignorées (pas d'équivalent sur les autres plateformes). Si Spotify bloque l'IP Railway, le module échoue en silence et la commande affiche une erreur claire.

### Bouton Traduire

- **Module `translate_button`** (actif par défaut, désactivable)
  - **Détection locale gratuite** avec `py3langid` : modèle de 4,6 Mo, plus numpy, déjà requis par onnxruntime.
    - Limité aux ~25 langues courantes.
    - Seulement pour un message de 4 mots ou plus, sans liens, emojis ni code, avec une confiance d'au moins 0,9.
  - **Langues « connues » du serveur**, qui ne déclenchent pas le bouton :
    1. la **langue du serveur** (`guild_locale`) ;
    2. les **langues habituellement parlées**, apprises automatiquement : une langue qui pèse au moins 10 % des messages détectés sur 14 jours glissants (Redis, échantillon minimum de 200 messages) ;
    3. des langues ajoutées à la main dans `/config`.
  - **Réponse** : `-# <drapeau> Anglais détecté` avec un bouton « Traduire » (`DynamicItem` `moddy:autotr:tr:<salon>:<premier msg>:<dernier msg>`).
    - Plusieurs messages d'affilée du même auteur dans les 2 min **mettent à jour la même réponse** au lieu d'en poster une nouvelle.
    - Le message disparaît après un délai réglable (10 min par défaut).
  - **Clic** : traduction **éphémère dans la langue de la personne qui clique** (`get_user_locale`), via DeepL dans la gateway, avec un cache Redis par message et par langue.
  - **Configuration `/config`** :
    - salons ignorés ;
    - langues supplémentaires connues ;
    - délai de suppression ;
    - liste en lecture seule des langues apprises.
  - **Mon interprétation** de « ces 2 trucs doivent pouvoir être désactivés » : chacun des deux modules peut être mis en pause dans `/config`, avec l'état générique « Gérer » existant. Si tu voulais plutôt deux interrupteurs internes (langue du serveur / langues habituelles), c'est facile à ajouter : dis-le-moi.

---

## 4. Architecture et fichiers

- **Logique pure** (sans Discord, testable), nouveau paquet `tools/`, sur le modèle de `aidetector/` et `bumpreminder/` :
  - `tools/hashing.py`, `regex_test.py`, `diffing.py`, `textstats.py`, `color.py`, `jwt_decode.py`, `generators.py` ;
  - `tools/domain/{rdap,doh,tls,crtsh}.py` ;
  - `tools/music.py` (lecture Spotify + correspondance) ;
  - `tools/langdetect.py` ;
  - `tools/image_ops.py`, `bg_removal.py`, `metadata.py`, `status_registry.py`, `whatsmyname.py`, `wiktionary.py`.
- **HTTP vers les API publiques sans clé**, nouveau `services/public_api.py` :
  - une session aiohttp partagée, User-Agent descriptif, limite par hôte, délais, cache Redis ;
  - concerne Wiktionary, HIBP, RDAP, DoH, crt.sh, Statuspage, data.gouv, Deezer, iTunes, pages Spotify et sites WhatsMyName ;
  - exception documentée dans `API_GATEWAY.md` : `/username` fait ~700 appels par recherche, ce qui saturerait la journalisation de la gateway.
- **Gateway** (clés et quotas), nouveaux adaptateurs et clients :
  - `virustotal`, `urlscan`, `saucenao`, `tracemoe`, `cloudconvert` ;
  - OpenAI pour `/tldr` et DeepL pour le bouton Traduire existent déjà ;
  - quotas initialisés dans `db/base.py` ;
  - variables d'environnement à créer : `VIRUSTOTAL_API_KEY`, `URLSCAN_API_KEY`, `SAUCENAO_API_KEY`, `TRACEMOE_API_KEY`, `CLOUDCONVERT_API_KEY`.
- **Cogs**, chargés automatiquement :
  - `cogs/tools_text.py` : define, tldr, regex, diff, textstats ;
  - `cogs/tools_security.py` : pwned, password, uuid, hash, jwt, color ;
  - `cogs/tools_web.py` : status, company, whois, dns, ssl, subdomains, link-check, username ;
  - `cogs/tools_media.py` : image, convert, exif, sauce ;
  - `cogs/music_links.py` : /music.
- **Vues** : `utils/tool_card.py` (gabarit commun), puis `utils/tools_{text,security,web,media}_views.py` et `utils/music_links_views.py`, `utils/translate_button_views.py`.
- **Modules** :
  - `modules/music_links.py` et `modules/translate_button.py` (`DEFAULT_ENABLED = True`, `REQUIRED_BOT_PERMISSIONS = []`, hors socle de base) ;
  - écrans de config dans `modules/configs/` ;
  - branchement dans `cogs/module_events.py` et `cogs/config.py`.
- **Partout** :
  - `utils/persistent_views.py` (enregistrement) ;
  - `stats/registry.py` (métriques des modules) ;
  - `locales/{fr,en-US,es-ES,pt-BR,de}.json`, sous une clé `tools.*` et sous `modules.*` ;
  - les 32 fichiers `locales/commands/*.json`.
- **Nouvelles dépendances**, toutes en wheels, chargées à la demande :
  - `regex`, `pyphen`, `py3langid` ;
  - `numpy` + `onnxruntime` (suppression de fond), `pillow-heif`, `pypdf`, `mutagen` ;
  - la police Inter (OFL), dans `assets/fonts/`.
- **Documentation** :
  - `docs/features/TOOLS.md`, `docs/features/MUSIC_LINKS.md`, `docs/features/TRANSLATE_BUTTON.md` ;
  - mises à jour de `API_GATEWAY.md`, `RAILWAY.md` (variables + empreinte mémoire), `PERSISTENT_VIEWS.md`, `EMOJIS.md`, `CLAUDE.md` ;
  - un journal de session.

## 5. Exécution avec des agents Sonnet (modèle `sonnet`, Sonnet 5.5)

1. **Moi, les fondations** :
   - `utils/tool_card.py`, `services/public_api.py`, le squelette `tools/` ;
   - les constantes d'icônes (une fois tes IDs reçus) ;
   - les squelettes de clés i18n.
2. **Vague 1**, quatre agents Sonnet en parallèle, chacun dans son worktree et sur des fichiers disjoints :
   - A : Texte ;
   - B : Sécurité / dev / couleur ;
   - C : Web / OSINT ;
   - D : Images / fichiers / sauce.
3. **Vague 2**, deux agents Sonnet :
   - E : Liens musique (module + `/music`) ;
   - F : Bouton Traduire.
4. **Moi, l'intégration** :
   - fusion des JSON de locales avec un script qui fusionne les dictionnaires ;
   - relecture du design de chaque carte ;
   - tests et documentation ;
   - commit et push sur `claude/youthful-cray-l7qj4b`.

Chaque prompt d'agent reprendra : CLAUDE.md, le gabarit `tool_card`, les règles de persistance, les Modals V2, l'i18n 5 + 32 langues, et les tests à écrire.

## 6. Vérification

- `pytest` complet, notamment :
  - `tests/test_persistent_views.py`, `test_command_localizations.py`, `test_command_casing.py` ;
  - un nouveau `tests/test_tools_*.py` par thème : logique pure, construction des cartes, codes d'erreur, complétude i18n des 5 langues ;
  - les tests de modules (musique, traduction) sur le modèle de `test_ai_detector.py`.
- **Tests de bout en bout** :
  - un script de test d'intégration contre les vraies API depuis Railway (pas depuis cette session : le proxy y est déjà limité par Wikimedia), à lancer manuellement ;
  - un test manuel EXIF sur Discord (JPEG géolocalisé) avant de figer le texte de `/exif`.
- **Mémoire** : mesurer le RSS au repos, puis après une suppression de fond et après son déchargement. Le noter dans `RAILWAY.md`.

---

## 7. Icônes à fournir (Google Material Symbols)

Il faut à chaque fois **une version blanche + une version `_dark`** (noire).

| Nom à donner | Icône Material | Ce qu'elle représente | Utilisée pour |
|---|---|---|---|
| `dictionary` | `dictionary` | livre ouvert avec un « A » | /define |
| `regex` | `regular_expression` | `.*` | /regex |
| `tag` | `tag` | dièse « # » | /hash |
| `difference` | `difference` | deux feuilles superposées avec +/− | /diff |
| `analytics` | `analytics` | graphique en barres dans un cadre | /textstats |
| `password` | `password` | astérisques `***` | /password |
| `numbers` | `123` | chiffres « 123 » | /uuid |
| `token` | `token` | hexagone (jeton) | /jwt |
| `gpp_maybe` | `gpp_maybe` | bouclier avec un point d'exclamation | /pwned |
| `monitor_heart` | `monitor_heart` | écran avec une ligne de pouls | /status |
| `blue_status` | (rond plein) | pastille bleue, même style que `green_status` | /status : maintenance |
| `grey_status` | (rond plein) | pastille grise, même style | /status : inconnu |
| `business` | `business` | immeuble de bureaux | /company |
| `dns` | `dns` | deux serveurs empilés | /dns |
| `account_tree` | `account_tree` | arborescence | /subdomains |
| `alt_route` | `alt_route` | flèche qui bifurque | /link-check : chaîne de redirections |
| `person_search` | `person_search` | silhouette + loupe | /username |
| `aspect_ratio` | `aspect_ratio` | rectangle avec coins | /image : redimensionner |
| `compress` | `compress` | flèches qui se rejoignent | /image : compresser |
| `transform` | `transform` | flèches de transformation | /convert |
| `auto_fix_high` | `auto_fix_high` | baguette magique | /image : retirer le fond |
| `frame_inspect` | `frame_inspect` | cadre + loupe | /exif |
| `location_on` | `location_on` | épingle de carte | /exif : coordonnées GPS |
| `image_search` | `image_search` | image + loupe | /sauce |
| `movie` | `movie` | clap / pellicule | /sauce : résultat anime (trace.moe) |
| `music_note` | `music_note` | note de musique | /music + module |
| `encrypted` | `encrypted` | bouclier avec cadenas | /ssl (certificat) — optionnel, sinon `lock` existant |

**Logos de marques**, en couleur et donc sans version `_dark` nécessaire :

- **Musique** : `deezer`, `apple_music`, `youtube_music`, `spotify`.
- **Optionnel**, pour les boutons de `/sauce` : `google_lens`, `yandex`, `bing`, `tineye`. Sans eux, ces boutons auront seulement du texte.

**Icônes existantes réutilisées** (même sens) :

- `BOOK` → `/library`, inchangé ;
- `TEXT` → résumé `/tldr`, comme `/summarize` ;
- `CODE`, `data_object` → blocs JSON de `/jwt` ;
- `color` (déjà uploadée, à ajouter en constante) → `/color` ;
- `lock` (id existant) → `/ssl` ;
- `WEB` → `/whois` ;
- `LINK` → `/link-check` ;
- `IMAGE` → `/image` ;
- `TRANSLATE` → bouton Traduire ;
- `HISTORY` → âge du domaine ;
- `REFRESH` → Régénérer / Actualiser ;
- `GREEN_STATUS` / `YELLOW_STATUS` / `RED_STATUS` → états `/status` ;
- `DONE`, `ERROR`, `WARNING`, `INFO`, `LOADING` → verdicts et chargements ;
- `DOWNLOAD` → export ;
- `BACK` / `NEXT` → pagination.

> Si `lock` te paraît trop générique pour `/ssl`, uploade `encrypted` (ligne ci-dessus) et je l'utiliserai à la place.

En attendant tes IDs, je peux coder sans icône aux endroits concernés.

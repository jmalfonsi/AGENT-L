# Documentation web AGENT-L

Ce répertoire contient le site Mintlify de la documentation Core et Studio.

## Prévisualiser

```bash
cd docs
npm ci
npm run login       # une fois : authentification et choix du projet Mintlify
npm run dev
```

Le site est servi par défaut sur <http://localhost:3000> avec rechargement
automatique. Une fois la session Mintlify ouverte, la recherche plein texte et
l’assistant sont également disponibles.

La recherche de la prévisualisation locale utilise l’index du projet Mintlify :
elle exige donc une connexion préalable avec `npm run login`. La session est
ensuite conservée par le CLI. Sans connexion, `npm run dev` reste utilisable
pour contrôler la mise en page, mais le CLI désactive la recherche et affiche
`Run mint login in the cli to activate search`.

### Serveur Ubuntu sans environnement graphique

Le CLI actuel stocke ses jetons via le protocole Secret Service. `libsecret`
n’est que la bibliothèque cliente : un serveur headless doit aussi fournir le
service, ici GNOME Keyring. Cette installation n’ajoute pas le bureau GNOME :

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends gnome-keyring
npm run login
npm run dev
```

Si le paquet vient d’être installé dans une ancienne session SSH et que l’erreur
`org.freedesktop.secrets` persiste, reconnectez la session SSH avant de relancer
`npm run login`.

## Valider

```bash
npm run validate
```

`docs.json` définit l’identité et la navigation. Les pages publiées sont les fichiers MDX explicitement listés dans cette navigation ; les documents d’ingénierie présents dans le répertoire restent hors du site.

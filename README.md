# moviegraphrag

Pipeline contrôlé de questions sur les films : Neo4j vérifie les contraintes,
E5 classe les candidats, et chaque résultat conserve ses preuves. Pas de Cypher
généré par le LLM, pas d'agent autonome, pas de seconde base.

## démarrage avec docker

Prérequis : Docker Desktop démarré, environ 4 Go de mémoire disponibles pour les
services et le modèle, accès réseau initial pour télécharger les images et E5.
La consommation et la latence réelles dépendent de la machine.

1. Copier `.env.example` vers `.env` et renseigner `MISTRAL_API_KEY`.
2. Acquérir les deux CSV depuis [Kaggle](https://www.kaggle.com/datasets/tmdb/tmdb-movie-metadata).
3. Les placer sous `data/raw/tmdb_5000_movies.csv` et `data/raw/tmdb_5000_credits.csv`.
4. Exécuter depuis ce répertoire :

```powershell
docker compose up -d --build
docker compose exec api moviegraphrag profile --output data/reports/profile.json
docker compose exec api moviegraphrag ingest --output data/reports/manifest.json
```

L'ingestion télécharge le modèle gratuit et crée les embeddings sur CPU. Elle
peut prendre plusieurs minutes. Les CSV et caches ne sont jamais ajoutés à Git.
La clé Mistral n'est pas nécessaire pour profiler ou ingérer.

Swagger : <http://localhost:8000/docs>. Interface Neo4j : <http://localhost:7474>.
Ports liés à localhost. Le mot de passe d'exemple est destiné au développement local.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/questions `
  -ContentType application/json `
  -Body '{"question":"Quels films de Nolan avec DiCaprio parlent des rêves ?","limit":5}'
```

`/health` vérifie le processus. `/ready` exige un corpus chargé, les index disponibles,
une clé LLM configurée et le modèle chargé dans le processus API. La première question
sémantique charge le modèle en mémoire ; `/ready` ne déclenche aucun téléchargement.
Une clé configurée ne prouve pas que le compte dispose de crédits.

Pour une clarification, renvoyer exactement la même question avec le
`clarification_token` reçu et `selections: {"reference_de_mention": 123}`.
Les IDs doivent provenir des candidats proposés. Les jetons expirent après 30 minutes
et sont invalidés au redémarrage de l'API. Les questions graphe acceptent `offset` ;
les recommandations sémantiques n'ont pas de pagination exhaustive.

## exécution python locale

Python 3.12 requis. Sous Windows :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev,embeddings]'
.\.venv\Scripts\moviegraphrag.exe profile --output data/reports/profile.json
.\.venv\Scripts\moviegraphrag.exe ingest --output data/reports/manifest.json
.\.venv\Scripts\uvicorn.exe moviegraphrag.api.app:app --host 127.0.0.1 --port 8000
```

Neo4j doit être accessible aux coordonnées de `.env`. La famille Neo4j retenue est
5.26 LTS ; Compose utilise explicitement `5.26.0-community`, à mettre à jour vers un
correctif validé avant tout déploiement exposé. L'application ne fournit pas de
frontière d'autorisation multiutilisateur et ne doit pas être publiée telle quelle.

## comportement et limites

- Graph : tous les candidats sont déterminés avant pagination.
- Hybride : toutes les contraintes sont appliquées avant le scoring cosine exact.
- Vectoriel pur : top approximatif de l'index natif, sans promesse d'exhaustivité.
- Vérification thématique : lots de 20, maximum 100 candidats, puis couverture explicite.
- Un keyword atteste une association annotée ; seul le synopsis peut étayer une centralité.
- Les citations sont vérifiées contre les champs sources ; l'implication sémantique
  et la fidélité de la synthèse nécessitent toujours une évaluation humaine.
- Les homonymes non résolus et les fautes fuzzy demandent une clarification.
- Les traductions de titres absentes, négations, agrégations et similarités à un film
  de référence sont hors MVP ; l'analyseur doit les déclarer non prises en charge.
- Une interprétation LLM valide syntaxiquement peut rester erronée. Voir les tests et l'évaluation.
- Pas de reconstruction destructive automatique. Un autre snapshot demande une base dédiée vide.
- Une ingestion échouée laisse le corpus `loading` ; la relancer avec les mêmes fichiers.
- Le coût reste `null` tant que les tarifs ne sont pas renseignés dans `.env`.

Modèles, hashes, prompts, latence et consommation sont enregistrés dans les traces.
Pour une reproduction stricte, fixer `EMBEDDING_REVISION` à un commit et
`MISTRAL_MODEL` à un identifiant versionné disponible chez le fournisseur. Le modèle
effectivement répondu est consigné ; `latest` n'est pas une garantie de stabilité.

## vérification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\moviegraphrag.exe reference --output data/reports/reference.json
```

Les tests dataset utilisent uniquement les CSV locaux et sont ignorés en leur absence.
Les tests Neo4j sont opt-in : ils réingèrent le même snapshot, sans supprimer de données.
Utiliser une instance dédiée, déjà ingérée :

```powershell
$env:RUN_NEO4J_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/integration -q
```

Les tests HTTP utilisent des doublures déterministes : ils ne prouvent pas la qualité
réelle de Mistral. Une clé valide est nécessaire pour la vérification de bout en bout.

## évaluation

```powershell
python evaluation/build_cases.py
moviegraphrag evaluate --system graph --split dev --output evaluation/reports/graph.json
moviegraphrag evaluate --system vector --split dev --output evaluation/reports/vector.json
moviegraphrag evaluate --system flat --split dev --output evaluation/reports/flat.json
```

Le jeu comprend 80 questions, réparties en quatre catégories. Les références
relationnelles Q1–Q4, Q8–Q10 peuvent être produites indépendamment des requêtes Cypher.
Les annotations thématiques et de centralité restent à relire : les champs vides
ne sont jamais convertis en zéros de performance et aucun gain n'est inventé.

Le runner fournit les métriques de retrieval lorsque les jugements sont renseignés,
les latences et traces. Pour les réponses de type Person, les baselines vectorielles
retournent des documents Movie : la comparaison doit porter sur les preuves retrouvées
ou une réponse évaluée humainement, pas comparer des IDs de personnes à des IDs de films.
Le baseline `flat` découpe les longues listes de crédits pour ne pas les perdre dans
la fenêtre du modèle ; cette différence doit figurer dans le rapport.
Les évaluations de fidélité, plans, linking et génération sont à annoter séparément.

## données et attribution

Source : [TMDB 5000 sur Kaggle](https://www.kaggle.com/datasets/tmdb/tmdb-movie-metadata),
données issues de [TMDB](https://www.themoviedb.org).
Les conditions de redistribution des CSV ne sont pas établies ici : aucun CSV n'est
redistribué. Consulter les conditions applicables avant de publier des données ou
leurs dérivés. [Attribution TMDB](https://developer.themoviedb.org/docs/faq).

« This product uses the TMDB API but is not endorsed or certified by TMDB. »
Cette mention d'attribution ne constitue pas une licence de redistribution.

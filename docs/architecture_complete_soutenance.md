# MovieGraphRAG — architecture complète pour la soutenance

Ce document décrit la version adaptée : **comptages exacts, listes relationnelles et recommandations classées par E5**. Groq analyse les questions et peut rédiger les recommandations ; il ne réexamine plus les candidats pour leur attribuer un jugement thématique.

Lire aussi la [fiche de révision](fiche_revision_soutenance.md) et le [cahier manuel](tests_manuels_rag.md). Les descriptions antérieures avec un plafond de 100 inspections et un Evidence Assessor concernent l'ancienne version.

## 1. Présenter le projet

> MovieGraphRAG permet d'interroger TMDB 5000 en français. Le graphe Neo4j vérifie les relations entre personnes, films et genres, et calcule les comptages. E5 multilingue classe les films par proximité avec les thèmes demandés. Les sources et les chemins de collaboration alimentent un contexte structuré pour une synthèse éventuelle via Groq. La pertinence thématique n'est pas certifiée par le classement.

Les responsabilités du RAG sont :
- Retrieval : trouver les identités, appliquer les contraintes et classer les documents.
- Augmentation : assembler résultats, données sources, chemins et couverture.
- Generation : rendre une réponse déterministe ou une synthèse LLM avec références.

C'est un pipeline contrôlé, pas un agent autonome. Le LLM n'exécute aucun outil librement et ne génère pas de Cypher exécutable. Aucun modèle n'est entraîné sur TMDB.

## 2. Architecture globale

~~~mermaid
flowchart TD
    CSV["CSV films et crédits"] --> ING["Validation, jointure et normalisation"]
    ING --> GRAPH["Nœuds et relations"]
    ING --> DOC["Document par film"]
    DOC --> E5["E5 local sur CPU"]
    GRAPH --> DB[("Neo4j")]
    E5 --> DB
    USER["Navigateur HTML CSS JavaScript"] --> API["FastAPI"]
    API --> ORCH["Pipeline Python contrôlé"]
    ORCH --> GROQ["Groq : analyse et synthèse éventuelle"]
    ORCH --> DB
    ORCH --> E5
    ORCH --> API
    API --> USER
~~~

| Couche | Technologie | Rôle |
| --- | --- | --- |
| Interface | HTML, CSS, JavaScript natif | Questions, comptes, cartes, preuves, clarification et pagination. |
| API | Python 3.12, FastAPI, Pydantic v2 | Contrats HTTP, validation et sérialisation. |
| Application | orchestration.py | Opérations, limites, retrieval, contexte et erreurs. |
| Stockage | Neo4j Community 5.26.0 | Graphe, documents, vecteurs et index. |
| Embeddings | intfloat/multilingual-e5-small | Encodage local, 384 dimensions, fenêtre 512 tokens. |
| LLM | API Groq, modèle configurable | Analyse structurée et rédaction ; aucune appréciation intermédiaire. |
| Exécution | Docker Compose | API et Neo4j persistants ; services de tests optionnels. |

Le frontend est servi par FastAPI. Aucun serveur Node n'est nécessaire à l'application. Les dépendances et poids déjà installés suffisent à cette adaptation.

## 3. Modèle du graphe et données

~~~mermaid
flowchart LR
    P["Person : identité"] -->|ACTED_IN| M["Movie : données et embedding"]
    P -->|DIRECTED| M
    M -->|IN_GENRE| G["Genre"]
    C["Corpus : manifeste technique"]
~~~

Acteur et réalisateur sont des rôles portés par les relations d'une même Person. Une personne à deux rôles n'est pas dupliquée. Corpus est isolé du graphe métier.

| Élément | Volumes du snapshot |
| --- | --- |
| Movie | 4 803 |
| Person acteurs/réalisateurs | 56 603 |
| Genre | 20 |
| Nœuds métier | 61 426, plus un Corpus technique |
| ACTED_IN | 106 084 couples distincts, issus de 106 257 crédits |
| DIRECTED | 5 166 |
| IN_GENRE | 12 160 |
| Relations métier | 123 410 |
| Noms exacts ambigus / noms normalisés ambigus | 405 / 420 |
| Personnes acteurs et réalisateurs | 563 |
| Films à plusieurs réalisateurs | 308 |
| Synopsis : médiane / maximum non vide | 48 / 175 mots |
| Synopsis absents | 4 |
| Documents encodés | 4 801 : deux metadata_only et deux films sans vecteur |

Les chiffres concernent ce corpus statique. L'absence dans TMDB 5000 ne démontre pas l'inexistence d'un film ou d'une collaboration dans le monde.

### Propriétés des nœuds et relations

| Objet | Contenu |
| --- | --- |
| Movie, identité | movie_id, title, original_title, normalized_title, aliases. |
| Movie, sources | overview, release_date, release_year, original_language, vote_average, vote_count, keywords, keyword_ids, unnamed_keyword_ids, genre_names. |
| Movie, traçabilité | snapshot_id, overview_missing, document_text, document_hash, document_version, text_coverage, token_count, keywords_removed. |
| Movie, représentation | embedding, embedding_model, embedding_revision. |
| Person | person_id, name, normalized_name, surname, aliases initialement vides. |
| Genre | genre_id, name, normalized_name. |
| Corpus | name=tmdb, manifest sérialisé en JSON. |
| ACTED_IN | person_id, movie_id, characters, min_order, credits_json, snapshot_id. |
| DIRECTED | person_id, movie_id, credits_json, snapshot_id. |
| IN_GENRE | movie_id, genre_id. |

Les personnages sont sur ACTED_IN car ils dépendent du couple personne–film. Les keywords sont des propriétés ; les genres sont des nœuds car ils participent aux motifs. Le casting et les réalisateurs se retrouvent par les relations, pas par des listes de noms stockées sur Movie.

La collaboration se déduit d'un film commun :
~~~text
acteur ─ACTED_IN→ film ←DIRECTED─ réalisateur
~~~

### Index

Contraintes uniques sur les IDs et Corpus.name ; index de noms/titres normalisés, patronymes, full-text personnes et films. L'index vectoriel de 384 dimensions reste présent mais le parcours actuel utilise un calcul exact des similarités, pas sa recherche approximative.

La liste complète exécutée est SCHEMA dans retrieval/neo4j_store.py. cypher/schema.cypher est une référence partielle.

## 4. Ingestion des CSV

~~~mermaid
flowchart TD
    A["Deux CSV"] --> B["Validation des colonnes, IDs et JSON"]
    B --> C["Jointure movies.id = credits.movie_id"]
    C --> D["Normalisation et agrégation des crédits"]
    D --> E["MERGE des nœuds et relations"]
    D --> F["Construction des documents"]
    F --> G["Contrôle des tokens et encodage E5"]
    E --> N[("Neo4j")]
    G --> N
    N --> H["Contrôle des volumes, index, couverture"]
    H --> I["Manifeste ready"]
~~~

1. Lecture CSV avec validation des colonnes, lignes et IDs uniques positifs.
2. Égalité des ensembles d'IDs ; les titres sont contrôlés mais ne servent pas de clé.
3. Décodage des tableaux JSON cast, crew, genres et keywords.
4. Les personnes du cast créent ACTED_IN ; les membres du crew dont job=Director créent DIRECTED.
5. Agrégation par couple personne–film, conservation des crédits et normalisation Unicode des noms.
6. Hashes SHA-256 des CSV et version de normalisation pour identifier le snapshot.
7. Manifeste loading, contraintes, puis MERGE par lots de 500.
8. Documents et embeddings nouveaux ou réutilisés selon hash, version, modèle, révision et dimension.
9. Contrôle des volumes et de la couverture avant passage à ready.

L'adaptation du retrieval réutilise ce graphe et ces vecteurs. Elle ne nécessite aucune reconstruction. Une ingestion du même snapshot est idempotente ; un autre snapshot exige une base dédiée, sans suppression automatique.

## 5. Chunking et embeddings

### Un film, un document principal

Le texte indexé contient :
~~~text
Title: ...
Overview: ...
Genres: ...
Keywords: ...
~~~

Il n'y a ni découpage glissant systématique ni overlap : les synopsis sont courts. Les acteurs et réalisateurs restent dans le graphe. Un film produit au maximum un embedding principal.

Le tokenizer compte l'entrée complète, préfixe et tokens spéciaux inclus. Si la fenêtre est dépassée, les derniers keywords sont retirés du document et le texte est recompté. Si cela ne suffit pas, une erreur est signalée, sans troncature silencieuse du synopsis. Les keywords sources restent conservés sur Movie. Le manifeste enregistré indiquait zéro document réduit.

- 384 : nombre de composantes du vecteur.
- 512 tokens : fenêtre d'entrée E5, pas 512 mots.
- 500 objets : lot d'écriture, pas taille de chunk.
- 32 documents : lot d'encodage.
- Aucun lot de 20 inspections Groq dans le nouveau parcours.

Les synopsis absents avec keywords donnent metadata_only. Sans synopsis ni keyword exploitable, le film reste interrogeable dans le graphe mais sans embedding thématique.

La baseline flat ajoute les crédits au texte et peut donc créer plusieurs morceaux. Le score du film y est le maximum des scores de ses morceaux ; ce découpage ne décrit pas le corpus principal.

### Recherche multilingue

E5 encode les documents avec passage: et la requête thématique avec query:. Les vecteurs sont normalisés. La requête doit utiliser le modèle et la révision du snapshot.

Le modèle rapproche des textes de différentes langues ; cela permet la comparaison d'une requête française avec un synopsis anglais. Voir la [fiche E5](https://huggingface.co/intfloat/multilingual-e5-small).

La similarité cosine compare les directions des vecteurs :
~~~text
cos(q,d) = (q · d) / (norme(q) × norme(d))
~~~
Un score est un signal de classement, pas une probabilité de pertinence ou une preuve de vérité. E5 n'établit pas les collaborations.

## 6. Analyse, opérations et quantité demandée

QuestionDraft contient :
~~~text
supported, unsupported_reason
operation : list | count | recommend
mentions, variables, edges, target, filters
theme_request, distinct_movies
requested_limit : entier explicite ou null
~~~

Le type retourné vient du label de la variable cible : Movie ou Person. Le LLM n'émet ni IDs canoniques ni Cypher libre. Pydantic impose les types, directions, références, motifs connectés acycliques et quatre relations maximum. Une correction d'analyse est autorisée.
Après un refus JSON du fournisseur, cette correction peut utiliser le mode JSON objet, toujours validé par Pydantic. Voir les [formats Groq](https://console.groq.com/docs/structured-outputs). Le nombre d'essais d'analyse reste borné à deux ; une erreur de fournisseur persistante est signalée.

| Opération | Fonction |
| --- | --- |
| list | Liste factuelle de Movie ou Person, sans thème. |
| count | Nombre exact de cibles distinctes selon les relations et filtres. |
| recommend | Classement thématique de Movie, avec ou sans contraintes structurelles. |

Les comptages globaux simples couvrent films, personnes, acteurs et réalisateurs. Un acteur ou réalisateur est compté comme Person avec au moins une relation du rôle demandé. Les autres motifs gardent leurs ancres/filtres.

La quantité demandée n'est jamais la cardinalité du dataset :
- « recommande trois films » : jusqu'à trois résultats ;
- « recommande un film » : au maximum un ;
- sans quantité : limite de l'interface, défaut cinq ;
- demande supérieure à vingt : plafond vingt avec avertissement ;
- « combien de films ? » : aucun top-k ; Neo4j calcule le compte.

Les quantités sont extraites par l'analyseur et contrôlées contre le texte dans quantities.py. Les formes usuelles numériques, françaises et anglaises sont reconnues. Les années ne doivent pas être transformées en limites. La pagination utilise effective_limit et non une ancienne valeur de l'interface.

## 7. Entity Linking

Normalisation de casse/accents/espaces ; recherche exacte et alias, patronyme d'un seul mot, puis full-text fuzzy. Les genres utilisent une table de correspondance français–anglais. Les titres traduits absents ne sont pas inventés.

Contexte explicite : rôle, année et film d'identification. Les films d'identification sont résolus avant les personnes. Une correspondance fuzzy reste à confirmer ; plus de dix candidats exige une précision.

Tom Holland correspond à deux IDs : 1136406 et 64796. Un contexte comme le réalisateur de Fright Night peut les départager. La popularité n'est pas une preuve d'identité.

Une identité exacte unique sans film d'identification n'est pas éliminée simplement parce qu'elle n'a aucun crédit pour la relation demandée. Ainsi, une personne connue sans DIRECTED peut donner un compte zéro. Le contexte d'identification explicitement fourni reste vérifié.

Les tokens de clarification HMAC-SHA256 conservent le plan et les choix autorisés/confirmés. Ils sont liés à la question exacte, expirent après trente minutes et sont invalidés par un redémarrage. Ils sont signés, pas chiffrés. Les choix sont revalidés côté serveur.

## 8. Retrieval et comptages

### Comptage exact

Python compile le motif avec ses valeurs paramétrées. graph_count() projette COUNT(DISTINCT cible), sans LIMIT ni clé de regroupement. Avec des identités résolues et zéro correspondance, l'agrégat retourne zéro.

La réponse contient count et une preuve aggregate avec valeur, type de cible, bindings, motif, snapshot et hashes des CSV. results est vide ; coverage.returned ne représente pas le compte. Aucun appel de génération, embedding ou reranking n'est nécessaire.

### Liste relationnelle

Tous les IDs admissibles sont dédupliqués et triés par ID. Une page est affichée, avec un témoin relationnel par résultat. Le total et la page sont distincts : afficher vingt des 509 acteurs de Nolan ne signifie pas qu'il n'en existe que vingt dans le corpus.
La réponse liste contient un plan signé dans clarification_token. Les pages suivantes le réutilisent et revalident les identités sans nouvelle analyse Groq, ce qui stabilise le motif entre pages.

### Recommandations E5

- VECTOR : calcul exact sur tous les films encodés du corpus, soit 4 801 pour ce snapshot.
- HYBRID_GRAPH_FIRST : toutes les contraintes d'abord, puis calcul exact sur tous les candidats encodés admissibles.
- Tri score décroissant, puis ID croissant.
- Vérification de l'intégrité et de la disponibilité des sources pour les résultats retenus.
- Jusqu'à k résultats ; aucun ajout artificiel si moins de candidats existent.

Il n'y a plus de plafond de cent candidats ni d'appel assess. ranking_coverage indique la disponibilité des embeddings pour le classement, pas une vérification des thèmes. Une liste top-k n'est pas une liste exhaustive de films thématiquement pertinents.

La fonction exacte est [documentée par Neo4j](https://neo4j.com/docs/cypher-manual/5/functions/vector/). Le calcul peut être complet sans que la pertinence sémantique soit parfaite.

## 9. Preuves, augmentation et génération

| Preuve | Contenu |
| --- | --- |
| relation | Nœuds et relations d'un motif témoin valide. |
| document | Titre, synopsis source, keywords avec IDs, genres, couverture, hash/version et snapshot. |
| aggregate | COUNT DISTINCT, valeur, cible, motif, paramètres et provenance. |

document_evidence() vérifie identité, snapshot, modèle/révision, hash du document et cohérence des annotations. Cela ne certifie pas que le document répond au thème. Aucun jugement automatique central/peripheral n'est produit.

Le contexte JSON assemble question, langue, demande thématique, résultats, preuves référencées et couverture. Budget : 24 000 caractères sérialisés. Aucun vecteur n'est transmis au générateur. Les ensembles résultat + preuves ne sont pas coupés au milieu.

COUNT et LIST sont rendus de façon déterministe. RECOMMEND peut recevoir une synthèse Groq française si elle est activée et que le contexte n'a pas été limité. Le modèle retourne des affirmations avec evidence_ids. Les références sont validées ; leur implication sémantique exige une évaluation humaine.

Si la synthèse échoue, cite une preuve inconnue ou si le contexte est limité, le rendu déterministe reste disponible. Les résultats structurés conservent toutes leurs sources.

Les mots « centralité certifiée », les comptages thématiques et les demandes thématiques exhaustives exigent une reformulation. Un keyword indique une annotation ; il ne suffit pas à prouver la centralité.

## 10. Parcours d'une question

~~~mermaid
flowchart TD
    Q["Question"] --> A["Analyse Groq ou plan signé"]
    A --> V["Validation et linking"]
    V --> E{"Identités résolues ?"}
    E -- non --> C["Clarification / entité absente"]
    E -- oui --> R{"Opération"}
    R -- count --> N["COUNT DISTINCT et provenance"]
    R -- list --> G["Relations puis pagination"]
    R -- recommend --> H["Corpus ou candidats du graphe"]
    H --> S["Cosine exact de tous les candidats encodés"]
    S --> K["Top-k et intégrité documentaire"]
    K --> CT["Contexte et chemins témoins"]
    CT --> L["Synthèse Groq ou rendu déterministe"]
    N --> O["Réponse structurée"]
    G --> O
    L --> O
~~~

### Q8 : Nolan, DiCaprio et les rêves

Analyse de deux personnes, deux relations et un thème ; résolution des identités ; un seul candidat structurel : Inception 27205. Son embedding est scoré, son document est fourni avec les relations, puis une synthèse peut être produite.

Un candidat scoré ne signifie pas un score égal à 1. Le synopsis parle du subconscient et le keyword dream existe, mais le système n'émet plus un verdict thématique certifié. Il ne complète pas artificiellement la liste jusqu'à cinq films.

### Q9 et Q10 : plusieurs sauts

~~~text
DiCaprio ─ACTED_IN→ film_témoin ←DIRECTED─ réalisateur
réalisateur ─DIRECTED→ film_résultat ─IN_GENRE→ Science Fiction
~~~

Le thème porte sur film_résultat ; DiCaprio n'a pas besoin d'y jouer. Spielberg / Catch Me If You Can / A.I. Artificial Intelligence constitue un exemple de motif.

Q9 possède 23 candidats structurels ; Q10 en possède 254. Tous les candidats encodés sont scorés. Cela ne signifie pas qu'ils satisfont tous le thème.

Pour Q4, les films témoins de deux collaborations peuvent différer : Nolan a dirigé DiCaprio et Murphy dans Inception ; Boyle les a dirigés dans The Beach et 28 Days Later.

## 11. Algorithmes réellement utilisés

Le retrieval graphe utilise des motifs Cypher paramétrés. Le moteur choisit son plan ; le projet n'impose pas un BFS ou DFS du voisinage métier. Il n'utilise ni PageRank, ni Jaccard, ni Leiden/Louvain, ni plus court chemin.

Un petit DFS dans la validation vérifie la connexion des variables du plan. Ce n'est pas un parcours du corpus pour répondre. La centralité de degré n'est pas un jugement de centralité thématique.

L'approche est proche de Text2Cypher, avec une étape déclarative contrôlée entre le LLM et le Cypher. L'hybride est Graph First, pas vecteur puis expansion arbitraire. Aucun résumé de communauté Microsoft GraphRAG n'est calculé.

## 12. API, couverture et déploiement

Endpoints inchangés : GET /, /static, /health, /ready, /entities/search, /movies/{id}, /docs ; POST /questions.

AnswerResponse expose :
~~~text
status, operation, strategy, count
resolved_entities, results, answer, evidence
thematic_verification, coverage, warnings, trace_id, trace
~~~

- count : entier pour COUNT, sinon null.
- thematic_verification : not_performed pour les recommandations ; not_applicable sinon.
- coverage : total_candidates, scored, sources_checked, returned, without_embedding, ranking_coverage, approximate, offset, has_more, effective_limit.
- ranking_coverage : complete/partial selon les embeddings disponibles ; aucun examen thématique n'est sous-entendu.
- approximate : false dans les parcours actuels.
- trace : snapshot/hashes, plan, bindings, motif, limites, sélection, scoring_ms, contexte limité, références du contexte, generation_mode, latence et appels/tokens/modèles par étape.

scoring_ms mesure l'appel de scoring et la récupération de ses résultats, pas la totalité des coûts E5 et Groq. Les valeurs mesurées dépendent de la machine et de l'état des services.

Les statuts restent ok, needs_clarification, entity_not_found, no_match_in_dataset, insufficient_evidence, unsupported et dependency_error. HTTP 200 n'est pas une garantie de statut métier ok.

Le frontend présente un nombre et sa provenance pour COUNT, des pages pour LIST et des cartes sourcées pour RECOMMEND. La question, les choix et les pages restent en mémoire. Modifier la question ou la limite les réinitialise.

Deux services persistants : API et Neo4j, avec restart unless-stopped. Le volume Neo4j contient graphe et vecteurs ; data/ et models/ sont sur la machine. Ports locaux 8000, 7474, 7687. La clé Groq reste côté serveur.

L'image saved-20261108 est un état historique antérieur à cette adaptation. Le code courant est monté depuis src/. Les volumes, CSV, cache et .env restent distincts d'une image.

~~~powershell
docker compose up -d --no-build --pull never
docker compose restart api
~~~

/ready vérifie le corpus, les index, la configuration de clé et le modèle chargé ; il ne vérifie pas le quota Groq.

## 13. Chaque fichier : rôle et place dans le système

Les chemins ci-dessous sont relatifs à la racine. Les fichiers suivis du projet sont couverts ; les fichiers locaux ignorés sont distingués ensuite.

### 13.1 Racine et configuration

| Fichier | À quoi il sert |
| --- | --- |
| `README.md` | Point d’entrée : démarrage, interface, configuration, API, tests et dépannage. |
| `pyproject.toml` | Dépendances Python, métadonnées du package, commande CLI, ressources web embarquées, configuration pytest/Ruff. |
| `Dockerfile` | Image Python de l’API ; installe dépendances CPU et lance Uvicorn. Écrit les versions installées dans `/app/requirements.lock`. |
| `compose.yaml` | Services, réseau, ports locaux, variables, volumes, dépendances et redémarrage automatique. |
| `Dockerfile.browser-tests` | Image optionnelle Ubuntu/Node/Playwright pour les tests navigateur ; son installation a été interrompue. |
| `.env.example` | Noms des paramètres et valeurs d’exemple, sans vraie clé Groq. |
| `.gitignore` | Exclut secrets, CSV, caches, modèles, rapports locaux et environnements temporaires de Git. |
| `.dockerignore` | Évite d’envoyer secrets, gros datasets, modèles et fichiers temporaires au contexte de build. |
| `.gitattributes` | Uniformise les fins de ligne, principalement LF. |

### 13.2 Entrées et orchestration Python

| Fichier | Responsabilité et fonctions importantes |
| --- | --- |
| `src/moviegraphrag/__init__.py` | Décrit le package et fixe `__version__ = "0.1.0"`. |
| `src/moviegraphrag/config.py` | `Settings` : lit l’environnement, valide budgets et tarifs facultatifs ; protège les secrets avec `SecretStr`. |
| `src/moviegraphrag/cli.py` | `main()` : commandes `profile`, `ingest`, `reference`, `warmup`, `versions`, `evaluate`. |
| `src/moviegraphrag/orchestration.py` | `MovieGraphRAG.ask()` et `_ask()` : pilote de bout en bout, erreurs, trace, choix du retrieval, preuves et réponse. |
| `src/moviegraphrag/quantities.py` | Extraction et validation des quantités explicites, détection des comptages et demandes de centralité. |
| `src/moviegraphrag/embeddings.py` | `LocalEmbedder` : chargement E5, verrou, préfixes, normalisation, métadonnées et encodage CPU. |
| `src/moviegraphrag/examples.py` | Questions Q1–Q10 et plans de référence ; sert aux tests et aux exemples du prompt d’analyse. Ce n’est pas un système de réponses prédéfinies à l’exécution. |
| `src/moviegraphrag/evaluation.py` | Références indépendantes depuis les CSV, validation des cas, conservation des annotations, métriques et exécution des trois systèmes comparés. |

### 13.3 Ingestion

| Fichier | Responsabilité |
| --- | --- |
| `src/moviegraphrag/ingestion/dataset.py` | Lit les CSV, contrôle les IDs/JSON, normalise, joint, agrège les crédits et produit `Dataset`, rapport et manifeste. |
| `src/moviegraphrag/ingestion/documents.py` | `Document` et `build_document()` : format du texte, couverture, tokenisation, retrait éventuel de keywords, hash et version. |
| `src/moviegraphrag/ingestion/loader.py` | `ingest()` : contraintes, MERGE par lots, réutilisation des embeddings, écritures et contrôle final du snapshot. `chunks()` organise des lots techniques. |

### 13.4 Retrieval et identification

| Fichier | Responsabilité |
| --- | --- |
| `src/moviegraphrag/retrieval/compiler.py` | `route()`, `CompiledQuery`, `compile_graph()` : stratégie déterministe et compilation paramétrée du plan. |
| `src/moviegraphrag/retrieval/neo4j_store.py` | `SCHEMA`, `Neo4jStore` : driver, transactions, index, manifeste, recherche d’entités, contexte, candidats, témoins et scores. |
| `src/moviegraphrag/linking/linker.py` | `EntityLinker.link()` : combine candidats et contexte explicite ; décide resolved, ambiguous ou not_found. Contient les alias de genres. |
| `src/moviegraphrag/linking/clarifications.py` | `Clarifications.issue()/verify()` : tokens signés, expiration, question exacte, choix autorisés et choix confirmés. |
| `cypher/schema.cypher` | Extrait de schéma pour lecture et référence ; la liste exécutable complète est dans `neo4j_store.py`. |
| `cypher/examples.cypher` | Exemples pédagogiques de motif acteur/réalisateur et de scoring exact sur candidats. Non lu automatiquement par le pipeline. |

### 13.5 LLM, preuves et contrats

| Fichier | Responsabilité |
| --- | --- |
| `src/moviegraphrag/llm/prompts.py` | Prompts versionnés `ANALYZER`, `GENERATOR`, règles de sources et d’intention. |
| `src/moviegraphrag/llm/groq.py` | `GroqLLM`, `strict_schema()`, `Usage`, reprise réseau : appels JSON structurés, adaptation du schéma, correction d’analyse, génération et comptage des appels/tokens par étape. |
| `src/moviegraphrag/evidence/validation.py` | `document_evidence()` : identité, snapshot, modèle, hash et sources des documents ; aucun jugement thématique. |
| `src/moviegraphrag/evidence/context.py` | `build_context()` et `validate_generated()` : budget de contexte et validité des références finales. |
| `src/moviegraphrag/schemas/models.py` | Tous les modèles Pydantic de requête, plan, linking, preuves, jugements, couverture et réponse ; invariants de graphe. |

### 13.6 API et frontend

| Fichier | Responsabilité |
| --- | --- |
| `src/moviegraphrag/api/app.py` | `create_app()`, cycle de vie, ressources partagées, chargement E5 en arrière-plan, routes HTTP et fichiers statiques. |
| `src/moviegraphrag/web/index.html` | Structure accessible de la page : formulaire, exemples, clarification, résultats, preuves et navigation. |
| `src/moviegraphrag/web/styles.css` | Thème sombre, mise en page responsive, états de focus et adaptation mobile. |
| `src/moviegraphrag/web/app.js` | État en mémoire, fetch, disponibilité, soumission, rendu sûr, citations, choix d’entités, fiches et cache des pages. |
| `src/moviegraphrag/web/icon.svg` | Icône vectorielle de l’application. |

Dans `app.js`, les fonctions à repérer sont `search()`, `showClarification()`,
`renderResults()`, `renderAnswer()`, `evidenceNode()`, `movieDetails()`,
`checkReadiness()`, `resetSearch()` et `changePage()`.
Les valeurs sources sont rendues avec `textContent` et des nœuds DOM ; une chaîne
provenant des données n’est pas exécutée comme du HTML.

### 13.7 Évaluation et tests

| Fichier | Responsabilité |
| --- | --- |
| `evaluation/build_cases.py` | Génère les 80 cas, attribue familles/splits et références disponibles, préserve les annotations humaines. |
| `evaluation/cases.jsonl` | Un objet JSON par question : ID, texte, catégorie, split, type, statut de jugement et pertinence. |
| `evaluation/reports/.gitkeep` | Conserve le dossier Git ; les futurs rapports locaux sont ignorés. |
| `tests/conftest.py` | Interdit pytest hors Docker et fournit les dépendances réelles : CSV, Neo4j, E5, HTTP et clé Groq requise. |
| `tests/real/test_graph.py` | Volumes, références CSV, candidats Cypher, homonymes, vecteurs natifs, scoring hybride et réingestion idempotente. |
| `tests/real/test_linking.py` | Films ambigus, contexte explicite, personne dépendante et patronymes trop larges. |
| `tests/real/test_clarification_replay.py` | Clarifications successives, conservation/revalidation des choix, signature, portée, expiration et types des résultats. |
| `tests/real/test_validation.py` | Plans invalides, demandes thématiques strictes refusées, intégrité documentaire et références finales. |
| `tests/real/test_http.py` | Endpoints réels et parcours Groq graphe/hybride, citations et entité absente. |
| `tests/real/test_groq_generation.py` | Génération réelle Groq avec un document d’Inception récupéré depuis Neo4j. |
| `tests/real/test_counts.py` | Comptages exacts confrontés aux CSV, comptes globaux, zéro et identités ambiguës. |
| `tests/real/test_semantic_ranking.py` | Classement exact des 4 801 vecteurs, 254 candidats hybrides, limites et contexte borné. |
| `tests/real/test_evaluation.py` | Références CSV, métadonnées des cas, préservation des annotations, ensemble complet et pagination HTTP. |
| `tests/browser/package.json` | Dépendance Playwright du conteneur de test ; ce n’est pas un besoin du frontend de production. |
| `tests/browser/playwright.config.js` | Garde Docker, navigateur headless, un worker, zéro retry, délais et rapports. |
| `tests/browser/journeys.spec.js` | Cinq parcours réels définis : recherche/clavier, hybride mobile, clarification/absence, pagination, puis comptage et quantité explicite. Non exécutés, car l’image navigateur n’est pas installée. |

### 13.8 Documentation et dossiers de données

| Fichier ou dossier | Responsabilité |
| --- | --- |
| `docs/architecture.md` | Résumé technique et premiers diagrammes. |
| `docs/tests_manuels_rag.md` | Cahier de 29 cas avec questions, attentes et méthode d’évaluation. |
| `docs/resultats_tests_manuels.csv` | Tableau de verdicts à remplir ; initialement NON_EXECUTE. |
| `docs/architecture_complete_soutenance.md` | Présent dossier : explication détaillée de l’implémentation. |
| `docs/fiche_revision_soutenance.md` | Synthèse à relire avant l’oral. |
| `data/raw/.gitkeep` | Conserve le dossier vide dans Git, sans distribuer les CSV. |
| `data/reports/.gitkeep` | Conserve le dossier des rapports, sans versionner leurs sorties locales. |

Les fichiers suivants marquent l’organisation en sous-packages Python. Ils
contiennent surtout une description, sans orchestration métier :

- `src/moviegraphrag/api/__init__.py` ;
- `src/moviegraphrag/ingestion/__init__.py` ;
- `src/moviegraphrag/linking/__init__.py` ;
- `src/moviegraphrag/retrieval/__init__.py` ;
- `src/moviegraphrag/evidence/__init__.py` ;
- `src/moviegraphrag/llm/__init__.py` ;
- `src/moviegraphrag/schemas/__init__.py`.

### 13.9 Fichiers locaux ignorés, mais utiles à expliquer

| Élément local | Rôle |
| --- | --- |
| `.env` | Configuration effective et secrets ; ne pas montrer la clé pendant la démonstration. |
| `data/raw/tmdb_5000_movies.csv` | Métadonnées et synopsis des films. |
| `data/raw/tmdb_5000_credits.csv` | Casting et équipe, dont les réalisateurs. |
| `data/reports/profile.json` | Statistiques et hashes des données. |
| `data/reports/manifest.json` | Rapport conservé d’une ingestion ; le manifeste utilisé en service est aussi stocké dans Corpus. |
| `data/reports/reference.json` | Ensembles d’IDs de référence pour les questions de contrôle. |
| `data/reports/*.xml` | Rapports historiques de tests, à distinguer d’une évaluation de pertinence. |
| `models/` | Cache des poids/tokenizers Hugging Face, monté dans les conteneurs. |
| `evaluation/reports/` | Sorties futures des comparaisons de systèmes. |
| `.runtime/` | Artefacts locaux de travail et de tests éventuels ; pas un service de production. |
| `.venv/` | Ancien environnement local éventuel, ignoré ; le fonctionnement et les tests actuels utilisent Docker. |


## 14. Vérification et évaluation

Les tests applicatifs s'exécutent uniquement dans Docker avec Neo4j, E5 et Groq réels selon le marqueur. Aucune doublure de service ni réponse interceptée n'est utilisée. Un plan signé explicite peut servir d'entrée de test sans remplacer une réponse LLM.

Les références relationnelles et les comptages sont confrontés aux CSV indépendamment des requêtes Cypher. Tests principaux : quatre films Nolan–Murphy, 509 acteurs de Nolan, zéro pour une personne connue sans réalisation, comptes globaux, homonymes, limites explicites, scoring de 4 801 documents et 254 candidats, provenance et rendu de secours.

Rapports de cette adaptation dans `data/reports/` :

| Rapport | Résultat et portée |
| --- | --- |
| `architecture-counts-tests.xml` | 18 réussites : comptages, linking et clarifications. |
| `architecture-ranking-tests.xml` | 47 réussites sans appels LLM : graphe réel, E5, classement et validations. Inclut une réingestion du même snapshot réutilisant les vecteurs. |
| `architecture-final-tests.xml` | Campagne combinée avant les dernières corrections : 62 réussites et 2 échecs, sur une analyse hybride et un compte global. La réingestion était exclue de cette campagne. |
| `architecture-provider-recheck.xml` | Les 8 scénarios fonctionnels repris réussissent : hybride, deux comptes globaux, article du film témoin et quatre quantités. Un neuvième test bas niveau échoue : il attendait à tort que le mode JSON garantisse aussi le schéma. |
| `architecture-validation-recheck.xml` | Après correction du contrat de ce test : 12 réussites, dont la génération Groq réelle, le contrôle du mode JSON, les quantités et le refus des comptes par groupe. |
| `architecture-http-smoke.json` | Corpus prêt, volumes conservés, page, ressources, fiche Inception et Swagger accessibles avec HTTP 200. |

Les premières campagnes Groq et leurs reprises intermédiaires restent conservées : elles montrent des erreurs de sortie structurée et des limites de quota rencontrées pendant l’intégration. Les deux échecs fonctionnels de la campagne combinée ont été repris avec succès après correction ; une campagne complète unique n’a pas été relancée ensuite. Le mode JSON peut fournir un document syntaxiquement valide mais incorrect pour le schéma : Pydantic doit alors le rejeter. Ce test bas niveau vérifie ce contrat ; les tests fonctionnels continuent d’exiger la bonne réponse.

Ruff a également validé les sources, tests et scripts d’évaluation dans Docker. Les anciens `docker-tests.xml` et `frontend-*.xml` concernent des versions précédentes. Ne pas additionner les campagnes comme une seule exécution ni les présenter comme une mesure de pertinence thématique.

`architecture-scoring.json` conserve une observation : **4 801 vecteurs scorés en 398,52 ms** pour le calcul Neo4j, le tri et la récupération des lignes. Cette mesure unique exclut l’encodage E5 de la question et les appels Groq ; elle n’établit ni p50/p95 ni une garantie de latence.

Les parcours Playwright restent optionnels et non exécutés : leur installation a été interrompue. Le cahier manuel et sa grille doivent être remplis lors d'essais réels.

L'évaluation conserve 80 cas, leurs familles et splits : 20 graphe, 20 vectoriels, 20 hybrides, 20 robustesse. Les demandes strictes de centralité sont maintenant attendues comme unsupported. Les annotations humaines ne sont pas écrasées. Le champ historique `answer_type` de ces cas décrit le type des identifiants de référence ; le contrat d’exécution `QuestionDraft` utilise désormais `operation` et le label de sa variable cible.

Trois systèmes : graph désigne le pipeline complet ; vector la baseline sur documents ; flat la baseline avec crédits et morceaux supplémentaires. Les baselines ne génèrent pas de réponse. Les latences n'ont donc pas toutes la même portée.

Mesures : exactitude des comptes/ensembles, precision@k et nDCG@k sur jugements humains, fidélité des citations, respect des contraintes, latence, coût et abstention. Le rappel sémantique exige une référence suffisamment complète. Un P@5 de 0,8 pour les quatre films exacts de Q1 ne signifie pas que la liste contient une erreur.

## 15. Décisions, limites et V2

| Décision | Justification |
| --- | --- |
| Neo4j seul | Identités communes pour relations, documents et vecteurs. |
| Mapping des CSV | Relations déjà explicites et vérifiables. |
| Un document par film | Synopsis courts, unité cohérente. |
| Scoring exact du corpus | Supprime le plafond intermédiaire et l'approximation ANN pour ce petit corpus ; latence à mesurer. |
| Graph First | Toutes les contraintes obligatoires précèdent la sélection sémantique. |
| Aucune appréciation Groq intermédiaire | Réduit les décisions LLM et les appels ; ne prétend plus certifier les thèmes. |
| Comptage et listes déterministes | Le nombre provient de la base, pas d'une estimation LLM. |
| Reranker en V2 | Exiger un gain mesuré face à E5 seul avant de l'ajouter. |

Limites : corpus statique ; alias incomplets ; quatre relations maximum ; pas de négation ni d'agrégats autres que les comptes prévus ; pas de comptage thématique, centralité certifiée, communauté, agent autonome, GNN ou fine-tuning.

Un classement exact n'est pas une garantie de pertinence. Aucun seuil universel de similarité n'est considéré comme preuve. Les préférences ALL/ANY sont conservées dans la requête thématique sans certifier leur satisfaction.

La synthèse peut encore interpréter incorrectement une source. La validation des références ne démontre pas l'implication de toutes les phrases. L'analyse de question peut aussi rester incorrecte malgré un schéma valide.

V2 : benchmark humain, reranker multilingue comparé à E5, budgets de reranking calibrés, nouveaux motifs et alias, puis ingestion incrémentale si nécessaire. Aucun reranker ne remplacera les contraintes du graphe ni un comptage exact.

## 16. Préparer la soutenance

1. Présenter les deux représentations : relations issues des CSV et vecteurs de documents.
2. Montrer un nœud Person partagé entre rôles, Movie et Genre.
3. Expliquer un document par film et la différence 512 tokens / 384 dimensions.
4. Montrer les trois opérations : liste, compte et recommandation.
5. Demander les quatre films Nolan–Murphy, puis combien d'acteurs Nolan a dirigés : 509 dans ce corpus.
6. Montrer Q8, ses sources et le fait qu'un seul film ne doit pas être complété artificiellement.
7. Montrer une quantité explicite : recommande trois films, même si l'interface indique dix.
8. Reconnaître la limite thématique et montrer la demande de reformulation pour une centralité certifiée.

> Le graphe établit les contraintes et les comptes. E5 classe les documents. Le contexte conserve les sources et chemins, et le LLM rédige sans remplacer les calculs de la base.

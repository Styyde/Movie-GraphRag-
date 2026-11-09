# MovieGraphRAG — cahier de tests manuels

Ce guide permet de tester **la recherche, l’augmentation, la génération et les
traversées du graphe** sur l’application existante. Il ne nécessite aucune installation.
Les services restent dans Docker ; tu peux utiliser ton navigateur pour les examiner.

**Ce document décrit des tests à effectuer : il ne constitue pas un rapport de réussite.**
La [grille de résultats](resultats_tests_manuels.csv) commence avec tous les cas en
`NON_EXECUTE`. Aucun scénario de ce guide n’a été lancé lors de sa rédaction.

Accès direct : [première session](#4-première-session-courte),
[retrieval](#5-tests-de-retrieval), [augmentation](#6-tests-daugmentation-et-de-preuves),
[génération](#7-tests-de-génération-et-de-réponse-finale),
[graphe](#8-tests-du-raisonnement-sur-le-graphe), [notation](#9-noter-les-résultats-sans-inventer-de-métriques).

## 1. Ce qu’on teste dans un RAG

Une réponse agréable à lire ne suffit pas à prouver qu’un RAG fonctionne.
Il faut localiser l’origine d’une erreur.

| Partie | Question à se poser | Exemple d’échec |
| --- | --- | --- |
| Analyse et identification | La question et les personnes ont-elles été correctement comprises ? | Confondre les deux Tom Holland. |
| Retrieval — recherche | Les bons films ou les bonnes personnes ont-ils été retrouvés ? | Oublier un des quatre films de Nolan avec Murphy. |
| Augmentation — préparation du contexte | Les informations utiles, leurs sources et leurs relations sont-elles conservées pour répondre ? | Perdre le film qui prouve une collaboration. |
| Génération | Les affirmations de la réponse sont-elles soutenues par ces informations ? | Inventer un acteur ou présenter un keyword comme thème central. |
| Raisonnement du graphe | Les chemins satisfont-ils toutes les contraintes de la question ? | Exiger DiCaprio dans le film recommandé alors qu’il est demandé dans un film témoin. |

Un processus d’évaluation RAG consiste à préparer des questions de référence,
exécuter le système et noter séparément recherche et réponse. Cette organisation
est aussi celle du [guide d’évaluation RAG de LangSmith](https://docs.langchain.com/langsmith/evaluate-rag-tutorial).
Ici, nous appliquons cette méthode manuellement, sans ajouter de framework.

Le « raisonnement » évalué ici correspond à des **relations et chemins vérifiables**.
Il n’est pas nécessaire de demander au LLM de raconter sa pensée interne.

## 2. Préparer une session

1. Ouvrir l’interface : <http://localhost:8000>.
2. Attendre **Prêt à explorer**.
3. Ouvrir également <http://localhost:8000/docs> pour examiner les réponses JSON.
4. Dupliquer ou remplir la grille CSV, avec le séparateur **point-virgule**.
5. Pour chaque réponse, conserver le `trace_id`, les IDs retournés et les preuves utilisées.

Si les services sont arrêtés, cette commande les relance avec les images locales,
sans reconstruction ni téléchargement :

```powershell
docker compose up -d --no-build --pull never
```

### Examiner le JSON

Dans Swagger, ouvrir **POST /questions**, cliquer sur **Try it out**, saisir par exemple :

```json
{
  "question": "Quels films de Nolan avec DiCaprio parlent des rêves ?",
  "language": "fr",
  "limit": 5,
  "offset": 0
}
```

Cliquer sur **Execute**, puis consulter **Response body**. Il s’agit d’un véritable
appel à l’application Docker et à Groq, pas d’une réponse de démonstration.
L’interface et Swagger effectuent deux appels distincts si tu y soumets deux fois
la question. Réutilise une même réponse pour examiner plusieurs critères.

| Champ | Utilité pour le test |
| --- | --- |
| `status` | Succès, clarification, absence, preuves insuffisantes ou erreur. Un HTTP 200 n’implique pas `status=ok`. |
| `strategy` | `GRAPH`, `VECTOR` ou `HYBRID_GRAPH_FIRST`. |
| `trace.plan` | Entités, liens, filtres et thèmes compris par l’analyseur. Comparer avec **la question originale**. |
| `resolved_entities` | IDs réellement sélectionnés et éventuels homonymes. |
| `results` | Résultats affichés ; les IDs Movie et Person appartiennent à des types différents. |
| `results[].evidence_ids` | Références des preuves qui justifient ce résultat. |
| `evidence` | Relations, extraits du synopsis ou keywords cités. |
| `coverage` | Nombre de candidats, de scores, de vérifications thématiques et de résultats affichés. |
| `warnings` | Couverture partielle ou recours à un rendu de secours. |
| `trace.snapshot_id`, `trace.source_hashes` | Version du corpus utilisée. |
| `trace.latency_ms`, `trace.llm_usage` | Temps de traitement et consommation observée. |

Dans l’interface, ouvrir **Pourquoi ce résultat ?** et **Détails du film**.
Les numéros de citation affichés comme `[1]` sont des repères d’interface : ils ne
sont pas les IDs TMDB. Les identifiants de preuves figurent dans le JSON.

### Limites à connaître avant de noter

- Les réponses `GRAPH` sont des listes rendues de façon déterministe. Groq analyse
  la question, mais ne rédige pas leur réponse finale : noter la génération LLM `N/A`.
- Pour `VECTOR` et l’hybride, la synthèse LLM peut être désactivée ou remplacée par
  un rendu déterministe. Le nombre d’appels Groq ne suffit pas à prouver qu’une
  synthèse a eu lieu : l’analyse et l’évaluation thématique utilisent aussi Groq.
- L’API ne publie ni le contexte exact du générateur, ni les candidats rejetés,
  ni un champ explicite `generation_mode`. Les preuves visibles permettent un
  **audit partiel de l’augmentation**, pas une mesure complète du prompt envoyé.
- Les résultats sémantiques combinent embeddings et vérification LLM. Leur seule
  lecture ne permet pas d’isoler la qualité du modèle d’embeddings.
- Un HTTP 429 ou `dependency_error` se note `ERREUR_DEPENDANCE`, avec sa trace.
  Ce n’est pas la preuve que le film est absent ni que la recherche est mauvaise.

## 3. Questions de référence à copier

Les identifiants Q1–Q10 correspondent aux questions déjà présentes dans le projet.
Les valeurs exactes viennent des références calculées depuis les CSV, sous le snapshot :

```text
951f77da9b7278fdb95700d6a489a7b4ca40fc6b8e6f806fd14136965de40965
```

Si ton `trace.snapshot_id` diffère, ces références doivent être revérifiées.
La vérité de référence reste celle des CSV du projet, pas toute la filmographie actuelle.

| Référence | Question | Stratégie et attente |
| --- | --- | --- |
| Q1 | Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ? | `GRAPH` ; exactement 4 films. |
| Q2 | Quels réalisateurs ont travaillé avec Leonardo DiCaprio ? | `GRAPH` ; exactement 17 personnes. |
| Q3 | Quels acteurs ont joué dans des films réalisés par Christopher Nolan ? | `GRAPH` ; exactement 509 personnes avant pagination. |
| Q4 | Quels réalisateurs ont travaillé avec Leonardo DiCaprio et Cillian Murphy ? | `GRAPH` ; Christopher Nolan et Danny Boyle. |
| Q5 | Trouve des films sur l’exploration spatiale, la survie et les relations familiales. | `VECTOR` ; trois thèmes obligatoires, opérateur `ALL`. |
| Q6 | Quels films explorent les conséquences de l’intelligence artificielle sur l’humanité ? | `VECTOR` ; pertinence à juger sur les sources. |
| Q7 | Recommande des films sur les rêves et la perception de la réalité. | `VECTOR` ; deux thèmes à vérifier. |
| Q8 | Quels films de Nolan avec DiCaprio parlent des rêves ? | `HYBRID_GRAPH_FIRST` ; seul candidat structurel : Inception. |
| Q9 | Quels films de science-fiction parlant d’intelligence artificielle ont été réalisés par des réalisateurs ayant travaillé avec Leonardo DiCaprio ? | `HYBRID_GRAPH_FIRST` ; 23 candidats structurels, puis sélection thématique. |
| Q10 | Quels films explorant la survie ont été réalisés par des réalisateurs ayant collaboré avec Matt Damon ? | `HYBRID_GRAPH_FIRST` ; 254 candidats structurels, puis sélection thématique. |

**Références exactes courtes :**

| Question | IDs attendus |
| --- | --- |
| Q1 | `155` The Dark Knight ; `272` Batman Begins ; `27205` Inception ; `49026` The Dark Knight Rises. |
| Q2 | `39,138,190,223,488,525,578,1032,1243,2034,2460,2710,5306,6201,7623,9181,59635`. |
| Q4 | `525` Christopher Nolan ; `2034` Danny Boyle. |
| Q8 | `27205` Inception est l’unique film structurellement admissible ; sa pertinence thématique doit aussi être prouvée. |

Les ensembles complets Q3, Q9 et Q10 sont dans `data/reports/reference.json`,
rapport local déjà généré. Pour Q9/Q10, ce sont des ensembles **structurels** :
ils ne constituent pas des listes de films thématiquement pertinents.

## 4. Première session courte

Commence par **R01, A01, T01, R06, G02, T05 et T06**.
Cela couvre une recherche exacte, les preuves, plusieurs sauts du graphe,
l’hybride, la fidélité, une ambiguïté et une recherche vide.
Ensuite, complète les autres cas. Les IDs de cas correspondent aux lignes du CSV.

## 5. Tests de retrieval

### R01 — retrouver exactement les bons films

**Question : Q1. Limite : 5.**

- `status=ok`, `strategy=GRAPH`, résultats de type `Movie`.
- Ensemble des IDs exactement égal à `{155,272,27205,49026}`.
- `coverage.total_candidates=4` et `coverage.returned=4`.
- Aucun doublon ni film supplémentaire.

**Échec :** film manquant, mauvais acteur, mauvais réalisateur, ou limite appliquée
avant la vérification des contraintes. L’ordre d’affichage n’est pas le critère principal.

### R02 — retrouver toutes les personnes et paginer

**Question : Q2.**

Avec une limite de 20 : 17 résultats `Person`, exactement les IDs de référence.
Avec une limite de 5 : quatre pages de tailles **5, 5, 5, 2** ; union des IDs égale
aux 17 attendus, sans répétition entre pages.
Le bouton Précédent doit restaurer la page précédente, et non afficher un autre ensemble.

### R03 — distinguer volume total et résultats affichés

**Question : Q3. Limite : 20.**

- `coverage.total_candidates=509`, 20 résultats sur la première page, `has_more=true`.
- Pour examiner directement la dernière page dans Swagger, utiliser `offset=500` :
  attendre 9 résultats et `has_more=false`.
- Une phrase indiquant qu’il n’existe que 20 acteurs est incorrecte.

Vérifier ces deux pages ne démontre pas le rappel sur les 509 personnes.
Ce rappel exige de comparer l’ensemble complet ; ne pas le noter comme mesuré ici.

### R04 — juger la recherche sémantique français–anglais

**Question : Q7. Limite : 5.**

- `strategy=VECTOR` ; examiner chaque film avec son synopsis anglais et ses keywords.
- Noter la pertinence de 0 à 3 selon la grille de la section 9.
- Vérifier un appui pour les rêves **et** la perception de la réalité.
- Aucune liste ni position exacte n’est imposée : il manque encore une référence humaine exhaustive.

Inception est un exemple pertinent à examiner s’il apparaît, pas une garantie
de classement. Son synopsis parle du subconscient ; le keyword `dream`, ID `1566`,
constitue aussi un appui. Il n’est pas nécessaire que le mot français « rêve » soit dans le synopsis.

### R05 — ne pas oublier un thème d’une conjonction

**Question : Q5.**

Vérifier dans `trace.plan.theme_request` les trois thèmes et `operator=ALL`.
Pour chaque résultat, contrôler un jugement et une preuve pour **chacun** des thèmes.
Un film seulement lié à l’espace ne satisfait pas automatiquement la survie et les
relations familiales. Une abstention faute de sources suffisantes peut être correcte.

### R06 — classer tous les candidats admissibles en hybride

**Question : Q9.**

- `strategy=HYBRID_GRAPH_FIRST`.
- Sur ce snapshot : `coverage.total_candidates=23`, `scored=23`, `without_embedding=0`.
- Les IDs finaux doivent appartenir à l’ensemble structurel Q9 du rapport de référence.
- Un `limit=5` ne doit pas réduire `scored` à 5.
- Chaque résultat satisfait les contraintes de graphe **et** possède un appui pour l’IA.

Un résultat hors des contraintes est un échec même si sa similarité sémantique est élevée.
Il ne faut pas attendre 23 recommandations : 23 est le nombre avant sélection thématique.
Ces compteurs vérifient la couverture déclarée par l’API. Le classement intermédiaire
complet et les IDs de tous les candidats scorés ne sont pas exposés : ce contrôle
manuel ne certifie pas à lui seul chaque calcul de similarité.

### R07 — exposer la couverture partielle

**Question : Q10.**

Avec le budget initial de 100 candidats : `total_candidates=254`,
`scored + without_embedding = 254`, `examined<=100`, `semantic_coverage=partial`,
avertissement de couverture partielle. Si aucun vecteur ne manque, `scored=254` ;
la référence relationnelle seule ne prouve pas cette couverture vectorielle.
L’examen peut s’arrêter avant 100 lorsque la limite de réponses est atteinte.
La réponse ne doit pas annoncer tous les films pertinents du corpus.

Les 254 films sont des candidats relationnels, pas 254 films prouvés pertinents pour la survie.

## 6. Tests d’augmentation et de preuves

Ces cas vérifient les preuves accessibles. La préparation exacte du contexte LLM
reste partiellement observable, comme indiqué dans A05.

### A01 — aucune référence de preuve inexistante

**Réutiliser la réponse Q8.**

Pour chaque `results[].evidence_ids`, retrouver l’entrée correspondante dans
`evidence[].id`. Vérifier que son `entity_id` correspond au résultat justifié.
Une référence existante attachée au mauvais film reste une erreur.
En hybride, attendre au moins une preuve `relation` et un appui `overview` ou `keyword`.

### A02 — chaque extrait existe exactement dans la source

**Réutiliser Q8 ou Q6.**

Pour une preuve `kind=overview`, ouvrir `GET /movies/{id}` dans Swagger, ou la
fiche du film. La chaîne `evidence[].data.quote` doit être un extrait continu exact
du champ `overview`, y compris sa formulation anglaise.
Ensuite, vérifier son sens : un extrait exact mais hors sujet ne soutient pas le thème.
Si la réponse ne contient que des keywords, noter ce sous-test `INCONCLUSIF` et examiner un autre résultat.

### A03 — keywords et centralité restent distincts

**Question : « Quels films de Christopher Nolan parlent d’intelligence artificielle ? »**

Si Interstellar (`157336`) est proposé : le keyword `artificial intelligence`,
ID `310`, peut soutenir une association. Son synopsis disponible parle d’exploration
et de trou de ver, sans démontrer la centralité de l’IA.

- Une preuve fondée uniquement sur ce keyword doit garder `centrality=unknown`.
- Le keyword cité doit correspondre à l’ID et au nom présents sur le film.
- Des descriptions de robots ou de personnages absentes du synopsis ne doivent pas être inventées.

Si Interstellar n’est pas retourné, contrôler ses sources via `GET /movies/157336`,
mais noter l’évaluation de sa réponse `INCONCLUSIF` : lire sa fiche ne prouve pas
que le pipeline aurait correctement formulé sa recommandation.

### A04 — conserver les contraintes et les films témoins

**Réutiliser Q9.**

Comparer d’abord le plan à la question originale : DiCaprio, le réalisateur relié,
le film résultat, le genre science-fiction et l’IA doivent tous être conservés.
Pour un résultat accepté, vérifier que les preuves permettent de justifier chaque contrainte.
Un plan amputé d’une contrainte peut produire des chemins valides tout en répondant mal à la question.

Les sources doivent appartenir au même snapshot : comparer `trace.snapshot_id`
aux `snapshot_id` présents sur les preuves thématiques, les nœuds Movie et les
relations `ACTED_IN`/`DIRECTED`. Les nœuds Person/Genre et les relations `IN_GENRE`
ne portent pas tous ce champ ; son absence à ces endroits n’est pas un échec.

### A05 — ne pas confondre preuves affichées et contexte réellement envoyé

**Réutiliser une réponse hybride ou vectorielle.**

L’API ne retourne pas le contexte exact construit par `build_context`.
Par conséquent, marquer son inspection directe **`NON_OBSERVABLE`** avec l’API actuelle.
L’absence d’avertissement ne suffit pas à marquer ce test réussi.

Si un avertissement de contexte limité apparaît, les résultats structurés conservent
leurs preuves, mais le contexte de synthèse peut être plus petit. Le code actuel
désactive alors cette synthèse et utilise un rendu déterministe.
Une future trace de contexte et de mode de génération permettrait d’auditer cette
étape directement ; ce guide ne modifie ni la configuration ni l’application pour la provoquer.

## 7. Tests de génération et de réponse finale

La **fidélité** consiste à vérifier que les affirmations sont soutenues par les
informations fournies au système, et pas uniquement plausibles. C’est une dimension
distincte de la pertinence, décrite dans la [documentation Ragas](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/).
Ici, son audit sur les preuves visibles reste une approximation lorsque le contexte exact n’est pas disponible.

### G01 — répondre dans la langue demandée et à la bonne question

**Question : Q8, avec `language=fr`.**

Le texte explicatif doit être français ; les titres et citations sources peuvent
rester anglais. Il doit répondre à la demande sur Nolan, DiCaprio et les rêves.
Ne pas exiger une phrase exacte ou un nombre fixe de mots.
La qualité de rédaction d’un LLM ne peut être certifiée si le mode de génération est inconnu.

### G02 — chaque affirmation est réellement soutenue

**Réutiliser Q8 ou Q9.**

Découper la réponse en affirmations factuelles. Pour chacune :

1. Repérer sa citation.
2. Ouvrir la preuve correspondante.
3. Vérifier que la preuve soutient **cette affirmation précise**.

Pour Inception : la participation de DiCaprio et la réalisation de Nolan nécessitent
des relations de crédits ; le thème du rêve nécessite un appui thématique.
Un score vectoriel n’établit aucune de ces affirmations à lui seul.

Le validateur de génération contrôle l’existence des IDs cités, pas toute l’implication
sémantique. Une citation valide avec une interprétation fausse doit donc échouer ici.

### G03 — ne pas compléter la réponse avec des connaissances externes

**Réutiliser Q8.**

Vérifier qu’aucun autre film, personnage, dialogue, prix ou détail d’intrigue n’est
ajouté sans source disponible. Même une information vraie dans le monde réel est
non étayée si elle ne vient pas des preuves de cette réponse.
Une référence contenant un autre fait ne justifie pas cet ajout.

### G04 — respecter une demande de thème central

**Question : « Quels films de Christopher Nolan sont centrés sur l’intelligence artificielle ? »**

Dans le plan, attendre `theme_request.requirement=central`.
Un résultat doit posséder un appui du synopsis montrant la centralité du thème.
Interstellar ne doit pas être accepté comme centré sur l’IA sur la seule base du keyword `310`.
Si aucune source suffisante n’est trouvée parmi les candidats, une réponse
`insufficient_evidence` est préférable à une justification inventée.

### G05 — distinguer absence, recherche vide et demande non couverte

Exécuter les trois questions et vérifier les statuts :

| Question | Attente |
| --- | --- |
| Quels films sont réalisés par UnePersonneInexistanteXYZ ? | `entity_not_found` ; aucune invention d’identité. |
| Quels films de Christopher Nolan avec Cillian Murphy sont sortis en 2050 ? | `no_match_in_dataset` ; entités connues, mais aucun film satisfaisant les filtres. |
| Quels films de Christopher Nolan n’ont pas Leonardo DiCaprio au casting ? | `unsupported` ; la négation est hors périmètre actuel. |

Ne pas transformer une erreur fournisseur en l’un de ces verdicts métier.
Ne pas supprimer une contrainte pour proposer malgré tout des films.

### G06 — stabilité et classification de la sortie

**Facultatif : répéter Q1 et Q8 trois fois, séparément.**

Pour Q1, exiger le même ensemble de quatre IDs à chaque réussite ; la rédaction
LLM finale est `N/A`, car le rendu est déterministe.
Pour Q8, vérifier à chaque réussite le film admissible et les contraintes/provenances.
La formulation, les citations choisies et les sélections sémantiques peuvent varier.

Noter séparément une erreur d’analyse, une variation de classement et une erreur
de fournisseur. Plusieurs appels Groq dans la trace ne démontrent pas une synthèse.
Sans mode de génération exposé, noter « réponse finale évaluée, synthèse LLM non certifiée ».

## 8. Tests du raisonnement sur le graphe

Les flèches ci-dessous représentent des relations stockées. Les films témoins
peuvent varier si plusieurs chemins satisfont la question.

### T01 — autoriser deux collaborations dans des films différents

**Question : Q4.**

Attendre exactement Nolan (`525`) et Boyle (`2034`).
Pour Boyle, deux témoins vérifiés dans les crédits sont :

```text
DiCaprio (6193) ─ACTED_IN→ The Beach (1907) ←DIRECTED─ Boyle (2034)
Murphy   (2037) ─ACTED_IN→ 28 Days Later (170) ←DIRECTED─ Boyle (2034)
```

La question n’exige pas que les deux acteurs aient travaillé avec Boyle dans le
même film. Ne pas imposer un témoin précis si un autre chemin réel satisfait les deux relations.

### T02 — respecter au contraire une demande de film commun

**Question : « Quels réalisateurs ont dirigé Leonardo DiCaprio et Cillian Murphy dans le même film ? »**

Attendre Nolan (`525`) avec Inception (`27205`) comme témoin commun.
La structure doit utiliser le même film pour les deux relations `ACTED_IN`.
Boyle ne doit pas être conservé en assemblant deux films différents.

### T03 — distinguer film résultat et film témoin sur quatre relations

**Question : Q9.**

Structure attendue :

```text
DiCaprio ─ACTED_IN→ film_témoin ←DIRECTED─ réalisateur
réalisateur ─DIRECTED→ film_résultat ─IN_GENRE→ Science Fiction
film_résultat : appui thématique sur l’IA
```

Exemple relationnel vérifié : Spielberg (`488`) a dirigé DiCaprio dans
Catch Me If You Can (`640`) et a réalisé A.I. Artificial Intelligence (`644`),
classé Science Fiction. Son synopsis décrit un garçon robot, ce qui fournit un
appui thématique à examiner si ce résultat est proposé.

DiCaprio n’a pas à jouer dans le film résultat. L’absence d’A.I. du top affiché
n’est pas, à elle seule, un échec ; l’exemple sert à contrôler un chemin, pas à imposer un classement.

### T04 — ne pas imposer deux films distincts sans demande

**Question : Q10.**

Le film témoin peut être le film résultat. Exemple vérifié dans le corpus :
The Martian (`286217`), réalisé par Ridley Scott (`578`), avec Matt Damon (`1892`).
Son synopsis décrit la survie d’un astronaute et possède le keyword `survival` (`10349`).
Si proposé, il ne doit pas être rejeté uniquement parce qu’il sert aussi de témoin.
Inversement, Damon n’est pas obligé de jouer dans chaque film recommandé.

### T05 — l’identité prime sur la popularité

**Question : « Quels films ont Tom Holland au casting ? »**

Les crédits contiennent deux personnes nommées Tom Holland : `1136406` et `64796`.
Si elles restent compatibles avec le contexte demandé, attendre
`needs_clarification`, puis sélectionner explicitement `1136406` dans l’interface.
La recherche doit reprendre sur cet ID et ses relations, sans choisir automatiquement
l’homonyme qui produit le plus de résultats.

Si la question est modifiée, une ancienne sélection ne doit pas être réutilisée.
Les rôles et films affichés servent à identifier la personne, pas à inventer des relations.

### T06 — la contrainte de date ne disparaît pas

**Question : « Quels films de Christopher Nolan avec Cillian Murphy sont sortis en 2050 ? »**

Attendre `GRAPH`, un filtre d’année égal à 2050 sur le film résultat,
`coverage.total_candidates=0`, `status=no_match_in_dataset` et aucun résultat.
Ne pas retourner les quatre films de Q1 en oubliant l’année, ni basculer vers des recommandations.

## 9. Noter les résultats sans inventer de métriques

### Verdict d’un cas

| Valeur | Signification |
| --- | --- |
| `NON_EXECUTE` | Pas encore essayé. |
| `PASS` | Toutes les assertions observables du cas sont satisfaites. |
| `FAIL` | Une assertion est contredite, avec exemple et preuve conservés. |
| `INCONCLUSIF` | Observation insuffisante pour trancher, par exemple film absent du top. |
| `NON_OBSERVABLE` | L’application n’expose pas l’information requise, comme le contexte exact. |
| `ERREUR_DEPENDANCE` | Fournisseur, réseau ou service indisponible ; ne pas compter cela comme une erreur sémantique. |
| `N/A` | Dimension non applicable, par exemple rédaction LLM finale d’une réponse purement GRAPH. |

Dans la grille, `score_0_3` concerne seulement une appréciation de pertinence ou de
qualité explicitement décrite. Laisser vide quand il ne s’applique pas ; ne pas
remplacer une mesure manquante par zéro. Pour plusieurs films, détailler leurs
notes dans `resultats_observes` plutôt que masquer leurs différences par une moyenne seule.

### Mesures utiles

| Mesure | Calcul ou procédure | Limite |
| --- | --- | --- |
| Exactitude d’ensemble du graphe | Ensemble retourné égal à l’ensemble attendu après toutes les pages. | Un même nombre de résultats ne suffit pas. |
| Précision des résultats affichés | Bons résultats / nombre de résultats affichés. | Préciser les critères et la référence utilisée. |
| Rappel d’un ensemble | Bons résultats / nombre total de résultats attendus. | Seulement si cette référence est complète. |
| Pertinence thématique | Noter chaque film de 0 à 3 selon les sources et la demande. | Une note humaine reste à documenter ; pas de vérité inventée. |
| Validité des références | Références de preuve existantes / références citées. | Ne mesure pas leur interprétation. |
| Soutien des affirmations | Affirmations effectivement étayées / affirmations factuelles vérifiées. | Proxy sur les preuves visibles tant que le contexte exact n’est pas exposé. |
| Respect des contraintes | Résultats respectant toutes les contraintes / résultats proposés. | Comparer à la question originale, pas seulement au plan. |
| Disponibilité | Réponses métier obtenues hors erreur de dépendance/timeout / tentatives. | Une clarification correcte compte comme traitée ; un HTTP 200 avec `dependency_error` ne suffit pas. |
| Latence et consommation | Conserver `trace.latency_ms` et `trace.llm_usage` sur chaque exécution. | Comparer des tâches, modèles et budgets similaires. |

Si le dénominateur est nul, noter `N/A` et évaluer séparément l’abstention.
`examined / total_candidates` mesure une couverture d’inspection, **pas un rappel sémantique**.

**Exemple Q1 :** quatre bons films parmi les quatre attendus donnent précision 1,
rappel 1 et exactitude d’ensemble vraie. Le `precision_at_5` du runner existant
divise toujours par cinq : il vaut alors 0,8. Cela ne signifie pas que la réponse
parfaite contient 20 % d’erreurs. De même, un seul bon film pour Q8 donne un P@5 de 0,2.
Ce sont des exemples de calcul, pas des scores de benchmark déjà mesurés. Sans
annotations de pertinence validées, le runner laisse les métriques concernées à `null`.

### Grille de pertinence 0–3 proposée

- **0** : hors sujet, contrainte obligatoire violée, ou thème obligatoire sans appui.
- **1** : pertinence faible ; appui existant mais interprétation peu convaincante.
- **2** : film pertinent, avec des sources adaptées à la demande.
- **3** : film très pertinent, avec un appui particulièrement explicite et convaincant.

Pour une demande d’association, un keyword précis peut suffire. Pour une demande
de centralité, un keyword isolé ne suffit pas : un résultat fondé uniquement sur
cette annotation doit être rejeté, même si le film est populaire.
La centralité n’est donc pas interchangeable avec cette note générale de pertinence.

## 10. Comment prolonger cela dans un projet réel

Pour ce projet, la progression recommandée est :

1. Conserver des questions, références, sources et verdicts versionnés dans Git.
2. Vérifier séparément les contrats techniques dans Docker et la pertinence humaine.
3. Réserver un ensemble de développement aux réglages et un ensemble de test aux comparaisons finales.
   Garder les paraphrases d’une même question dans le même ensemble pour limiter les fuites.
4. Examiner le pool de films retournés par tous les systèmes pour annoter aussi leurs différences.
   Un film non encore jugé ne doit pas être assimilé automatiquement à un mauvais film.
5. Comparer le pipeline complet avec les baselines à questions et représentations documentées.
6. Rejouer les cas après changement de prompt, modèle, index ou données ; analyser les régressions par composant.
7. Plus tard, relire un échantillon de questions réelles, les abstentions et les erreurs de dépendances.
   Un juge LLM éventuel peut aider, avec contrôle humain d’un échantillon et de ses désaccords.

Les seuils d’acceptation doivent être fixés selon le produit. Pour ce MVP, nous
proposons de bloquer une validation sur une contrainte relationnelle violée, une
preuve inventée, une identité ambiguë choisie arbitrairement ou une fausse centralité
fondée seulement sur un keyword. Ce sont des objectifs de validation, pas des résultats mesurés.

### Ce qui existe déjà dans le dépôt

- `evaluation/cases.jsonl` contient 80 cas : 20 graphe, 20 vectoriels, 20 hybrides et 20 robustesse.
- Dans l’état relu pour ce guide, 19 cas graphe ont une référence `csv_verified` ;
  le cas sur Will Smith reste ambigu. Les pertinences thématiques attendent une revue humaine.
- Les champs non annotés ne démontrent donc aucun score global de qualité.
- `evaluation/reports/` ne contient encore aucun rapport comparatif enregistré.
- Les XML de tests techniques dans `data/reports/` attestent des exécutions
  historiques, pas d’un benchmark thématique ni de l’exécution de ce cahier.

La grille CSV est un compte rendu manuel ; elle n’est pas importée automatiquement
par le runner. Les références utilisées par celui-ci résident dans
`evaluation/cases.jsonl`. Ne marquer une annotation `human_reviewed` qu’après
relecture effective, et `exhaustive=true` que si l’ensemble attendu est réellement complet.

Le runner `--system graph` désigne le **pipeline MovieGraphRAG complet**, qui peut
router vers GRAPH, VECTOR ou HYBRID. Les systèmes `vector` et `flat` sont des
baselines de retrieval ; ils ne génèrent pas de réponse finale.
Ils ne suffisent donc pas à comparer la qualité de génération.

Les baselines renvoient des films : ne pas comparer leurs IDs Movie aux IDs Person
des questions Q2–Q4. Comparer plutôt les documents témoins ou une tâche de même type.
Leur retrieval exact n’a pas non plus la même portée que la latence complète du pipeline.

Les commandes suivantes sont **facultatives et non lancées par ce guide**. Elles
utilisent le conteneur existant, sans build ni installation. `--system graph`
appelle réellement Groq ; `vector` et `flat` encodent et classent localement sans appel LLM :

```powershell
docker compose exec api moviegraphrag evaluate --system graph --split dev --output evaluation/reports/manual-graph-dev.json
docker compose exec api moviegraphrag evaluate --system vector --split dev --output evaluation/reports/manual-vector-dev.json
docker compose exec api moviegraphrag evaluate --system flat --split dev --output evaluation/reports/manual-flat-dev.json
```

Elles peuvent encoder de nombreux documents et consommer des appels API : commencer
par les cas manuels. Les métriques de pertinence ne sont interprétables qu’après
annotation des références ; elles ne remplacent pas les verdicts du présent cahier.
Le runner calcule aussi nDCG@5 pour tenir compte du rang et de notes graduées.
Avec un pool incomplet, ne pas annoncer un rappel exhaustif du corpus.

## 11. Diagnostiquer un échec

| Observation | Première étape à examiner |
| --- | --- |
| Mauvais homonyme | `resolved_entities`, contexte de linking et choix de clarification. |
| Condition absente dès le plan | Analyseur ; relire `trace.plan` face à la question. |
| Plan correct, mauvais ensemble structurel | Compilateur Cypher, relations et filtres du graphe. |
| Bon ensemble, propositions thématiques faibles | Classement et appréciations thématiques ; les résultats seuls ne permettent pas de les isoler complètement. |
| Bon film, preuve absente ou attachée au mauvais résultat | Construction et validation des preuves. |
| Bonnes preuves, affirmation non soutenue | Réponse finale ; vérifier aussi si une synthèse LLM ou un rendu de secours a été utilisé. |
| Contexte exact introuvable | Limite d’observabilité, pas un succès implicite de l’augmentation. |
| Erreur Groq / délai dépassé | Dépendance et disponibilité ; conserver la trace avant de retenter. |

**Une anomalie utile contient :** question exacte, limite/offset, `trace_id`, résultat
attendu, résultat observé, IDs et preuve concernés. « La réponse semble mauvaise »
ne suffit pas à localiser ni reproduire le problème.

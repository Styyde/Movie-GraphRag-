# Architecture de MovieGraphRAG

Voir le [dossier complet](architecture_complete_soutenance.md) et la [fiche de révision](fiche_revision_soutenance.md).

## Ingestion

~~~mermaid
flowchart TD
    CSV["CSV films et crédits"] --> V["Validation et jointure par ID"]
    V --> N["Normalisation et agrégation"]
    N --> G["Person, Movie, Genre et relations"]
    N --> D["Un document par film"]
    D --> E["E5 local : 384 dimensions"]
    G --> DB[("Neo4j")]
    E --> DB
    DB --> M["Contrôles et manifeste ready"]
~~~

Les relations viennent des champs structurés, pas des embeddings. Person porte les rôles par ACTED_IN et DIRECTED. Movie est relié à Genre par IN_GENRE ; ses keywords restent des propriétés. Corpus conserve le manifeste technique.

Un document titre + synopsis + genres + keywords par film, sans overlap. Contrôle réel de la fenêtre E5 de 512 tokens ; pas de troncature silencieuse du synopsis. Le snapshot contient 4 803 films et 4 801 embeddings.

## Questions

~~~mermaid
flowchart TD
    Q["Question"] --> A["Analyse structurée Groq"]
    A --> V["Validation du plan et de la quantité explicite"]
    V --> E["Entity Linking et clarification"]
    E --> R{"Opération"}
    R -- count --> C["COUNT DISTINCT sans LIMIT"]
    R -- list --> G["Contraintes puis pagination"]
    R -- recommend --> S["Cosine exact : corpus ou candidats admissibles"]
    S --> K["Top-k et sources documentaires"]
    K --> CT["Contexte et chemins témoins"]
    CT --> L["Synthèse contrôlée ou rendu déterministe"]
    C --> O["Réponse et provenance"]
    G --> O
    L --> O
~~~

Le LLM fournit une intention déclarative, jamais des IDs TMDB ni du Cypher libre. Python compile des motifs autorisés, paramétrés, connectés et acycliques de quatre relations au plus.

- COUNT : nombre exact de films/personnes distincts, y compris zéro pour une identité connue sans correspondance.
- LIST : tous les candidats structurels, puis page déterministe et total.
- RECOMMEND : scoring exact de tous les films encodés, ou de tous les films encodés admissibles en hybride ; score décroissant puis ID.

Quantité explicite de la question prioritaire sur la limite de l'interface ; défaut cinq, maximum vingt avec avertissement. Le comptage ignore cette limite.

Aucun appel d'appréciation thématique, plafond de cent inspections ou reranker dans cette version. L'index vectoriel est conservé mais n'intervient plus dans la sélection actuelle.

## Sources et garanties

Preuves relation, document et aggregate. Les documents sont vérifiés par identité, snapshot, modèle et hash ; cette intégrité ne certifie pas la pertinence thématique.

Le contexte JSON conserve sources et références sous 24 000 caractères, sans vecteurs. Les listes et comptes sont déterministes ; la synthèse des recommandations peut utiliser Groq. Les références inconnues sont refusées, mais la fidélité sémantique reste à évaluer humainement.

Les comptes thématiques, exigences de centralité certifiée, négations et agrégations non prévues demandent une reformulation. Les recommandations ne constituent pas une liste thématique exhaustive.

## Exécution et vérification

API et Neo4j restent dans leurs conteneurs, sans dépendance supplémentaire. Les tests utilisent exclusivement Docker et les services réels. Les parcours navigateur restent écrits mais non exécutés, leur installation ayant été interrompue.

V2 : benchmark, puis reranker multilingue seulement si un gain est mesuré. Aucun reranker ne remplacera COUNT DISTINCT ni les contraintes obligatoires.

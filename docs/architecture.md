# architecture

## ingestion

```mermaid
flowchart TD
  A[CSV] --> B[validation et jointure par ID]
  B --> C[normalisation et crédits agrégés]
  C --> D[graphe idempotent]
  C --> E[document par film]
  E --> F[tokenisation et E5 local]
  D --> N[(Neo4j)]
  F --> N
  N --> G[index et contrôle du snapshot]
```

## questions

```mermaid
flowchart TD
  A[question] --> B[analyse structurée]
  B --> C[validation et linking]
  C --> D{résolu}
  D -- non --> E[clarification]
  D -- oui --> R[router déterministe]
  R -- graph --> G[contraintes et témoins]
  R -- vector --> V[index vectoriel]
  R -- hybrid --> H[tous les candidats du graphe]
  H --> S[scoring exact]
  S --> P[preuves thématiques]
  V --> P
  P --> X[contexte]
  G --> X
  X --> Y[rendu ou synthèse contrôlée]
```

Le modèle métier compte Movie, Person, Genre et trois types de relations. Un nœud
technique Corpus conserve le manifeste ; il n'est pas compté parmi les 61 426 nœuds métier.

Le compilateur accepte uniquement des arbres connectés de quatre relations au plus,
avec ancres et directions conformes au schéma. Les variables sont remplacées par
des alias internes ; les valeurs sont toujours des paramètres.

La désambiguïsation ne recherche pas l'homonyme qui fournit une réponse. Elle utilise
le nom exact, les rôles explicitement demandés et les films d'identification explicites.
Une réponse vide ne supprime jamais de contrainte.

L'index natif sert aux recherches thématiques pures. Pour l'hybride, le nombre de
candidats du graphe n'est pas limité avant le scoring. Seule leur inspection LLM
est bornée et cette couverture est exposée. Les preuves sont inspectables même si
la génération échoue.

Les valeurs initiales ne sont pas des garanties mesurées : 20 films par lot, 100
films inspectés, 5 secondes par transaction de lecture, 24 000 caractères de contexte.
Les résultats structurés ne sont pas tronqués quand le contexte de synthèse est limité.

V2 : ingestion incrémentale, alias enrichis, motifs supplémentaires, benchmark des
stratégies vector-first/parallèles et reranker seulement si un gain est démontré.

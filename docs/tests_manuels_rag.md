# MovieGraphRAG — cahier de tests manuels

Ce cahier décrit la version avec comptages exacts et classement E5 complet. Aucun scénario n'est exécuté automatiquement par ce document. Remplir la [grille](resultats_tests_manuels.csv) uniquement après de vrais essais.

## 1. Comment tester un RAG

| Partie | Ce qu'on vérifie |
| --- | --- |
| Retrieval relationnel | Bons IDs, contraintes respectées, total distinct de la page. |
| Retrieval sémantique | Tous les vecteurs admissibles scorés et pertinence des recommandations à noter humainement. |
| Augmentation | Sources fidèles, documents et chemins identifiés, contexte borné. |
| Génération | Langue, utilité, fidélité aux sources et références existantes. |
| Raisonnement | Bonne portée des variables et films témoins. |
| Comptage | Valeur exacte indépendante de la limite affichée. |

Un score vectoriel n'est pas une preuve de pertinence. Le système n'effectue plus d'appréciation thématique Groq et ne certifie pas la centralité.

## 2. Préparation

Ouvrir http://localhost:8000, attendre Prêt à explorer. Ouvrir http://localhost:8000/docs pour consulter le JSON. Aucun outil supplémentaire n'est nécessaire.

Pour chaque cas, noter question, limite de l'interface, statut, trace_id, IDs, count éventuel, couverture, références, latence et anomalie.

Les champs utiles :
- operation : list, count ou recommend.
- count : valeur du comptage ; null ailleurs.
- coverage.total_candidates, scored, sources_checked, without_embedding, returned.
- coverage.effective_limit et ranking_coverage ; aucune vérification thématique n'est sous-entendue.
- thematic_verification : not_performed pour les recommandations.
- trace.selection_method, scoring_ms, generation_mode, context_truncated, context_evidence_ids, llm_usage.stages.

Un statut HTTP 200 peut contenir unsupported ou dependency_error. Le nombre d'appels réseau inclut les reprises ; consulter les étapes pour distinguer analyse et génération.

## 3. Questions et références du snapshot

| Référence | Question |
| --- | --- |
| Q1 | Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ? |
| Q2 | Quels réalisateurs ont travaillé avec Leonardo DiCaprio ? |
| Q3 | Quels acteurs ont joué dans des films réalisés par Christopher Nolan ? |
| Q4 | Quels réalisateurs ont travaillé avec Leonardo DiCaprio et Cillian Murphy ? |
| Q5 | Trouve des films sur l'exploration spatiale, la survie et les relations familiales. |
| Q6 | Quels films explorent les conséquences de l'intelligence artificielle sur l'humanité ? |
| Q7 | Recommande des films sur les rêves et la perception de la réalité. |
| Q8 | Quels films de Nolan avec DiCaprio parlent des rêves ? |
| Q9 | Quels films de science-fiction parlant d'intelligence artificielle ont été réalisés par des réalisateurs ayant travaillé avec Leonardo DiCaprio ? |
| Q10 | Quels films explorant la survie ont été réalisés par des réalisateurs ayant collaboré avec Matt Damon ? |

Références structurelles : Q1 = IDs 155, 272, 27205, 49026 ; Q2 = 17 personnes ; Q3 = 509 personnes ; Q4 = Nolan 525 et Boyle 2034 ; Q8 = Inception 27205 ; Q9 = 23 candidats ; Q10 = 254 candidats.

Ces références concernent le snapshot 951f77da9b7278fdb95700d6a489a7b4ca40fc6b8e6f806fd14136965de40965. Les ensembles Q9/Q10 ne sont pas des jugements thématiques.

## 4. Retrieval — sept cas

### R01 — ensemble exact
Poser Q1 avec limite cinq. Attendre list/GRAPH, les quatre IDs, total=4 et quatre preuves relationnelles. Aucun cinquième film ajouté.

### R02 — pagination des personnes
Poser Q2 avec limite cinq. Attendre total=17, cinq Person sur la première page, puis une page suivante sans doublon. Précédent doit réutiliser la page en cache.

### R03 — volume et page
Poser Q3 avec limite vingt. Attendre total=509, vingt personnes, has_more=true. Ne pas annoncer qu'il n'existe que vingt acteurs. Deux pages ne démontrent pas à elles seules le rappel de l'ensemble.

### R04 — recherche français–anglais
Poser Q7. Attendre recommend/VECTOR, scored=4801, approximate=false, without_embedding=2, thematic_verification=not_performed. Lire les synopsis et noter la pertinence des cinq premiers.

### R05 — plusieurs thèmes
Poser Q5. Vérifier que les préférences thématiques restent dans trace.plan.theme_request. Noter séparément l'appui de chaque thème dans les sources. Le système ne doit pas affirmer que leur conjonction est certifiée.

### R06 — tous les candidats hybrides
Poser Q9. Attendre total_candidates=23, scored=23 et contraintes genre/collaboration visibles dans les témoins. La pertinence IA reste à juger humainement.

### R07 — suppression du plafond cent
Poser Q10. Attendre total_candidates=scored=254. sources_checked concerne les documents retenus, pas une inspection thématique de tous les candidats. Aucun champ examined ou semantic_coverage hérité de l'ancien parcours.

## 5. Augmentation — cinq cas

### A01 — références existantes
Poser Q8. Pour chaque evidence_id du résultat, trouver une preuve correspondante. Attendre document et relation. Cliquer les références de la réponse et ouvrir la bonne section.

### A02 — sources verbatim
Poser Q6 ou Q8. Comparer le synopsis et les keywords de la preuve document avec GET /movies/{id}. Ils doivent correspondre aux données sources, sans extrait inventé. Vérifier snapshot et hash.

### A03 — annotation et centralité
Demander des recommandations de films de Nolan sur l'IA. Si Interstellar est proposé, le keyword artificial intelligence soutient une annotation mais ne certifie pas sa centralité. Aucune pastille central/peripheral ni promesse de vérification thématique.

### A04 — film témoin
Poser Q9. Le film où DiCaprio a joué peut être différent du film recommandé. Le chemin doit contenir le réalisateur commun et le genre du résultat.

### A05 — contexte réellement utilisable
Vérifier trace.context_evidence_ids et generation_mode. Si context_truncated=true, attendre le rendu déterministe et les sources complètes dans l'API. Une preuve affichée n'est pas automatiquement une preuve utilisée par une synthèse.

## 6. Génération — six cas

### G01 — langue et utilité
Poser Q8. Réponse en français, Inception, références aux documents et relations. Vérifier si generation_mode vaut llm_synthesis ou deterministic.

### G02 — fidélité de chaque phrase
Poser Q6 ou Q9. Pour chaque affirmation, ouvrir la source citée. Une référence existante ne suffit pas si la phrase ne découle pas de son contenu. Noter chaque inférence abusive.

### G03 — pas de film ajouté
Poser Q8 avec limite vingt. Le seul candidat structurel est Inception. Le texte ne doit pas ajouter d'autres titres depuis la mémoire du LLM.

### G04 — demande stricte
Demander : « Uniquement des films centrés sur l'intelligence artificielle. »
Attendre unsupported et une demande de reformulation en recommandations, sans classement qui prétendrait certifier la centralité.

### G05 — statuts distincts
Tester un réalisateur inexistant : entity_not_found.
Tester Nolan avec Murphy après 2050 : no_match_in_dataset.
Demander un compte thématique (« combien de films parlent d'IA ? ») : unsupported, count=null.
Une panne fournisseur reste dependency_error, sans nombre inventé.

### G06 — quantité et stabilité
Poser « Recommande trois films sur l'IA » avec dix dans l'interface : trois résultats maximum.
Puis « Recommande un film sur les rêves » : un.
Puis « Quels films sur l'IA ? » avec cinq : cinq par défaut.
Avec vingt-cinq explicitement demandés : maximum vingt et avertissement.
À requête E5 identique, l'ordre est score décroissant puis ID ; le texte généré peut varier.

## 7. Raisonnement du graphe — six cas

### T01 — témoins différents autorisés
Poser Q4. Attendre Nolan et Boyle. Boyle est justifié par The Beach avec DiCaprio et 28 Days Later avec Murphy ; le même film n'est pas imposé.

### T02 — film commun explicitement demandé
Demander les réalisateurs ayant dirigé DiCaprio et Murphy dans un même film. Attendre Nolan via Inception ; ne pas conserver Boyle à partir de deux films différents.

### T03 — résultat différent du témoin
Poser Q9. Si A.I. Artificial Intelligence apparaît, vérifier Spielberg et Catch Me If You Can comme témoin de collaboration. Ne pas prétendre que DiCaprio joue dans A.I.

### T04 — aucun distinct implicite
Poser Q10. Un film résultat peut aussi être un film témoin de collaboration. Le plan ne doit pas imposer deux films différents sans demande.

### T05 — homonymes
Demander les films avec Tom Holland. Attendre needs_clarification et les IDs 1136406 / 64796. Sélectionner une identité, puis vérifier les résultats de cette personne. Une préférence pour l'acteur célèbre ne vaut pas preuve.

### T06 — date conservée
Demander les films de Nolan avec Murphy sortis après 2050. Attendre total=0, liste vide et no_match_in_dataset. Ne pas supprimer le filtre pour obtenir des résultats.

## 8. Comptages — cinq cas

### C01 — quatre films
« Combien de films ont été réalisés par Christopher Nolan avec Cillian Murphy ? »
Attendre operation=count, count=4, results=[], preuve aggregate. Dans Swagger, envoyer `limit: 1` : le compte reste quatre. L’interface propose seulement 5, 10 ou 20 ; changer ce choix ne modifie pas le compte.

### C02 — 509 acteurs
« Quel est le nombre d'acteurs ayant joué dans des films réalisés par Christopher Nolan ? »
Attendre count=509, pas vingt ni cinq. La réponse est déterministe, sans scoring ni génération.

### C03 — zéro pour une personne connue
« Combien de films ont été réalisés par Leonardo DiCaprio ? »
Attendre count=0 dans ce corpus, identité 6193 résolue. Ne pas transformer zéro en personne absente.

### C04 — comptes globaux et rôles
« Combien de films contient le dataset ? » : 4803.
« Combien de personnes contient le dataset ? » : 56603.
Pour les acteurs/réalisateurs, comparer aux ensembles distincts des CSV ; une personne jouant dans dix films est comptée une fois.

### C05 — absence, ambiguïté et limite de périmètre
Un nom inventé donne entity_not_found et count=null.
Tom Holland sans contexte donne une clarification, pas un compte arbitraire.
Un compte thématique est unsupported. La limite d'affichage ne doit jamais borner COUNT DISTINCT.

## 9. Noter sans fabriquer de métriques

Verdicts : REUSSI, ECHEC, A_REVOIR ou NON_EXECUTE. Ne pas remplir une réussite à partir d'un exemple théorique.

Pour la pertinence thématique, proposer une note humaine :
- 0 : hors sujet ;
- 1 : association faible ;
- 2 : appui utile ;
- 3 : correspondance forte à la demande.

Noter chaque thème séparément si nécessaire. Une annotation manquante ne devient pas automatiquement zéro. Le rappel n'est mesurable qu'avec une référence suffisamment complète.

Mesurer les ensembles relationnels, les comptes, precision@k/nDCG@k, la fidélité des citations, les violations de contraintes, la latence et le coût. scored/total_candidates mesure une couverture de calcul, pas le rappel sémantique.

Le runner P@5 divise par cinq : quatre résultats corrects pour Q1 donnent 0,8, même si l'ensemble est exact. Ne pas comparer directement des IDs Person à des IDs Movie des baselines.

## 10. Campagnes automatiques et limites

Les tests automatiques tournent seulement dans Docker, avec les vrais services. Les rapports architecture-*.xml concernent cette adaptation ; les anciens frontend-*.xml et docker-tests.xml sont historiques.

Les 80 cas d'évaluation et les baselines restent disponibles. Les références relationnelles sont calculées depuis les CSV ; la pertinence et la fidélité demandent une annotation humaine. Aucun gain global du GraphRAG n'est encore démontré.

Les parcours Playwright sont écrits mais non exécutés : leur installation a été arrêtée. Ce cahier ne remplace pas un rapport de tests navigateur.

En cas d'échec, conserver trace_id et statut, vérifier le plan, les identités, les contraintes, les sources et le mode de génération. Distinguer une erreur de données, une mauvaise interprétation, une mauvaise recommandation et une indisponibilité Groq.

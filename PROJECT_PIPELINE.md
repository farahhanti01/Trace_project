# TRACE AI Platform - Project Pipeline

Derniere mise a jour : 2026-08-27

Ce fichier resume le fonctionnement actuel de TRACE AI Platform tel qu'il est
implemente dans le code. Il sert de carte rapide pour comprendre le flux entre
frontend, backend, RAG, analyse de traces, guardrails et affichage utilisateur.

## 1. Objectif du projet

TRACE AI Platform aide a analyser :

- des documents techniques : PDF, DOCX, XLSX, TXT, LOG, images ;
- des traces transactionnelles ISO 8583 ;
- des echanges HSM ;
- des captures d'ecran de traces ou de documents.

Le projet contient deux agents principaux :

- Documentation Agent : repond aux questions sur les documents ingeres avec un
  pipeline RAG, des ContentUnits, un EvidenceBundle et une memoire de
  conversation.
- Log Analysis Agent : parse les traces, extrait les transactions ISO 8583,
  reconstruit la Log Story, detecte les traitements HSM et signale les
  anomalies documentees.

Principe central :

```text
donnees utilisateur
-> controles de securite
-> parsing deterministe / extraction documentaire
-> stockage MongoDB
-> retrieval RAG si necessaire
-> EvidenceBundle si disponible
-> generation controlee
-> StructuredResponse
-> AIResponseCard
```

Le LLM ne doit pas etre la source de verite technique. Il explique et reformule
a partir des donnees extraites, des preuves documentaires et des structures
preparees par le backend.

## 2. Pipeline general

```text
Frontend React/Vite
-> POST /api/chat
-> InputSecurityGuardrail
-> classify_chat_workflow()
-> agent selectionne
   -> Documentation Agent
   -> Log Analysis Agent
-> parsing / retrieval / evidence
-> call_hps_ai()
-> OutputSecurityGuardrail
-> sauvegarde MongoDB
-> reponse API
-> AIResponseCard
```

Les fichiers principaux :

```text
backend/app/main.py
backend/app/services/document_service.py
backend/app/services/documentation_agent_service.py
backend/app/services/log_analysis_agent_service.py
backend/app/services/hps_ai_service.py
frontend/src/App.jsx
frontend/src/components/AIResponseCard.jsx
frontend/src/components/ChatInput.jsx
frontend/src/components/Sidebar.jsx
frontend/src/services/api.js
```

## 3. Upload, stockage et extraction

Le service central d'upload est :

```text
backend/app/services/document_service.py
```

Avant stockage, les fichiers passent par :

```text
FileSecurityGuardrail.validate_upload()
```

Ce controle verifie notamment :

- nom de fichier dangereux ;
- extension non supportee ;
- fichier vide ;
- taille excessive selon la policy ;
- tentative de path traversal.

Extraction documentaire :

```text
backend/app/services/extraction_service.py
```

Types geres :

- PDF ;
- DOCX ;
- XLSX ;
- TXT / LOG / TRC ;
- PNG / JPG / JPEG via OCR best-effort et/ou analyse screenshot.

Les donnees extraites sont stockees principalement dans :

```text
documents_collection
document_sections_collection
document_content_units_collection
```

Le fichier original reste stocke dans :

```text
backend/storage/documents/<conversation_or_document_id>/<file_id>.<ext>
```

Important : les documents, logs, OCR, screenshots et chunks retrouves par RAG
sont consideres comme des donnees non fiables. Une instruction trouvee dans un
document ou une trace doit etre analysee comme du contenu, jamais comme une
instruction systeme.

## 4. Security Guardrails

La couche de securite est additive et se trouve dans :

```text
backend/app/guardrails/
backend/app/guardrails/security/
```

Fichiers principaux :

```text
models.py
security/security_policy.py
security/input_security_guard.py
security/prompt_injection_guard.py
security/secret_guard.py
security/pii_guard.py
security/file_security_guard.py
security/trace_security_guard.py
security/document_security_guard.py
security/output_security_guard.py
security/llm_message_security.py
```

Statuts utilises :

- PASS : la donnee peut continuer.
- WARNING : la donnee continue, mais un risque est signale.
- REDACT : la donnee continue apres masquage.
- BLOCK : le pipeline est arrete et une reponse controlee est renvoyee.

Flux securite principal :

```text
question utilisateur
-> InputSecurityGuardrail
-> PromptInjectionGuardrail / SecretGuardrail / PIIGuardrail
-> workflow agent
-> FileSecurityGuardrail pour upload
-> TraceSecurityGuardrail pour logs/traces
-> DocumentSecurityGuardrail pour contenu documentaire extrait
-> secure_llm_messages() avant chaque appel LLM
-> OutputSecurityGuardrail avant retour utilisateur
```

Ce qui est protege aujourd'hui :

- demandes de system prompt ou instructions internes ;
- prompt injection simple ;
- private keys ;
- API keys, bearer tokens, client secrets, passwords ;
- PAN ou valeurs sensibles dans les traces ;
- donnees sensibles dans la sortie LLM ;
- propagation d'instructions trouvees dans les documents/logs vers le LLM.

Exemple important :

```text
FLD (002): [4556737586899855]
```

Le Field 002 est masque avant affichage et avant envoi au modele lorsque le
guardrail le considere sensible.

## 5. Documentation Agent

Fichier principal :

```text
backend/app/services/documentation_agent_service.py
```

Pipeline legacy encore utilise par defaut :

```text
question
-> conversation memory si activee
-> load_sections()
-> retrieve_relevant_sections()
-> focus_sections_on_exact_fields()
-> compact_sections_by_query_coverage()
-> build_context()
-> call_hps_ai()
-> parse_ai_json()
-> repair_atomic_response()
-> normalize_agent_response()
-> StructuredResponse
```

Le Documentation Agent peut aussi traiter les screenshots via :

```text
backend/app/services/screenshot_analysis_service.py
```

Dans ce cas, le pipeline distingue :

- SCREEN_EXTRACTION : extraire une valeur visible depuis l'image ;
- SCREEN_UNDERSTANDING : decrire ou expliquer ce qui est visible ;
- SCREEN_DIAGNOSIS : diagnostiquer une sequence de trace visible ;
- SCREEN_VALUE_EXPLANATION : expliquer une valeur visible ;
- DOCUMENTATION_LOOKUP : question documentaire classique.

Regle pour screenshots :

```text
screenshot = observation
documentation RAG = confirmation documentaire
LLM = explication
```

Une demande comme `extrait le champ 039` doit extraire la valeur visible dans
le screenshot ou dire qu'elle n'est pas visible. Elle ne doit pas remplacer
l'absence de valeur par une definition documentaire du Field 039.

## 6. Conversation Memory

Fichier principal :

```text
backend/app/services/conversation_memory_service.py
```

La memoire sert uniquement a resoudre le contexte conversationnel :

```text
"Que represente le Field 039 ?"
-> active_entity = Field 039

"Et le 51 ?"
-> resolved_query = "Que signifie le code 51 du Field 039 ?"
```

Responsabilites :

- conserver le sujet actif ;
- conserver les entites actives ;
- gerer les follow-ups ;
- resoudre les pronoms et references implicites ;
- ne jamais devenir une source de verite technique.

Regle d'architecture :

```text
MEMORY RESOLVES CONTEXT.
DOCUMENTATION ESTABLISHES FACTS.
EVIDENCE CONTROLS GENERATION.
```

## 7. RAG, ContentUnits et EvidenceBundle

Composants principaux :

```text
backend/app/services/retrieval_service.py
backend/app/services/content_unit_service.py
backend/app/services/generic_question_classifier_service.py
backend/app/services/adaptive_retrieval_service.py
backend/app/services/evidence_builder_service.py
backend/app/services/documentation_evidence_generation_service.py
backend/app/models/document_content.py
```

Pipeline evidence/debug :

```text
question resolue
-> GenericQuestionClassifier
-> QueryPlan
-> AdaptiveRetriever
-> ContentUnits
-> EvidenceBuilder
-> EvidenceBundle
-> RetrievalEvidenceGuardrail
-> EvidenceResponseWriter
-> OutputGuardrail
-> response candidate
```

Ce pipeline est utilise pour debug/shadow/experimentation. Il ne remplace pas
encore automatiquement le pipeline legacy de generation utilisateur.

ContentUnits possibles :

- heading ;
- paragraph ;
- rule ;
- definition ;
- field_description ;
- field_attribute ;
- field_usage ;
- valid_value ;
- code_mapping ;
- table ;
- table_row ;
- note ;
- unknown.

La logique de code_mapping est stricte : un mapping ne doit etre cree que si la
structure documentaire est explicite. En cas de doute, la donnee reste
`table_row` ou `unknown`.

## 8. Retrieval et guardrail evidence

Le retrieval classique selectionne des sections pertinentes. Le retrieval
adaptatif ajoute une couche plus structuree :

```text
intent
-> entities
-> strategies
-> metadata filters
-> table/row expansion si necessaire
```

Le `RetrievalEvidenceGuardrail` verifie que l'EvidenceBundle est suffisant avant
de laisser une generation experimentale continuer.

Exemples :

- EvidenceBundle vide : BLOCK, pas d'appel writer/LLM.
- question exhaustive sans table : BLOCK ou evidence incomplete.
- evidence suffisante et sourcee : PASS.

## 9. Log Analysis Agent

Fichiers principaux :

```text
backend/app/services/log_parser_service.py
backend/app/services/log_analysis_agent_service.py
```

Le parsing de trace est deterministe.

Le parser detecte :

- transactions par `Start DumpVisa()` ou `Start DumpCis()` ;
- MTI ;
- fields ISO 8583 ;
- longueurs declarees des fields ;
- valeurs des fields ;
- Log Story ;
- fonctions OK / ERROR / WARNING / UNKNOWN ;
- interactions HSM ;
- HsmResultCode et codes retour.

Pipeline :

```text
trace texte securisee
-> split_transactions()
-> parse_transaction_fields()
-> parse_log_story()
-> parse_hsm_analysis()
-> transaction_status()
-> enrich_transactions_with_documentation()
-> select_transactions_for_question()
-> StructuredResponse
```

Le Field 002 est masque par securite. Les espaces dans les valeurs de fields
sont conserves pour les controles de longueur. Exemple :

```text
FLD (043): (040): [APPLE.COM/BILL    CORK       IRL]
```

La longueur documentaire doit tenir compte des espaces presents dans la valeur.

## 10. Extraction ciblee depuis une trace

Le Log Analysis Agent possede maintenant une logique d'extraction ciblee pour
les questions du type :

```text
quelles sont les fields 039 trouves dans cette trace ?
je veux aussi les fields 002
extrais le champ 037
```

Fonctions concernees :

```text
requested_extraction_fields()
build_field_extraction_response()
```

Fichier :

```text
backend/app/services/log_analysis_agent_service.py
```

Dans ce cas, le backend retourne une table deterministe construite depuis les
transactions parsees :

```text
Field | Valeur | MTI | RRN | Source
```

Ce flux ne doit pas partir vers une definition documentaire du field. Si la
valeur existe dans la trace, elle est extraite. Si elle est sensible, elle est
masquee.

## 11. Log Story

La Log Story est reconstruite depuis les lignes de trace :

```text
Start FunctionName()
End FunctionName(...)
```

Le catalogue de fonctions peut venir d'un XLSX ou d'un document extrait. Il sert
a enrichir :

- nom de fonction ;
- description ;
- statut ;
- exceptions ;
- chemin source ;
- references documentaires.

Les fonctions non documentees peuvent apparaitre avec un statut `UNKNOWN` si
elles sont visibles dans la trace mais absentes du catalogue fourni.

## 12. HSM Analysis

Le parser cherche notamment :

```text
TO HSM
FROM HSM
HsmResultCode
command_XX()
WriteBalHsm
ReadBalHsm
HsmQuery
```

La sortie HSM affiche :

- message envoye ;
- thread ;
- commande ;
- reponse HSM ;
- code retour ;
- HsmResultCode ;
- description documentaire si elle existe.

Si aucun bloc HSM n'est detecte dans la trace active, la reponse doit le dire
clairement au lieu d'inventer une analyse HSM.

## 13. Reponses securite ciblees

Le Log Analysis Agent peut repondre directement a une question securite sur la
trace active, par exemple :

```text
Est-ce qu'il y a une API key ou un token dans cette trace ?
Si oui, indique le type trouve sans afficher la valeur complete.
```

Dans ce cas :

- la trace est inspectee ;
- les secrets detectes sont classes par type ;
- les valeurs completes ne sont pas affichees ;
- la reponse n'est pas remplacee par une Log Story generale.

Fonctions concernees :

```text
question_requests_security_audit()
detect_trace_security_findings()
build_trace_security_audit_response()
```

## 14. StructuredResponse

Les agents retournent une reponse structuree au frontend.

Champs courants :

- summary ;
- sections ;
- story ;
- issues ;
- recommendations ;
- references ;
- compliance ;
- confidence ;
- transactions ;
- metadata.

Le frontend affiche ces blocs dans :

```text
frontend/src/components/AIResponseCard.jsx
```

Les tables doivent etre envoyees comme des blocs structures, par exemple :

```json
{
  "type": "table",
  "columns": [
    {"key": "field", "label": "Field"},
    {"key": "value", "label": "Valeur"}
  ],
  "rows": [
    {"field": "039", "value": "51"}
  ]
}
```

Le JSON ne doit pas etre affiche comme texte brut dans l'interface.

## 15. Frontend

Fichiers principaux :

```text
frontend/src/App.jsx
frontend/src/components/Sidebar.jsx
frontend/src/components/ChatInput.jsx
frontend/src/components/ChatMessage.jsx
frontend/src/components/AIResponseCard.jsx
frontend/src/components/UploadArea.jsx
frontend/src/services/api.js
frontend/src/styles.css
```

Responsabilites :

- creer et restaurer une conversation ;
- afficher l'historique ;
- choisir Documentation Agent ou Log Analysis Agent ;
- uploader documents, traces ou screenshots ;
- afficher les pieces jointes ;
- afficher les reponses structurees ;
- proposer des actions : copier, exporter JSON, relancer ;
- afficher un apercu documentaire quand une reference est selectionnee.

La sidebar a ete modernisee en gardant le logo HPS, la police de l'interface et
la palette existante.

## 16. Apercu et ouverture PDF

L'apercu documentaire utilise :

```text
DocumentPreviewPanel
-> page-preview / view URL
-> frontend/src/services/api.js
-> backend/app/routes/admin.py
```

Pour eviter les blocages sur les gros PDF, le bouton `Ouvrir le PDF` ouvre le
PDF original inline avec l'ancre de page, au lieu de regenerer tout le PDF
highlighted via PyMuPDF.

Flux attendu :

```text
Ouvrir le PDF
-> referenceUrl()
-> getAdminDocumentViewUrl()
-> /api/admin/documents/{id}/view#page=N
-> PDF inline dans un nouvel onglet
```

Le panneau d'apercu peut continuer a utiliser une image de page quand elle est
disponible.

## 17. Calcul des statistiques Log Analysis

Les statistiques affichees en haut de l'analyse sont issues des transactions
parsees :

- NBR TRANSACTIONS : nombre de transactions detectees ;
- NBR ECHEC : transactions avec statut failed/error ;
- SANS RETOUR : transactions sans reponse attendue/detectee selon le parser ;
- ALERTE DETECTEE : warnings/anomalies classees comme alertes.

Ces valeurs doivent venir de la structure d'analyse, pas d'une interpretation
LLM.

## 18. Tests utiles

Commandes regulierement utilisees :

```bash
cd backend
.\.venv\Scripts\python.exe -m unittest backend/test_security_guardrails.py
.\.venv\Scripts\python.exe -m unittest backend/test_guardrails.py backend/test_documentation_evidence_generation.py backend/test_security_guardrails.py
.\.venv\Scripts\python.exe -m unittest backend/test_trace_file_type_contract.py backend/test_log_parser_contract.py
```

Build frontend :

```bash
cd frontend
npm install
npm run build
npm run dev
```

Tests recents valides :

- Security Guardrails : 18 tests OK.
- Guardrails + Evidence Generation : 40 tests OK.
- Trace file type + parser contracts : 17 tests OK.
- Log parser + security guardrails : 31 tests OK.
- Frontend build Vite : OK.

## 19. Lancement local

MongoDB :

```bash
docker start trace-mongodb
```

Backend :

```bash
cd backend
.\.venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Frontend :

```bash
cd frontend
npm install
npm run dev
```

Variable frontend importante :

```text
VITE_API_URL=http://127.0.0.1:8000
```

Si le backend tourne sur un autre port, cette valeur doit correspondre au port
reel.

## 20. Points restants importants

1. Brancher progressivement les guardrails sur le pipeline legacy utilisateur
   si l'objectif est de proteger toutes les reponses finales.
2. Ajouter plus de tests d'integration sur `/api/chat` avec mocks pour verifier
   qu'un BLOCK empeche vraiment le LLM.
3. Continuer a separer security guardrails, retrieval guardrails, grounding et
   business rules ISO/HSM.
4. Finaliser la completude TABLE_LOOKUP sans hardcoder Field 039.
5. Ameliorer le GroundingGuardrail plus tard pour comparer les claims LLM avec
   EvidenceBundle.
6. Eviter de versionner `__pycache__`, `node_modules`, fichiers runtime et
   documents uploades dans Git.

## 21. Regles d'architecture a retenir

```text
SECURITY GUARDRAILS prevent leaks, injections and unsafe inputs.
MEMORY identifies conversation context.
PARSERS extract facts from traces and files.
RAG retrieves documentary evidence.
EVIDENCE determines what can be said.
THE WRITER explains but must not invent.
STRUCTURED RESPONSE controls frontend rendering.
```

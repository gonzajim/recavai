# Building the Knowledge Base for Regulatory RAG in Spanish: Semantic Chunking, Legal Structure and Normative Graphs

**A controlled factorial study with article-level citation evaluation**

*Draft v0.1 — 18 September 2026 — research line 1 of the doctoral project. Sections 6–7 are pre-registered templates: no experimental results exist yet. Results are produced by `src/kb_experiment.py` once the golden set v1 (research line 3) is frozen.*

---

## Abstract

Retrieval-augmented generation (RAG) over regulatory text is only as good as the knowledge base it retrieves from, and the construction of that knowledge base — how documents are segmented, how their structure is preserved, and how they are enriched — is usually treated as an engineering detail rather than an object of study. Existing evidence on segmentation ("chunking") strategies comes almost entirely from English, general-domain or U.S. case-law corpora, and evaluates retrieval at the passage or document level. European regulatory text in Spanish has properties that this evidence does not cover: an explicit hierarchy (Title / Chapter / Article / paragraph), dense intra- and inter-document cross-references, overlapping standards with different levels of authority (binding directives, delegated regulations, and soft-law standards such as GRI or the OECD Guidelines), and temporal versioning. Above all, the unit that a legal practitioner needs to see cited is the *article*, not the passage.

We present a controlled factorial study of knowledge-base construction for regulatory RAG in Spanish. We compare five segmentation strategies (fixed-size windows, unconstrained semantic breakpoint chunking, structure-constrained semantic chunking with and without a contextual header, and small-to-big retrieval), three embedding models (a monolingual English model and two multilingual models of different dimensionality), and two query conditions (literal and paraphrased), on the same corpus and the same expert-built golden set. We introduce **citation@article**, a family of retrieval metrics that scores the article label a system would cite rather than the passage it returns, and we compare a **skeleton-anchored normative graph** — whose nodes are the pre-existing articles and disclosures of the corpus and whose edges are detected cross-references — against an emergent graph built by LLM entity extraction. Four falsifiable hypotheses are pre-registered together with their decision rules. The segmentation pipeline, the experimental harness, and the evaluation protocol are released.

The contribution is not a new chunking algorithm. It is the first controlled study of how segmentation, legal structure and enrichment interact with retrieval quality and citation precision on a Spanish EU regulatory corpus, with lexical distance as an explicit factor, and an evaluation protocol centred on what a jurist actually checks.

**Keywords:** retrieval-augmented generation; legal information retrieval; text segmentation; semantic chunking; knowledge graphs; Spanish; sustainability regulation; CSRD; CSDDD; evaluation.

---

## 1. Introduction

### 1.1 Problem

Sustainability compliance teams in the European Union work with a stack of instruments that changes every few months: the Corporate Sustainability Reporting Directive (CSRD, Directive (EU) 2022/2464), the European Sustainability Reporting Standards (Delegated Regulation (EU) 2023/2772), the Corporate Sustainability Due Diligence Directive (CSDDD, Directive (EU) 2024/1760), the 2025 "Omnibus" simplification package, and a layer of soft law — the OECD Guidelines for Multinational Enterprises, the OECD Due Diligence Guidance, the GRI Standards — that the binding instruments reference. A question such as *"which due-diligence obligations apply to a 600-employee textile company whose supplier operates in Bangladesh?"* requires locating the right articles across several of these instruments, respecting their hierarchy of authority, and citing them precisely.

Retrieval-augmented generation [Lewis et al., 2020] is the natural architecture for such assistants, and the RecavAI system that motivates this work is one of them. But the reliability of a RAG system is bounded by its knowledge base. If the corpus is segmented so that a fragment starts in Article 8 and ends in Article 9, the citation attached to that fragment is ambiguous; if the segmentation discards the heading hierarchy, the system cannot tell a definition (Article 3) from an obligation (Article 8); if the embedding model was trained on English, Spanish legal vocabulary is represented through a lossy lens. These are not hypothetical failure modes: the production index that preceded this work stored one vector per layout block with a median length of two words, and a first benchmark of it retrieved the right document for 80 % of questions but the right document *and page* for only 65 %.

### 1.2 What is and is not known

Segmentation for RAG has become an active topic. Semantic breakpoint chunking is implemented in LangChain and LlamaIndex and popularised by Kamradt's "five levels" of text splitting; proposition-level retrieval was proposed by Chen et al. [2023]; recursive hierarchical summarisation by Sarthi et al. [2024]; contextual chunk headers by Anthropic [2024] and, in embedding space, late chunking by Günther et al. [2024]; graph-augmented retrieval by Edge et al. [2024] and Guo et al. [2024]. Two recent studies question whether semantic chunking is worth its cost at all, finding that its advantage over fixed-size windows is inconsistent across datasets [Qu et al., 2024; Smith and Troynikov, 2024]. In the legal domain, Magesh et al. [2024] show that commercial legal research tools hallucinate or mis-cite in a substantial fraction of queries, Pipitone and Alami [2024] release LegalBench-RAG with span-level relevance on English contracts and privacy policies, and Louis et al. [2024] build statutory-article retrieval for Belgian law in French.

What is not known — as the systematic review in Section 3 makes explicit — is how these techniques behave, and how they interact, on European regulatory text in Spanish, evaluated at the granularity of the article. Almost no chunking study measures whether the *cited unit* is correct; almost none includes lexical distance between query and evidence as a controlled factor, although it is the most plausible explanation for the contradictory results on semantic chunking; and none compares a graph anchored on the pre-existing normative skeleton with a graph emerging from LLM extraction.

### 1.3 Research question and hypotheses

**RQ.** How do the segmentation strategy, the hierarchical structure of the normative text, and the enrichment of the knowledge base interact with retrieval quality, citation precision and answer faithfulness, on a Spanish regulatory corpus?

We pre-register four falsifiable hypotheses (Section 5.5 gives decision rules):

- **H1.1 (structure).** Semantic chunking constrained by the legal hierarchy — breakpoints never cross a heading — improves article-level citation recall over unconstrained semantic chunking and over fixed-size windows, with the largest gains on comparative and multi-hop questions.
- **H1.2 (lexical distance).** The advantage of semantic over fixed chunking is conditional on the lexical distance between question and evidence: on literal queries the strategies converge; on paraphrased queries, structure-constrained semantic chunking with a contextual header dominates. This interaction would explain the contradictory findings in the literature.
- **H1.3 (graph).** A graph anchored on the normative skeleton (nodes = pre-existing articles and disclosures, edges = detected cross-references) improves multi-hop article recall more than an emergent graph built by LLM entity extraction, at lower construction cost and variance.
- **H1.4 (language).** Replacing the monolingual English embedding model of the original deployment by a multilingual model yields a measurable improvement on the Spanish corpus, quantifying the language penalty of the authors' earlier IPMU 2026 system.

### 1.4 Contributions

1. The first controlled factorial study of knowledge-base construction strategies on a Spanish EU regulatory corpus, with lexical distance as an explicit factor (Sections 4–5).
2. **citation@article**, a metric family that scores the article label a system would cite, with strict and lenient variants, and an evaluation protocol centred on citation rather than passage recall (Section 4.5).
3. A **skeleton-anchored normative graph** construction method and a budget-matched protocol for comparing it against emergent graphs (Sections 4.4, 4.6).
4. A released segmentation pipeline (`corpus_pipeline.py`), experimental harness (`kb_experiment.py`), and reindexed corpus, with a synthetic smoke corpus for reproducibility (Section 5.7).

### 1.5 On novelty

We state plainly what this paper does not claim. Semantic breakpoint chunking, contextual headers, small-to-big retrieval and graph-augmented retrieval are existing techniques; we implement them faithfully and do not propose a new segmentation algorithm. The novelty lies in the controlled design, the domain and language, the article-level evaluation, and the skeleton-anchored graph. A reviewer who expects a new chunker will not find one here; a reviewer who wants to know whether the existing chunkers do what they are assumed to do on a Spanish regulatory corpus will.

### 1.6 Organisation

Section 2 gives background. Section 3 reports the systematic literature review and the gap it identifies. Section 4 presents the method: the structural model, the segmentation strategies, the graph, and the metric. Section 5 describes the experimental design and its pre-registration. Sections 6 and 7 are templates for results and discussion, to be completed once the golden set v1 is frozen. Section 8 discusses threats to validity; Section 9 concludes.

---

## 2. Background

### 2.1 RAG and the knowledge-base construction stage

A RAG system [Lewis et al., 2020; Gao et al., 2023] answers a query *q* by retrieving *k* units from an index of a corpus and conditioning a generator on them. The literature has concentrated on the retriever (dense, sparse, hybrid, re-ranked) and the generator (self-reflective, corrective, iterative) [Karpukhin et al., 2020; Asai et al., 2024; Yan et al., 2024]. The stage that produces the index — parsing, segmentation, enrichment, embedding — receives less attention, although Barnett et al. [2024] list it among the recurring failure points of engineered RAG systems, and the "lost in the middle" and "power of noise" results [Liu et al., 2024; Cuconasu et al., 2024] show that what is put in front of the generator matters as much as how it is ranked.

### 2.2 Segmentation strategies

We use *segmentation* and *chunking* interchangeably for the partition of a document into indexable units. The strategies in use fall into five families:

- **Fixed-size windows.** *w* tokens with overlap *o*, the default in most frameworks and the strategy of the original RecavAI deployment (400 words, 50 overlap). Cheap, structure-blind.
- **Recursive / delimiter-based.** Split on a hierarchy of separators (paragraph, sentence, word) until a size limit is met. Structure-aware only to the extent that separators reflect structure.
- **Semantic breakpoint chunking.** Embed sentences; cut where the cosine similarity between consecutive sentences falls below a percentile threshold [Kamradt, 2024; LangChain `SemanticChunker`; LlamaIndex `SemanticSplitterNodeParser`]. Variants use LLM judgement [Duarte et al., 2024], perplexity [Zhao et al., 2024] or clustering [Smith and Troynikov, 2024].
- **Propositions.** LLM-generated atomic statements indexed individually, mapped back to their source passage [Chen et al., 2023].
- **Hierarchical / multi-granular.** Parent–child ("small-to-big") indexes; recursive summary trees [Sarthi et al., 2024]; mixtures of granularity [Zhong et al., 2024].

### 2.3 Enrichment

Enrichment adds information to a unit before embedding without altering the text that will be cited: a *contextual header* summarising document and section [Anthropic, 2024]; keywords, summaries or entities generated by an LLM; or, in embedding space, encoding the whole document with a long-context model and pooling per chunk afterwards (*late chunking*) [Günther et al., 2024].

### 2.4 Graph-augmented retrieval

GraphRAG [Edge et al., 2024] extracts entities and relations with an LLM, builds a graph, detects communities and summarises them, targeting global "sensemaking" questions; LightRAG [Guo et al., 2024] simplifies this to a dual-level index. Surveys by Peng et al. [2024] and Han et al. [2025] catalogue the variants. All of them build the graph *from* the text by extraction. Legal corpora, however, come with a graph already: the hierarchy of headings and the explicit cross-references between articles. Whether extraction adds anything to that skeleton — or merely adds noise and cost — is an open question that H1.3 addresses.

### 2.5 European regulatory text in Spanish

Five properties distinguish the corpus of this study from the corpora on which the techniques above were developed:

1. **Explicit hierarchy.** Title / Chapter / Section / Article / paragraph (*apartado*) / point, with Annexes; standards use their own units (GRI "Contenido 306-2", ESRS "E1-6").
2. **Dense cross-references.** "*de conformidad con el artículo 8*", "*con arreglo a los artículos 10 y 11*", "*enumerados en el anexo I*". A single article of the CSDDD may reference five others.
3. **Overlapping instruments with different authority.** CSRD (binding) → ESRS (delegated) ↔ GRI (voluntary, interoperable by mapping) ↔ OECD (soft law, referenced by CSDDD).
4. **Temporal versioning.** Thresholds and dates changed with the Omnibus package; the same article label denotes different content at different dates.
5. **Language.** Spanish official translations with a legal register (*velarán por que*, *sin perjuicio de*) that English-trained embedding models represent poorly; the original deployment used `all-MiniLM-L6-v2`, an English model.

The unit a practitioner cites is the article (or disclosure). This fixes the granularity of our evaluation.

---

## 3. Systematic literature review

### 3.1 Protocol

We follow Kitchenham and Charters' guidelines for systematic reviews in software engineering and the PRISMA 2020 reporting items [Page et al., 2021], adapted to a mapping study.

**Review questions.**
- **RQ-A.** Which segmentation strategies have been proposed or evaluated for RAG, and under which evaluation regimes (metrics, unit of relevance judgement)?
- **RQ-B.** How is document structure (hierarchy, headings, cross-references) exploited in knowledge-base construction for retrieval?
- **RQ-C.** Which studies address legal or regulatory corpora, in which languages, and what is their unit of citation or relevance?
- **RQ-D.** What evidence exists on the interaction between query–evidence lexical distance and segmentation strategy?

**Sources.** ACL Anthology; ACM Digital Library; IEEE Xplore; Scopus; arXiv (cs.CL, cs.IR, cs.AI); the proceedings of ICAIL and JURIX and the journal *Artificial Intelligence and Law*; forward and backward snowballing from included studies. Grey literature (framework documentation, vendor technical reports) is included when it is the primary source of a widely used technique and is flagged as such.

**Search strings** (adapted per source; full strings in Appendix A).
- S1: ("retrieval-augmented generation" OR "RAG") AND (chunking OR segmentation OR "text splitting" OR granularity OR proposition)
- S2: (legal OR regulatory OR statute OR statutory OR compliance) AND ("retrieval-augmented" OR RAG OR "dense retrieval")
- S3: (Spanish OR multilingual OR "cross-lingual") AND legal AND (retrieval OR "language model")
- S4: (graph OR "knowledge graph") AND ("retrieval-augmented" OR RAG)

**Period.** January 2020 – September 2026.

**Inclusion criteria.** (I1) Proposes or empirically evaluates a knowledge-base construction technique (segmentation, enrichment, graph construction) for retrieval or RAG; or (I2) evaluates retrieval or RAG on a legal/regulatory corpus; or (I3) provides a benchmark, dataset or metric used to evaluate (I1)/(I2). Peer-reviewed venues or preprints with an available implementation or dataset.

**Exclusion criteria.** (E1) No empirical evaluation. (E2) LLM prompting without a retrieval component. (E3) Not available in full text. (E4) Duplicate or superseded version. (E5) Language other than English or Spanish.

**Screening.** Title/abstract screening by one reviewer, full-text screening by two reviewers with disagreement resolved by discussion; Cohen's κ on a 20 % sample reported.

**Data extraction form.** Study; year; venue; technique family (§2.2–2.4); structure-aware (yes/no/partial); language(s); domain; corpus; unit of relevance judgement (document / passage / span / article); metrics; lexical-distance analysis (yes/no); code available.

**Quality assessment.** Four yes/partial/no items: clear baseline; statistical testing; reproducible artefacts; evaluation unit matches the stated use case.

### 3.2 Search results

> **[To be completed after executing the searches.]** Records identified: ACL [n], ACM [n], IEEE [n], Scopus [n], arXiv [n], ICAIL/JURIX/AI&Law [n], snowballing [n]. After de-duplication: [n]. Excluded at title/abstract: [n]. Full-text assessed: [n]. Excluded with reasons (E1 [n], E2 [n], E3 [n], E4 [n], E5 [n]). Included: [n]. Screening agreement κ = [·]. PRISMA flow diagram in Appendix A.

The synthesis below is based on the seed set of primary studies known to the authors at the time of writing (Table 1); it will be revised once the search is executed. The seed set is not a substitute for the search, and the paper must not be submitted with the placeholders above unfilled.

### 3.3 Synthesis

**(a) Granularity and segmentation (RQ-A).** Dense retrieval inherited the 100-word passage from DPR [Karpukhin et al., 2020] and the RAG paper [Lewis et al., 2020]. Chen et al. [2023] showed that proposition-level units improve retrieval on Wikipedia QA, at the cost of an LLM pass over the corpus. Semantic breakpoint chunking entered practice through framework implementations rather than a paper; its first controlled evaluations are recent and cautionary. Qu et al. [2024] compare semantic chunkers against fixed-size on document retrieval, evidence retrieval and answer generation and find that the gains are inconsistent and sometimes negative, concluding that "the computational cost is not justified" for many datasets. Smith and Troynikov [2024] propose token-level recall and precision to evaluate chunkers and find that the best strategies differ by corpus. LumberChunker [Duarte et al., 2024] and Meta-Chunking [Zhao et al., 2024] replace embedding similarity by LLM judgement or perplexity, again on English narrative or QA corpora. Finardi et al. [2024] is one of the few non-English studies, on Brazilian Portuguese, and reports that chunk size and retriever interact. None of these studies reports the lexical distance between queries and gold passages, and all evaluate at passage or document level.

**(b) Structure-aware construction (RQ-B).** Yepes et al. [2024] is the closest precedent: element-based chunking of financial reports (titles, tables, narrative) using a layout parser outperforms fixed-size windows on retrieval and generation. RAPTOR [Sarthi et al., 2024] imposes a hierarchy by recursive summarisation rather than by using the document's own. Mix-of-Granularity [Zhong et al., 2024] learns to route between granularities. Late chunking [Günther et al., 2024] preserves document context in the embedding rather than in the text. Document parsers such as Docling [Auer et al., 2024] recover headings and tables but leave the segmentation policy to the user. The explicit legal hierarchy — where the boundary between Article 8 and Article 9 is a semantic boundary by construction — has not been used as a constraint on semantic breakpoints in any study we know of.

**(c) Contextual enrichment.** Anthropic [2024] reports large reductions in retrieval failure rate from prepending an LLM-generated chunk context, on internal benchmarks; the effect has not been isolated from re-ranking in a controlled setting, nor tested in Spanish, nor related to query lexical distance — although the mechanism (bringing document- and section-level vocabulary into the chunk embedding) is exactly what should help paraphrased queries and not literal ones, which is H1.2.

**(d) Graph-augmented retrieval.** GraphRAG [Edge et al., 2024] and LightRAG [Guo et al., 2024] construct graphs by LLM extraction; the surveys by Peng et al. [2024] and Han et al. [2025] show that every catalogued variant does the same. Extraction is expensive (one LLM call per unit), non-deterministic (the graph differs between runs), and blind to the graph the documents already declare. MultiHop-RAG [Tang and Yang, 2024] provides multi-hop evaluation on news, not law.

**(e) Legal RAG and its evaluation (RQ-C).** Magesh et al. [2024] evaluate Lexis+ AI, Westlaw AI-Assisted Research and GPT-4 on U.S. legal research queries and find hallucination or mis-citation in 17–33 % of responses despite retrieval; Dahl et al. [2024] profile legal hallucinations without retrieval. LegalBench [Guha et al., 2023] measures legal reasoning without retrieval; LegalBench-RAG [Pipitone and Alami, 2024] adds a retrieval benchmark with character-span relevance on four English corpora (contracts, privacy policies, M&A agreements), and observes that chunking strategy affects results but does not study it factorially. Louis et al. [2024] build LLeQA, long-form QA over Belgian statutes in French, with retrieval at the level of the statutory article — the only precedent we know of for article-level relevance, but with the article taken as the given unit rather than as the target of a segmentation comparison. HyPA-RAG [Kalra et al., 2024] applies hybrid and adaptive retrieval to a New York local law. Earlier surveys of legal IR [Sansone and Sperlì, 2022; Locke and Zuccon, 2022] predate RAG. COLIEE provides case-law and statute retrieval tasks in English and Japanese.

**(f) Multilingual and Spanish legal NLP.** MultiEURLEX [Chalkidis et al., 2021], LEXTREME [Niklaus et al., 2023] and MultiLegalPile [Niklaus et al., 2024] cover EU law in Spanish for classification and pre-training, not retrieval. Gutiérrez-Fandiño et al. [2021] release Spanish legalese language models. On the embedding side, multilingual sentence encoders [Reimers and Gurevych, 2020], multilingual E5 [Wang et al., 2024] and BGE-M3 [Chen et al., 2024] exist and are benchmarked on MTEB/MMTEB [Muennighoff et al., 2023; Enevoldsen et al., 2025], but no study quantifies the penalty of using an English monolingual encoder on a Spanish legal corpus in a RAG setting.

**(g) Sustainability-domain NLP.** ChatReport [Ni et al., 2023], ChatClimate [Vaghefi et al., 2023], ClimRetrieve [Schimanski et al., 2024] and Bronzini et al. [2024] apply LLMs and retrieval to sustainability *reports* (company disclosures), mostly in English; none targets the regulatory corpus itself.

**(h) Evaluation methodology.** RAGAS [Es et al., 2024] and ARES [Saad-Falcon et al., 2024] provide LLM-judged faithfulness and relevance; Zheng et al. [2023] establish LLM-as-judge with human agreement checks. Pooling [Buckley and Voorhees, 2004], inter-annotator agreement [Artstein and Poesio, 2008] and paired significance testing in IR [Smucker et al., 2007] provide the protocol we adopt in Section 5.

### 3.4 Extraction table (seed set)

**Table 1.** Seed primary studies. *Unit* = unit of relevance judgement. *Struct.* = uses document structure. *Lex.* = analyses query–evidence lexical distance. Rows to be completed from the executed search.

| Study | Technique family | Struct. | Language | Domain | Unit | Lex. |
|---|---|---|---|---|---|---|
| Lewis et al. 2020 | fixed 100-word | no | EN | Wikipedia | passage | no |
| Karpukhin et al. 2020 | fixed 100-word | no | EN | Wikipedia | passage | no |
| Chen et al. 2023 (Dense X) | propositions | no | EN | Wikipedia | passage | no |
| Sarthi et al. 2024 (RAPTOR) | hierarchical summaries | imposed | EN | narrative/QA | passage | no |
| Anthropic 2024 | contextual header | no | EN | internal | chunk | no |
| Günther et al. 2024 | late chunking | no | EN (+ML) | BEIR | passage | no |
| Edge et al. 2024 (GraphRAG) | emergent graph | no | EN | news/podcasts | — (global) | no |
| Guo et al. 2024 (LightRAG) | emergent graph | no | EN | UltraDomain | — | no |
| Qu et al. 2024 | semantic vs fixed | no | EN | QA | passage/doc | no |
| Smith & Troynikov 2024 | chunker evaluation | no | EN | mixed | token span | no |
| Duarte et al. 2024 (LumberChunker) | LLM segmentation | no | EN | narrative | passage | no |
| Zhao et al. 2024 (Meta-Chunking) | perplexity segmentation | no | EN/ZH | QA | passage | no |
| Zhong et al. 2024 (MoG) | multi-granular | partial | EN | QA | passage | no |
| Yepes et al. 2024 | element-based | **yes** | EN | financial reports | passage | no |
| Finardi et al. 2024 | chunk size × retriever | no | PT | mixed | passage | no |
| Tang & Yang 2024 (MultiHop-RAG) | benchmark | no | EN | news | passage | no |
| Magesh et al. 2024 | evaluation of legal tools | — | EN | U.S. law | citation | no |
| Pipitone & Alami 2024 (LegalBench-RAG) | benchmark | no | EN | contracts/privacy | char span | no |
| Louis et al. 2024 (LLeQA) | article retrieval | **yes (given)** | FR | Belgian statutes | **article** | no |
| Kalra et al. 2024 (HyPA-RAG) | hybrid/adaptive | partial | EN | NYC LL144 | passage | no |
| Chalkidis et al. 2021 (MultiEURLEX) | classification | no | 23 EU langs | EU law | document | — |
| Niklaus et al. 2023/2024 | benchmark / pre-training | no | 24 EU langs | EU law | document | — |
| Gutiérrez-Fandiño et al. 2021 | legal LMs | no | ES | Spanish law | — | — |
| Ni et al. 2023; Vaghefi et al. 2023; Schimanski et al. 2024 | sustainability RAG | no | EN | company reports | passage | no |
| **This study** | **factorial: 5 seg. × 3 emb. × 2 lex. × graph** | **yes (constraint)** | **ES** | **EU sustainability regulation** | **article** | **yes** |

### 3.5 Gap

Table 1 makes the gap concrete along four dimensions that no prior study covers jointly: (i) segmentation strategies compared factorially rather than one-against-baseline; (ii) the legal hierarchy used as a *constraint* on semantic breakpoints, not merely as metadata; (iii) relevance judged at the article, the unit of legal citation, on a Spanish corpus; (iv) lexical distance controlled as a factor, which is the natural candidate explanation for the inconsistent findings of Qu et al. [2024] and Smith and Troynikov [2024]. A fifth gap is the comparison of skeleton-anchored and emergent graphs (H1.3), which no GraphRAG variant addresses because none starts from a corpus that declares its own graph.

---

## 4. Method

### 4.1 Structural model of a regulatory document

Let a document *d* be a character sequence with a page map π: offset → page. A **heading detector** identifies a sequence of headings *h*₁…*h*ₘ, each with a level ℓ(*h*) ∈ {1..8} and an offset. Levels follow the Spanish legislative grammar and the units of standards:

| Level | Pattern (regex, case-insensitive) | Example |
|---|---|---|
| 1 | `^(TÍTULO\|ANEXO) (ROMAN\|\d+)` | TÍTULO II, ANEXO I |
| 2 | `^(CAPÍTULO\|MÓDULO) …` | CAPÍTULO III |
| 3 | `^SECCIÓN …` or all-caps line | SECCIÓN 2 |
| 4 | `^(Artículo\|Art\.) \d+( bis\| ter)?` | Artículo 8 bis |
| 4 | `^(Contenido\|Disclosure) \d{3}-\d{1,2}` | Contenido 306-2 (GRI) |
| 4 | `^[EGS]\d?-\d{1,2}\b` | E1-6 (ESRS) |
| 5–8 | `^\d+(\.\d+){0,3}\.? [A-Z…]` | 2.1.3 Alcance |

The **section path** σ(*x*) of an offset *x* is the stack of headings in force at *x*, one per level, joined as "TÍTULO I · CAPÍTULO II · Artículo 8". The **article** α(*x*) is the deepest level-4 heading in σ(*x*), normalised to an identifier: `art.8`, `art.8.bis`, `anexo.I`, `gri.306-2`, `esrs.E1-6`. Paragraph numbers ("8.3") are deliberately dropped: the article is the unit of citation, and paragraph-level gold would demand an annotation effort the golden set does not fund in v1.

Tables (runs of `|`-prefixed lines produced by the PDF parser) and figure captions are **atomic blocks**: they are never split and never merged with surrounding prose.

### 4.2 Segmentation strategies

Let *S* = ⟨*s*₁…*s*ₙ⟩ be the sentence sequence of a text block, produced by a Spanish-aware splitter that protects abbreviations (*art.*, *apdo.*, *núm.*), decimals and enumerations. Let *e*(·) be the embedding function of the current model. A segmentation is a partition of *S* into contiguous groups.

**Fixed (F).** Windows of *w* = 400 whitespace tokens with overlap *o* = 50 over the whole document, ignoring block and heading boundaries. This reproduces the original deployment.

**Semantic, unconstrained (S-free).** Sentences over the whole document, headings included as sentences. Consecutive similarities *c*ᵢ = cos(*e*(*s*ᵢ), *e*(*s*ᵢ₊₁)); a breakpoint is placed before *s*ᵢ₊₁ whenever *c*ᵢ < τ, where τ is the (100 − *p*)-th percentile of {*c*ᵢ} with *p* = 80 (i.e. the lowest 20 % of transitions). Groups are then adjusted to [350, 1800] characters: oversized groups are split at sentence boundaries, undersized ones merged with a neighbour. No overlap.

**Semantic, structure-constrained (S-struct).** Identical breakpoint rule, but computed *within* each text block delimited by headings, tables and figures. A group therefore never crosses a heading, and α is constant within a group. This is the strategy implemented in `corpus_pipeline.chunk_document`.

**S-struct with contextual header (S-struct+ctx).** Same units as S-struct; the text that is *embedded* is `[title · type year issuer · σ(x)] ⊕ content`, while the text that is *stored and cited* remains the literal content. The header is deterministic (built from metadata), not LLM-generated, so its cost is zero and its effect is separable from any LLM enrichment.

**Small-to-big (S2B).** Children are windows of *m* = 2 sentences within each S-struct+ctx parent; children are indexed, parents are returned. This tests whether finer matching units help without sacrificing the citation unit.

**Propositions (P).** LLM-generated atomic statements per S-struct parent [Chen et al., 2023], indexed as children of the parent. Requires one LLM call per parent and is therefore run as an optional arm with its cost recorded.

### 4.3 Contextual header and enrichment

The header carries three kinds of vocabulary into the embedding that the chunk text itself may lack: the instrument's name and type (*Directiva 2024 UE*), the issuer, and the section path (*CAPÍTULO II · Artículo 8 · Determinación y evaluación de los efectos adversos*). A paraphrased query ("*si detecto un riesgo de daño que todavía no ha ocurrido, ¿qué tengo que hacer?*") shares few tokens with the article's text but may share semantics with its heading. This is the mechanism behind H1.2. Optional LLM enrichment (summary, keywords, entities, triplets with PageRank importance) is implemented in `corpus_pipeline.enrich_chunks` and is used only for the emergent graph arm, so that the contextual-header effect is measured without LLM involvement.

### 4.4 Normative graph: skeleton-anchored vs emergent

**Skeleton graph G_S.** Nodes are the pairs (document, α) for every article-level heading detected in §4.1. A directed edge (*u* → *v*) is added when the text of any unit under article *u* contains a cross-reference resolving to *v* in the same document, detected by the grammar `art(ículo|s|.) N [(y|,|a|al|hasta) M]` and `anexo R`. Units are attached to their article node. Construction is deterministic, costs no LLM calls, and takes milliseconds.

**Emergent graph G_E.** Nodes are units; an edge joins two units that share an LLM-extracted entity, excluding entities that occur in more than 25 units (stop-entities). Construction costs one LLM call per unit and is non-deterministic across runs; we record its variance by building it twice.

**Expansion.** Given the seed units retrieved for a query, both graphs expand to their 1-hop neighbours, rank the neighbours by dense similarity to the query, and admit at most *e* = 3 of them.

### 4.5 citation@article

Let *G_q* be the gold set of (document, article) pairs for query *q* and *R* = ⟨*r*₁…*r*ₖ⟩ the units returned. Let doc(*r*) ≈ doc(*g*) denote the document matcher of the existing harness (normalised substring or token-Jaccard ≥ 0.6). Define the **cited label** of a unit as (doc(*r*), α(start(*r*))) — the article in which the unit *starts*, which is what a system would print — and the **spanned set** as {(doc(*r*), *a*) : *a* an article heading intersecting span(*r*)}.

- **CitRecall@k** = |{*g* ∈ *G_q* : ∃ *r* ∈ *R*, cited(*r*) = *g*}| / |*G_q*|
- **CitPrecision@k** = |{*r* ∈ *R* : cited(*r*) ∈ *G_q*}| / |{*r* ∈ *R* : α(*r*) ≠ ∅}|
- **CitRecall-lenient@k** = as CitRecall with spanned(*r*) in place of cited(*r*)
- **CitAll@k** = 𝟙[CitRecall@k = 1], the all-or-nothing score for multi-article questions

The strict/lenient pair is diagnostic: a unit that starts in Article 8 and ends in Article 9 counts for Article 9 under the lenient variant but not under the strict one. The gap between the two, aggregated over a strategy, measures how much of its recall depends on ambiguous citations. We report both and pre-register the strict variant as primary.

Document- and page-level metrics (hit@k, recall@k, precision@k, MRR with ±1 page tolerance) are retained from the earlier harness for comparability with the deployment baseline.

### 4.6 Budget-matched retrieval

All conditions return exactly *k* units. Without a graph, the *k* best of the dense (or hybrid RRF) ranking, collapsed to parents where applicable. With a graph, the *k* − *e* best serve as seeds, up to *e* neighbours are admitted, and the list is completed from the dense ranking if the graph contributes fewer than *e*. This prevents the graph arms from being credited for simply returning more units — a confound present in several GraphRAG evaluations.

---

## 5. Experimental design

### 5.1 Corpus

The RecavAI regulatory corpus: approximately 48 documents in Spanish (CSRD, CSDDD, ESRS, GRI 11 and the GRI universal standards, the OECD Guidelines and Due Diligence Guidance, a theoretical framework and a glossary), 9,176 layout blocks in the legacy index. Documents are parsed with `pdfplumber` (tables to Markdown) and `python-docx`. The corpus is fixed across all conditions. Licensing of GRI and OECD texts is reviewed before release; where redistribution is not permitted, the released artefact contains offsets and hashes rather than text (Appendix D).

### 5.2 Golden set

The golden set is produced by research line 3 under the protocol "Metodología del Golden Set" and the eight-week plan agreed with IDPEI: double independent annotation by legal experts, blind to any system output, pooled relevance judgements for retrieval, adjudication, and inter-annotator agreement reported per task (Cohen's κ for categorical labels; target ≥ 0.70). This study consumes two of its tasks:

- **T1 (retrieval and citation):** ~50 questions, each with a reference answer and gold sources at document, page and **article** level; coverage quotas over role, instrument, intent, evidence scope and difficulty.
- **T9 (lexical sensitivity):** for every T1 question, a paraphrase written without the instrument's vocabulary, with an automated check that token overlap with the gold passage stays below a threshold.

The `parafrasis` field of each item is the *paraphrased* level of the query factor; `pregunta` is the *literal* level. Items whose gold sources carry no article label are excluded from the citation metrics and reported separately.

### 5.3 Factors and levels

| Factor | Levels | Notes |
|---|---|---|
| Segmentation | F, S-free, S-struct, S-struct+ctx, S2B, (P) | P optional, LLM cost recorded |
| Embedding | `all-MiniLM-L6-v2` (EN, 384d); `paraphrase-multilingual-MiniLM-L12-v2` (ML, 384d); `paraphrase-multilingual-mpnet-base-v2` (ML, 768d) | first = original deployment; multilingual E5/BGE-M3 as an extension |
| Query | literal, paraphrased | within-item, paired |
| Graph | none, skeleton, (emergent) | emergent optional, LLM cost recorded |
| Retrieval channel | dense; dense + BM25 (RRF) as robustness check | k = 6 (deployment value); k = 12 as sensitivity |

The design is fully crossed for the non-optional levels: 5 × 3 × 2 × 2 = 60 conditions × ~50 items = ~3,000 paired observations per metric.

### 5.4 Metrics

Primary: **CitRecall@6** (strict). Secondary: CitPrecision@6, CitRecall-lenient@6, CitAll@6, recall@6, precision@6, MRR, hit@6. End-to-end (optional arm, k = 6, Gemini 2.5 Flash as generator): groundedness, correctness and citation_ok by LLM judge, with a 50-item human sample from task T8 to report judge–human Spearman correlation. Cost: units, mean/median characters, units crossing an article boundary, build seconds, embedding seconds, LLM calls, index size.

### 5.5 Statistical analysis and pre-registration

All contrasts are **paired by item**. For each contrast we report the mean difference, a 95 % percentile bootstrap confidence interval (2,000 resamples), and the two-sided Wilcoxon signed-rank *p*-value, with Holm correction across the family of pre-registered contrasts. The interaction in H1.2 is tested as the difference of differences, (S-struct+ctx − F)_paraphrased − (S-struct+ctx − F)_literal, with its own bootstrap CI.

**Decision rules.** A hypothesis is *supported* when the CI of its primary contrast excludes zero in the predicted direction and the Holm-adjusted *p* < 0.05; *not supported* when the CI includes zero; *contradicted* when the CI excludes zero in the opposite direction. H1.2 additionally requires the interaction CI to exclude zero. H1.3 is evaluated on the multi-source subset as primary and on all items as secondary.

| Hypothesis | Primary contrast | Metric | Subset |
|---|---|---|---|
| H1.1 | S-struct − F; S-struct − S-free | CitRecall@6 | all; comparativa + multi_salto |
| H1.2 | interaction (S-struct+ctx − F) × query | recall@6, CitRecall@6 | all |
| H1.3 | skeleton − none; skeleton − emergent | CitRecall@6 | multi-source |
| H1.4 | ML-384d − EN-384d (same dimensionality) | recall@6, CitRecall@6 | all, per strategy |

The hypotheses, contrasts, metrics and decision rules are fixed in this document and in the code before any result is seen. Exploratory analyses will be labelled as such.

### 5.6 Secondary variables

Construction cost (seconds, LLM calls, USD at list price), index size (units × dimension), and — for the emergent graph — variance between two builds (Jaccard of edge sets).

### 5.7 Implementation and reproducibility

Segmentation is implemented in `src/corpus_pipeline.py` (heading grammar, Spanish sentence splitter, breakpoints, size adjustment, contextual header, enrichment, propositions). The factorial harness is `src/kb_experiment.py`: it builds every (embedding × segmentation) index in memory, runs every (graph × query) condition, writes one row per item and condition, and produces the report with the pre-registered contrasts. A synthetic two-document smoke corpus with eight annotated questions (`benchmarks/smoke/`) exercises every code path in under a minute and is used as an **instrument check**: on it, the strict citation metric separates F from S-struct as the structural model predicts (F units cross article boundaries; S-struct units do not), and the skeleton graph admits cross-referenced articles into the top-*k* for multi-hop items. These smoke numbers are not results and are not reported as such.

---

## 6. Results

> **[Template — to be generated by `kb_experiment.py --out results/v1` after golden set v1 is frozen. No numbers below are real.]**

### 6.1 Construction cost (Table 2)

| Embedding | Segmentation | Units | Mean chars | Units crossing an article | Build s | Embed s | LLM calls |
|---|---|---:|---:|---:|---:|---:|---:|
| EN-384d | F | · | · | · | · | · | 0 |
| … | … | | | | | | |

### 6.2 H1.1 — structure and citation recall (Table 3, Figure 1)

CitRecall@6 by segmentation × embedding, literal queries, with the strict/lenient gap; contrasts S-struct − F and S-struct − S-free with CI and Holm *p*; breakdown by question type (conceptual / operational / resource / comparativa / multi_salto).

### 6.3 H1.2 — lexical distance interaction (Table 4, Figure 2)

recall@6 and CitRecall@6 by segmentation × query; interaction estimate with CI; per-embedding.

### 6.4 H1.3 — skeleton vs emergent graph (Table 5)

CitRecall@6 on the multi-source subset by graph mode × segmentation; edge counts; construction cost; emergent-graph variance.

### 6.5 H1.4 — language penalty (Table 6)

recall@6 and CitRecall@6 by embedding × segmentation; ML-384d − EN-384d contrast; ML-768d as dimensionality control.

### 6.6 End-to-end (optional, Table 7)

Groundedness, correctness, citation_ok by judge; judge–human correlation on the T8 sample.

---

## 7. Discussion

> **[To be written after results. The interpretive frames below are fixed in advance so that the discussion cannot be fitted post hoc.]**

**If H1.1 is supported**, the strict/lenient gap tells whether the gain comes from *better retrieval* (lenient also rises) or from *unambiguous citation* (only strict rises). The second is the more useful finding for practice: it means fixed-size chunking retrieves the right text but cites it wrongly. **If H1.1 is not supported**, the likely reasons are (i) the golden set's articles are long enough that fixed windows rarely cross them — testable from the "units crossing an article" column — or (ii) the questions are mostly single-article, where structure cannot help; the per-type breakdown separates these.

**If H1.2 is supported**, the inconsistency in the chunking literature has a candidate explanation: benchmarks differ in the lexical distance of their queries, and semantic chunking with a header only pays off when that distance is high. This is the result with the widest relevance beyond law. **If not**, either the header does not carry the vocabulary that paraphrases use (inspect the failing items), or the multilingual models already close the gap without it (compare per embedding).

**If H1.3 is supported**, the recommendation is direct: for corpora that declare their structure, build the graph from the structure and spend the LLM budget elsewhere. **If the emergent graph wins**, the extracted entities capture relations that cross-references do not (e.g. the same obligation phrased in CSDDD and OECD); that is worth knowing and points to a hybrid graph. **If neither helps**, multi-hop questions may be answerable from single units in this corpus, and the graph should be re-evaluated on task T5 (framework comparison) where hops are guaranteed.

**H1.4** is expected to be supported; the interesting quantity is the size of the penalty, which bounds how much of the IPMU 2026 system's reported performance was lost to the embedding model rather than to the aggregation operators studied there.

---

## 8. Threats to validity

**Construct.** The article is our unit of citation, but some questions are answered at paragraph level; strict CitRecall may over-credit a unit that cites the right article and the wrong paragraph. Paragraph-level gold is planned for v2 of the golden set. The heading grammar may miss unconventional headings (mitigated by the all-caps and numbered fallbacks and by reporting the number of units with α = ∅).

**Internal.** Semantic breakpoints depend on the embedding model, so segmentation and embedding are not independent factors; we report the number of units per (segmentation × embedding) cell and treat embedding as a blocking factor in the contrasts. Percentile-based thresholds adapt to each document, which is intended, but means that a document with uniformly high sentence similarity is still cut at its 20 % lowest transitions. Budget matching (§4.6) removes the "more units" confound from the graph arms.

**External.** One corpus, one language, one domain. The sustainability corpus mixes binding and soft-law instruments, which is typical of compliance domains but not of case law. The golden set is built by one institution's experts; κ is reported but does not remove institutional bias.

**Statistical.** ~50 items give limited power for small effects; we report CIs rather than only *p*-values, and pre-register the contrasts to avoid the garden of forking paths. Holm correction is conservative across a large family; we report both raw and adjusted values.

**Reproducibility.** Embedding models are pinned by name and revision; the LLM arms (P, emergent graph, judge) are non-deterministic and are run twice with variance reported. GRI and OECD texts may not be redistributable; the release includes hashes and offsets so that a reader who holds the documents can reproduce the index exactly.

---

## 9. Conclusion

Knowledge-base construction is where a regulatory RAG system decides what it will be able to cite. This paper turns that stage into an object of controlled study on a Spanish EU sustainability corpus, evaluated at the unit that matters to a jurist. It pre-registers four hypotheses about structure, lexical distance, graphs and language, releases the instruments to test them, and commits to reporting the results whichever way they fall. The segmentation techniques are not new; the evidence about them on this kind of text will be.

---

## References

*Verification note: entries were written from the authors' knowledge and must be checked against DOI/arXiv records before submission; identifiers are given where the authors are confident of them.*

- Anthropic (2024). *Introducing Contextual Retrieval*. Technical blog post, September 2024.
- Artstein, R., Poesio, M. (2008). Inter-coder agreement for computational linguistics. *Computational Linguistics*, 34(4), 555–596.
- Asai, A., Wu, Z., Wang, Y., Sil, A., Hajishirzi, H. (2024). Self-RAG: Learning to retrieve, generate, and critique through self-reflection. *ICLR 2024*.
- Auer, C. et al. (2024). Docling technical report. arXiv:2408.09869.
- Barnett, S., Kurniawan, S., Thudumu, S., Brannelly, Z., Abdelrazek, M. (2024). Seven failure points when engineering a retrieval augmented generation system. *CAIN 2024*. arXiv:2401.05856.
- Bronzini, M., Nicolini, C., Lepri, B., Passerini, A., Staiano, J. (2024). Glitter or gold? Deriving structured insights from sustainability reports via large language models. *EPJ Data Science*, 13.
- Buckley, C., Voorhees, E. M. (2004). Retrieval evaluation with incomplete information. *SIGIR 2004*.
- Chalkidis, I., Fergadiotis, M., Androutsopoulos, I. (2021). MultiEURLEX — A multi-lingual and multi-label legal document classification dataset for zero-shot cross-lingual transfer. *EMNLP 2021*.
- Chen, J., Xiao, S., Zhang, P., Luo, K., Lian, D., Liu, Z. (2024). BGE M3-Embedding: Multi-lingual, multi-functionality, multi-granularity text embeddings through self-knowledge distillation. arXiv:2402.03216.
- Chen, T., Wang, H., Chen, S., Yu, W., Ma, K., Zhao, X., Zhang, H., Yu, D. (2023). Dense X Retrieval: What retrieval granularity should we use? arXiv:2312.06648 (NAACL 2024).
- Cormack, G. V., Clarke, C. L. A., Buettcher, S. (2009). Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. *SIGIR 2009*.
- Cuconasu, F. et al. (2024). The power of noise: Redefining retrieval for RAG systems. *SIGIR 2024*.
- Dahl, M., Magesh, V., Suzgun, M., Ho, D. E. (2024). Large legal fictions: Profiling legal hallucinations in large language models. *Journal of Legal Analysis*, 16(1).
- Duarte, A. V., Marques, J., Graça, M., Freire, M., Li, L., Oliveira, A. L. (2024). LumberChunker: Long-form narrative document segmentation. *Findings of EMNLP 2024*. arXiv:2406.17526.
- Edge, D. et al. (2024). From local to global: A Graph RAG approach to query-focused summarization. arXiv:2404.16130.
- Enevoldsen, K. et al. (2025). MMTEB: Massive multilingual text embedding benchmark. arXiv:2502.13595.
- Es, S., James, J., Espinosa-Anke, L., Schockaert, S. (2024). RAGAS: Automated evaluation of retrieval augmented generation. *EACL 2024 (demo)*.
- Finardi, P. et al. (2024). The chronicles of RAG: The retriever, the chunk and the generator. arXiv:2401.07883.
- Gao, Y. et al. (2023). Retrieval-augmented generation for large language models: A survey. arXiv:2312.10997.
- Guha, N. et al. (2023). LegalBench: A collaboratively built benchmark for measuring legal reasoning in large language models. *NeurIPS 2023 Datasets and Benchmarks*.
- Günther, M., Mohr, I., Williams, D. J., Wang, B., Xiao, H. (2024). Late chunking: Contextual chunk embeddings using long-context embedding models. arXiv:2409.04701.
- Guo, Z., Xia, L., Yu, Y., Ao, T., Huang, C. (2024). LightRAG: Simple and fast retrieval-augmented generation. arXiv:2410.05779.
- Gutiérrez-Fandiño, A., Armengol-Estapé, J., Gonzalez-Agirre, A., Villegas, M. (2021). Spanish legalese language models and corpora. arXiv:2110.12201.
- Han, H. et al. (2025). Retrieval-augmented generation with graphs (GraphRAG). arXiv:2501.00309.
- Kalra, R. et al. (2024). HyPA-RAG: A hybrid parameter adaptive retrieval-augmented generation system for AI legal and policy applications. arXiv:2409.09046.
- Kamradt, G. (2024). *5 levels of text splitting*. Public notebook.
- Karpukhin, V. et al. (2020). Dense passage retrieval for open-domain question answering. *EMNLP 2020*.
- Kitchenham, B., Charters, S. (2007). Guidelines for performing systematic literature reviews in software engineering. EBSE Technical Report.
- Lewis, P. et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.
- Liu, N. F. et al. (2024). Lost in the middle: How language models use long contexts. *TACL*, 12.
- Locke, D., Zuccon, G. (2022). Case law retrieval: problems, methods, challenges and evaluations in the last 20 years. arXiv:2202.07209.
- Louis, A., van Dijck, G., Spanakis, G. (2024). Interpretable long-form legal question answering with retrieval-augmented large language models. *AAAI 2024*. arXiv:2309.17050.
- Magesh, V., Surani, F., Dahl, M., Suzgun, M., Manning, C. D., Ho, D. E. (2024). Hallucination-free? Assessing the reliability of leading AI legal research tools. arXiv:2405.20362 (*Journal of Empirical Legal Studies*, 2025).
- Muennighoff, N., Tazi, N., Magne, L., Reimers, N. (2023). MTEB: Massive text embedding benchmark. *EACL 2023*.
- Ni, J. et al. (2023). CHATREPORT: Democratizing sustainability disclosure analysis through LLM-based tools. *EMNLP 2023 (demo)*.
- Niklaus, J. et al. (2023). LEXTREME: A multi-lingual and multi-task benchmark for the legal domain. *Findings of EMNLP 2023*.
- Niklaus, J. et al. (2024). MultiLegalPile: A 689GB multilingual legal corpus. *ACL 2024*.
- Page, M. J. et al. (2021). The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. *BMJ*, 372:n71.
- Peng, B. et al. (2024). Graph retrieval-augmented generation: A survey. arXiv:2408.08921.
- Pipitone, N., Alami, G. H. (2024). LegalBench-RAG: A benchmark for retrieval-augmented generation in the legal domain. arXiv:2408.10343.
- Qu, R., Tu, R., Bao, F. (2024). Is semantic chunking worth the computational cost? arXiv:2410.13070.
- Reimers, N., Gurevych, I. (2020). Making monolingual sentence embeddings multilingual using knowledge distillation. *EMNLP 2020*.
- Robertson, S., Zaragoza, H. (2009). The probabilistic relevance framework: BM25 and beyond. *Foundations and Trends in IR*, 3(4).
- Saad-Falcon, J., Khattab, O., Potts, C., Zaharia, M. (2024). ARES: An automated evaluation framework for retrieval-augmented generation systems. *NAACL 2024*.
- Sansone, C., Sperlì, G. (2022). Legal information retrieval systems: State-of-the-art and open issues. *Information Systems*, 106.
- Sarthi, P., Abdullah, S., Tuli, A., Khanna, S., Goldie, A., Manning, C. D. (2024). RAPTOR: Recursive abstractive processing for tree-organized retrieval. *ICLR 2024*. arXiv:2401.18059.
- Schimanski, T. et al. (2024). ClimRetrieve: A benchmarking dataset for information retrieval from corporate climate disclosures. *Findings of EMNLP 2024*.
- Singh, I. S., Aggarwal, R., Allahverdiyev, I., Taha, M., Akalin, A., Zhu, K., O'Brien, S. (2024). ChunkRAG: Novel LLM-chunk filtering method for RAG systems. arXiv:2410.19572.
- Smith, B., Troynikov, A. (2024). *Evaluating chunking strategies for retrieval*. Chroma technical report.
- Smucker, M. D., Allan, J., Carterette, B. (2007). A comparison of statistical significance tests for information retrieval evaluation. *CIKM 2007*.
- Tang, Y., Yang, Y. (2024). MultiHop-RAG: Benchmarking retrieval-augmented generation for multi-hop queries. arXiv:2401.15391.
- Vaghefi, S. A. et al. (2023). ChatClimate: Grounding conversational AI in climate science. *Communications Earth & Environment*, 4.
- Wang, L., Yang, N., Huang, X., Yang, L., Majumder, R., Wei, F. (2024). Multilingual E5 text embeddings: A technical report. arXiv:2402.05672.
- Yan, S.-Q., Gu, J.-C., Zhu, Y., Ling, Z.-H. (2024). Corrective retrieval augmented generation. arXiv:2401.15884.
- Yepes, A. J., You, Y., Milczek, J., Laverde, S., Li, R. (2024). Financial report chunking for effective retrieval augmented generation. arXiv:2402.05131.
- Zhao, J. et al. (2024). Meta-Chunking: Learning efficient text segmentation via logical perception. arXiv:2410.12788.
- Zheng, L. et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *NeurIPS 2023 Datasets and Benchmarks*.
- Zhong, Z. et al. (2024). Mix-of-Granularity: Optimize the chunking granularity for retrieval-augmented generation. arXiv:2406.00456.
- [Own] Jiménez Martín, G. et al. (2026). *[IPMU 2026 short paper — title to be inserted]*. Non-additive aggregation for retrieval in a regulatory assistant.
- Directive (EU) 2022/2464 (CSRD); Directive (EU) 2024/1760 (CSDDD); Commission Delegated Regulation (EU) 2023/2772 (ESRS); European Commission, Omnibus I proposals COM(2025) 80 and 81; OECD (2023) Guidelines for Multinational Enterprises on Responsible Business Conduct; OECD (2018) Due Diligence Guidance for Responsible Business Conduct; GRI Standards (2021/2023).

---

## Appendix A — Systematic review: search strings and PRISMA flow

*Full per-source strings, date of execution, and the PRISMA 2020 flow diagram go here. Placeholders in §3.2 must be filled from the executed search before submission.*

## Appendix B — Article identifier grammar

Implemented in `kb_experiment.article_id` and `corpus_pipeline._HEADING_PATTERNS`. Inputs: heading labels or human-written gold labels ("Artículo 8.3", "art. 10 bis", "Anexo I", "306-2", "E1-6"). Output: `art.N[.bis|.ter]`, `anexo.R`, `gri.NNN-N`, `esrs.X`. Paragraph suffixes are dropped.

## Appendix C — Golden set schema (extension)

```json
{"id": "gs-011", "pregunta": "…", "parafrasis": "…",
 "categoria": "CSDDD", "tipo": "multi_salto", "dificultad": "alta",
 "respuesta_ref": "…",
 "fuentes_esperadas": [
   {"documento": "02_NORMATIVAS/CSDDD", "pagina": 12, "articulo": "Artículo 8"},
   {"documento": "02_NORMATIVAS/CSDDD", "pagina": 15, "articulo": "art. 10.2"}]}
```

## Appendix D — Reproducibility checklist

Code: `src/corpus_pipeline.py`, `src/kb_experiment.py`, `src/rag_benchmark.py`. Smoke corpus: `benchmarks/smoke/`. Command lines: `benchmarks/README.md`. Embedding models pinned by Hugging Face revision. LLM arms: model name, date, temperature 0, two runs. Corpus release: text where licensed; otherwise SHA-256 per document plus unit offsets. Golden set: `benchmarks/golden_set_v1/` with κ report and data statement.

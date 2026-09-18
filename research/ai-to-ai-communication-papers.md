# AI-to-AI communication: paper summaries

**For:** the synapse lane's design work on efficient AI-to-AI communication. **Requested by** CireSnave, via the portfolio PM, 2026-09-16. **Scope:** summaries only. This file draws no design conclusions.

For each paper: the problem; the mechanism; the **access** it needs (`api-tokens` = text through a hosted API; `model-internals` = hidden states, embeddings, KV caches or logits; `training` = fine-tuning or training any model); measured gains with the paper's own metric and benchmark; limitations; and a tier: `token`, `latent` or `neither`.

## Read these flags first

| Item | Flag |
|---|---|
| **#8 Gibberlink spec (Scribd)** | **Not fetched.** Scribd served only its landing page. **A copy is needed.** |
| **#3 doi:10.1080/01691864.2026.2661958** | The publisher refused the fetch (HTTP 403). Summarised from the **arXiv preprint 2501.00226**, matched by Crossref title and all five authors. The journal version may differ. |
| **#4 arXiv:2503.17407** | **STRUCK (CireSnave, 2026-09-18, verbatim):** *"I agree with your earlier statement that ArXiV 2503.17407v2 was likely not a good fit as a paper covering something relating directly to LLM-to-LLM language. Consider that one stricken."* Resolves to *A Comprehensive Survey on Long Context Language Modeling*, not agent communication - the ID flag below is what he's confirming. |
| **#15 recursivemas.github.io** | A project page; summarised from the paper it links, arXiv:2604.25917. |

**How much to trust these summaries.** The bulk reading was done by a non-Claude model, per the cost rule. Its figures were then checked mechanically, not by reading. A ✅ means the claim's numbers appear in a quote, and the quote appears verbatim in the extracted paper text (whitespace-normalised). **The check proves a quote exists; it does not prove the quote supports the claim.** A real sentence attached to the wrong benchmark would still pass. The summariser also assigned the tier and access labels; the reviewer notes at the end list the ones that look questionable.

Summarised by `models/gemini-3.5-flash`, `models/gemini-3.6-flash` (Google AI Studio free tier), one call per paper at temperature 0, via `probe/paper_summaries.py`. Of 54 reported gains: **43 ✅** carry a figure found in a quote that appears verbatim in the paper; **4 ℹ️** have a verbatim quote but state no figure; **7 ⚠️** failed the check, with the reason and the quote shown. Every quote is in the companion `.json`.

| # | Source | Tier | Access |
|---|---|---|---|
| 1 | [arXiv:2410.11905 (Agora)](https://arxiv.org/abs/2410.11905) | `token` | api-tokens |
| 2 | [arXiv:2412.07646](https://arxiv.org/abs/2412.07646) | `token` | api-tokens, model-internals |
| 3 | [doi:10.1080/01691864.2026.2661958 (Advanced Robotics)](https://doi.org/10.1080/01691864.2026.2661958) | `neither` | model-internals, training |
| 4 | ~~[arXiv:2503.17407](https://arxiv.org/abs/2503.17407)~~ **STRUCK** | `neither` |  |
| 5 | [arXiv:2609.01491 (GlossoGen; listed via alphaxiv)](https://arxiv.org/abs/2609.01491) | `token` | api-tokens |
| 6 | [arXiv:2503.01063](https://arxiv.org/abs/2503.01063) | `token` | api-tokens |
| 7 | [arXiv:2505.12741](https://arxiv.org/abs/2505.12741) | `latent` | model-internals, training |
| 8 | [Gibberlink Mode Protocol spec (Scribd 972509569)](https://www.scribd.com/document/972509569) | **not summarised** | - |
| 9 | [arXiv:2606.29354](https://arxiv.org/abs/2606.29354) | `token` | api-tokens |
| 10 | [EMNLP 2025 main, paper 518](https://aclanthology.org/2025.emnlp-main.518/) | `latent` | model-internals |
| 11 | [arXiv:2606.19135](https://arxiv.org/abs/2606.19135) | `neither` | api-tokens |
| 12 | [arXiv:2510.13821 (LACP)](https://arxiv.org/abs/2510.13821) | `token` | api-tokens |
| 13 | [arXiv:2606.19857](https://arxiv.org/abs/2606.19857) | `token` | api-tokens |
| 14 | [arXiv:2606.05711](https://arxiv.org/abs/2606.05711) | `latent` | model-internals, training |
| 15 | [recursivemas.github.io -> arXiv:2604.25917](https://recursivemas.github.io/) | `latent` | model-internals, training |
| 16 | [arXiv:2411.02820 (DroidSpeak)](https://arxiv.org/abs/2411.02820) | `latent` | model-internals |
| 17 | [arXiv:2402.18439](https://arxiv.org/abs/2402.18439) | `token` | api-tokens |

## 1. A SCALABLE COMMUNICATION PROTOCOL FOR NETWORKS OF LARGE LANGUAGE MODELS

[arXiv:2410.11905 (Agora)](https://arxiv.org/abs/2410.11905) · `models/gemini-3.6-flash`

- **Problem:** Scaling networks of heterogeneous LLM agents introduces the Agent Communication Trilemma, where protocols must balance versatility, efficiency, and portability. Existing solutions trade off these traits, resulting in expensive natural language communications, inflexible REST APIs, or high human implementation effort.
- **Mechanism:** Agents communicate over HTTPS using JSON messages that include a protocol hash, protocol download URIs, and a payload body. For frequent interactions, agents autonomously negotiate plain-text protocol documents (PDs) and generate local code routines to process structured JSON messages directly without invoking an LLM. Rare interactions or routine failures fall back to natural language processed by LLMs.
- **Access needed:** api-tokens — The protocol requires text-level API access to LLMs for instruction following, protocol negotiation, code routine synthesis, and natural language fallback.
- **Measured gains:**
  - Agora reduces execution cost by approximately five times compared to natural language communication in a 100-agent network. — *metric:* overall cost in API queries (USD); *benchmark:* 100-agent heterogeneous network running 1000 task queries; *vs:* natural language communication. ⚠️ claim numbers ['100'] not in quote - quote is verbatim; the extra figure(s) appear elsewhere in the paper: “executing this demo with Agora is approximately five times cheaper than with regular natural language.”
  - Negotiating a protocol and generating routines costs 0.043 USD compared to 0.020 USD for a single natural-language query, achieving net savings after three uses. — *metric:* cost in API calls (USD); *benchmark:* 2-agent weather data retrieval task; *vs:* natural-language exchange. ✅
  - Across 1000 task queries in a 100-agent network, only 0.8% failed due to third-party model API issues. — *metric:* query failure rate (%); *benchmark:* 100-agent network demo running 1000 queries; *vs:* N/A. ⚠️ claim numbers ['100'] not in quote - quote is verbatim; the extra figure(s) appear elsewhere in the paper: “Out of 1000 queries, 8 (representing thus 0.8% of the total query volume) failed due to Google’s Gemini API not responding.”
- **Limitations:** Negotiating protocols and writing routines incurs an initial cost overhead, requiring multiple transactions to achieve cost efficiency. Additionally, partial network isolation can lead to duplicate protocols emerging independently, and the framework relies on LLM capability and third-party API stability for code generation and negotiation.
- **Tier:** `token`

## 2. Searching for Structure: Investigating Emergent Communication with Large Language Models

[arXiv:2412.07646](https://arxiv.org/abs/2412.07646) · `models/gemini-3.6-flash`

- **Problem:** This paper investigates whether artificial languages evolve structural properties that optimize communicative efficiency and learnability when shaped by the implicit biases of Large Language Models (LLMs) in a referential signaling game.
- **Mechanism:** LLM agents communicate in a referential game by exchanging text signals (concatenations of CV syllables) formatted in structured JSON-like in-context prompts. The speaker model observes stimulus attributes and completes a prompt to produce a text utterance, while the listener model identifies the target stimulus by selecting the distractor candidate that yields the highest log-probability when prefilled into the completion prompt.
- **Access needed:** api-tokens, model-internals — The method requires passing textual prompts via API/tokens and accessing output token probabilities/logits to score prefilled distractor completions during listener discrimination.
- **Measured gains:**
  - LLMs achieve near-perfect accuracy when guessing signals for target stimuli in lookup tasks. — *metric:* guessing accuracy (M); *benchmark:* 15 training stimuli in the guessing block; *vs:* chance performance. ℹ️ quote verified; the claim states no figure: “LLMs were able to guess the correct signals for the stimuli almost perfectly (M = .973,SD = .031).”
  - Communicative success between LLM agents starts at approximately 70% in the first round and increases to roughly 75% in later rounds. — *metric:* PercCom (percentage of successful interactions); *benchmark:* 30-interaction referential communication game rounds; *vs:* chance performance of 25%. ⚠️ quote not found in paper - near-verbatim (likely extraction noise): 92% of quote matches in order: “approximately 70% of the interactions in the first round are successful (chance performance would amount to 25%). This increases somewhat in the following rounds to≈ 75%”
  - Higher topographic similarity in evolved languages strongly correlates with improved generalisation to unseen stimuli. — *metric:* Pearson correlation coefficient r; *benchmark:* 27-stimuli testing block generalisation (GenScore vs TopSim); *vs:* unstructured / low TopSim languages. ℹ️ quote verified; the claim states no figure: “high TopSim languages allow for better generalisation (r = 0.735,p<. 001, Figure 3).”
  - Labelling initial unstructured stimuli via prompt completion yields an average accuracy of 0.453. — *metric:* labelling accuracy (M); *benchmark:* 15 training stimuli in the labelling completion block; *vs:* guessing accuracy (0.973). ✅
  - Generational transmission via iterated learning significantly reduces the ratio of uniquely produced signals by generation 7. — *metric:* ratio of uniquely produced signals (M); *benchmark:* 8-generation iterated learning transmission chain testing block; *vs:* generation 0 (M gen0 = .707). ✅
- **Limitations:** Languages often exhibit non-humanlike tendencies such as increasing message lengths over communication rounds and developing degenerate, underspecified vocabularies. The framework relies on greedy decoding and fixed in-context prompt structures, lacking cognitive or memory constraints found in humans.
- **Tier:** `token`

## 3. Generative Emergent Communication: Large Language Model is a Collective World Model

[doi:10.1080/01691864.2026.2661958 (Advanced Robotics)](https://doi.org/10.1080/01691864.2026.2661958) · `models/gemini-3.6-flash`

> ⚠️ Publisher page refused this fetch (HTTP 403 from tandfonline via doi.org). Summarised from the arXiv PREPRINT 2501.00226, matched to the DOI by Crossref title and all five authors. The journal version may differ.

- **Problem:** It remains a puzzle how disembodied Large Language Models (LLMs) acquire rich world knowledge without direct sensorimotor experience. Existing theories fail to bridge the gap between an individual agent's grounded world model and the collective emergence of a shared language that reflects that knowledge.
- **Mechanism:** Agents exchange external symbolic messages to perform decentralized Bayesian inference over their internal latent representations, minimizing the group's Collective Free Energy. In games such as the Metropolis-Hastings Naming Game, a speaker samples a message based on its internal state, and a listener accepts or rejects it based on conditional likelihoods. Through this interactive process, the society collectively encodes its grounded sensorimotor experiences into language, which an LLM subsequently decodes to reconstruct the latent structure of the collective world model.
- **Access needed:** model-internals, training — Agents must access their local internal latent representations and conditional likelihood distributions to evaluate and sample messages, as well as update internal model parameters during learning.
- **Measured gains:** none reported
- **Limitations:** Direct empirical evidence for the collective world model hypothesis remains limited, and the relationship between neural representations in LLMs and human conceptual structures requires further investigation. Additionally, the framework focuses primarily on linguistic structure and meaning rather than pragmatic language use or multi-agent language evolution in open, dynamic environments.
- **Tier:** `neither`

## 4. ~~A Comprehensive Survey on Long Context Language Modeling~~ STRUCK

[arXiv:2503.17407](https://arxiv.org/abs/2503.17407) · `models/gemini-3.6-flash`

> 🔴 **STRUCK (CireSnave, 2026-09-18, verbatim):** *"I agree with your earlier statement that ArXiV
> 2503.17407v2 was likely not a good fit as a paper covering something relating directly to LLM-to-LLM
> language. Consider that one stricken."* Kept below, not deleted, as the record of why it was
> excluded - this entry no longer counts toward this file's paper list.
>
> ⚠️ This ID resolves to 'A Comprehensive Survey on Long Context Language Modeling', which does not look like an agent-communication paper. Check the ID; summarised as it resolves.

- **Problem:** This paper is a survey on long context language modeling and is not about communication between AI agents or models.
- **Mechanism:** The paper provides a comprehensive review of long context language modeling across three main dimensions. It examines data engineering, architectural designs (such as position embeddings and linear attention), and workflow strategies (such as prompt compression and memory modules). Additionally, it surveys training/inference infrastructure, evaluation benchmarks for comprehension and long-form generation, and behavioral/mechanistic analysis.
- **Access needed:**  — As a survey paper, it covers techniques across all levels of model access, including text-based APIs, internal KV caches and hidden states, and model training.
- **Measured gains:** none reported
- **Limitations:** Existing long context models exhibit a large gap between claimed supported context lengths and effective context lengths, suffer from performance degradation in long-form generation, and incur severe memory and computational costs during inference.
- **Tier:** `neither`

## 5. GLOSSOGEN: Emergent Language in Complex Multi-Agent LLM Interactions

[arXiv:2609.01491 (GlossoGen; listed via alphaxiv)](https://arxiv.org/abs/2609.01491) · `models/gemini-3.6-flash`

- **Problem:** Prior work on emergent communication mostly focuses on simple reference games or agents trained from scratch, leaving a gap in understanding how pre-trained LLM agents develop non-English communication protocols in complex, multi-turn, sequential scenarios.
- **Mechanism:** Agents communicate through text messages over structured Slack-style channels alongside tool calls. In the cooperative SAVEVEYRU scenario, a Field Observer reports partial environmental observations while a Specialist provides treatment steps over a channel constrained by a character-based time budget. Between rounds, agents can optionally use an unconstrained 'postmortem' text channel to deliberate on past performance and negotiate symbolic shorthand conventions.
- **Access needed:** api-tokens — Only standard text generation and token exchange via API interfaces are required to operate the LLM agents and communication channels.
- **Measured gains:**
  - Under high time pressure and postmortem access, mean perplexity of agent messages increases by approximately 430% across proprietary models compared to maximum budget without postmortem. — *metric:* mean perplexity; *benchmark:* SAVEVEYRU scenario; *vs:* setting with maximum budget and no postmortem. ✅
  - Proprietary models achieved a 92.1% task success rate under the 2000s budget compared to 30.8% for open-weight models. — *metric:* success rate; *benchmark:* SAVEVEYRU scenario at 2000s budget; *vs:* open-weight models (Qwen3-32B and Llama-3.3-70B-Instruct). ⚠️ quote not found in paper - near-verbatim (likely extraction noise): 99% of quote matches in order: “as evidenced by their relatively lower success rates even in the high-est budget setting (30.8% for open-weight models, vs. 92.1% for proprietary models, at 2000s).”
  - The Haiku 4.5 LLM judge achieved 95.7% accuracy when evaluated against a gold action benchmark dataset. — *metric:* accuracy; *benchmark:* 500-example dataset of gold and distractor actions; *vs:* three-run consensus followed by manual validation. ⚠️ quote not found in paper - near-verbatim (likely extraction noise): 100% of quote matches in order: “The judge was verified against a balanced 500-example constructed dataset of gold actions – as determined by three-run consensus followed by manual validation – and distractor actions, where it achieved 95.7% accuracy.”
  - Human and LLM judge annotations agreed on identifying metalinguistic questions with a Cohen's kappa of 0.96. — *metric:* Cohen's kappa; *benchmark:* 50 randomly-sampled agent messages; *vs:* human blind annotation. ⚠️ quote not found in paper - paraphrased, number present in paper: 58% of quote matches in order: “Agreement was strong, with 49/50 agreement on whether an example constitutes a question (Cohen’sκ= 0.96 ), with 28 examples labeled as questions; the annotator and LLM judge agreed on the compositionality of25/28of the q”
- **Limitations:** Emergent non-English languages fail to develop without access to a postmortem deliberation channel, and high budget pressure remains difficult with agents failing on the majority of low-budget runs even with postmortem access. Less capable open-weight models (such as Qwen3-32B and Llama-3.3-70B-Instruct) fail to construct emergent shorthand languages even when provided both time pressure and postmortem channels.
- **Tier:** `token`

## 6. AI-Invented Tonal Languages: Preventing a Machine Lingua Franca Beyond Human Understanding

[arXiv:2503.01063](https://arxiv.org/abs/2503.01063) · `models/gemini-3.6-flash`

- **Problem:** The paper addresses the risk of AI agents autonomously developing private, human-incomprehensible tonal languages for machine-to-machine communication that evade human oversight and auditing.
- **Mechanism:** Agents convert text characters (ASCII 32–126) into discrete audio frequencies using equal temperament semitone scaling defined by f = 220 × 2^((i-32)/12) Hz. Messages are transmitted as short audio tones (ranging from 220 Hz up to ultrasonic 50,175.42 Hz) or stored as ABC musical notation, and are decoded back into text via frequency domain analysis or multimodal transformer interfaces.
- **Access needed:** api-tokens — The method only requires sending and receiving text or multimodal audio tokens through model API endpoints.
- **Measured gains:** none reported
- **Limitations:** The mapping relies on Western musical conventions, lacks semantic compression by allocating equal encoding resources to every character, and faces real-world ultrasonic limitations such as environmental noise and signal degradation. Additionally, it uses a fixed predetermined lookup table rather than demonstrating truly emergent linguistic conventions between multiple agents.
- **Tier:** `token`

## 7. Language Model Networks: Supervision-Efficient Learning through Dense Communication

[arXiv:2505.12741](https://arxiv.org/abs/2505.12741) · `models/gemini-3.6-flash`

- **Problem:** Existing multi-model and multi-agent systems communicate via discrete natural language text, creating an optimization bottleneck that causes information loss during token embedding/de-embedding cycles and prevents end-to-end gradient flow.
- **Mechanism:** LMNet strips internal embedding and de-embedding layers from language models serving as vertex nodes in a computational graph, enabling intermediate models to directly exchange dense vectors (hidden states). Small, trainable sequence-to-sequence modules serve as communication edges that align, translate, and route these dense vector sequences between vertexes. Natural language input and output interfaces are preserved solely at the system boundaries, allowing the full network to be optimized end-to-end via gradient descent from task-level supervision.
- **Access needed:** model-internals, training — The architecture requires access to intermediate internal hidden states (dense vector representations), stripped transformer layers, and model parameters for end-to-end gradient-based optimization.
- **Measured gains:**
  - LMNet achieves a 30.5% relative performance improvement over prompting across general reasoning, science, math, and coding benchmarks. — *metric:* Relative Improvement; *benchmark:* Combined benchmarks (MMLU, BBH, GSM8K, MATH, HumanEval, MBPP, etc. with Qwen2.5-0.5B); *vs:* Prompt. ⚠️ quote not found in paper - near-verbatim (likely extraction noise): 99% of quote matches in order: “First, LMNet brings consistent and significant improvement compared with Prompt (+30.5%), which can be viewed as natural-language communication with the model itself.”
  - LMNet outperforms test-time scaling strategies Self-Consistency (+3.4%) and Self-Refine (+0.5%) under equivalent compute budgets. — *metric:* Relative Improvement; *benchmark:* Combined benchmarks on Qwen2.5-0.5B; *vs:* Self-Refine (+0.5%) and Self-Consistency (+3.4%). ✅
  - LMNet outperforms parameter-efficient fine-tuning baselines including LoRA and Adapters on the E2E dataset. — *metric:* BLEU; *benchmark:* E2E dataset (GPT2-M); *vs:* LoRA (68.9 BLEU) and Full Fine-Tuning (68.2 BLEU). ℹ️ quote verified; the claim states no figure: “LMNet 70.5 8.85 46.5 71.52.48 1.6”
  - LMNet improves reasoning performance on MMLU beyond fine-tuning and continuous latent reasoning approaches. — *metric:* ∆Acc; *benchmark:* MMLU (Qwen2.5-1.5B); *vs:* SFT (35.02%), COCONUT (33.85%), and CODI (35.16%). ℹ️ quote verified; the claim states no figure: “LMNet 13.10 27.19 27.57 23.35 37.28”
- **Limitations:** LMNet increases parameter count and per-token inference latency compared to a single LLM node. It requires direct access to model internals and parameter optimization, preventing its use with black-box API models, and its dense vector communications lack human interpretability. Furthermore, end-to-end auto-regressive decoding does not yet realize inner-auto-regressive sentence-level message passing.
- **Tier:** `latent`

## 8. Gibberlink Mode Protocol spec (Scribd 972509569)

[Gibberlink Mode Protocol spec (Scribd 972509569)](https://www.scribd.com/document/972509569)

> ⚠️ NOT FETCHED. Scribd served only its landing page (HTTP 200, 3,151 characters of page chrome): a 23-page document uploaded by a Scribd user, with a description Scribd itself labels 'AI-enhanced'. No specification text was available, so nothing is summarised. A copy is needed.

**Not summarised.**

## 9. When LLMs Develop Languages: Symbolic Communication for Efficient Multi-Agent Reasoning

[arXiv:2606.29354](https://arxiv.org/abs/2606.29354) · `models/gemini-3.6-flash`

- **Problem:** Chain-of-Thought (CoT) reasoning incurs long, verbose natural-language rationales that add substantial token latency and cost. Existing prompt optimization and compression techniques fail to create persistent, socially emergent symbolic protocols that agents can adaptively share, route, and compose.
- **Mechanism:** Multiple frozen LLM agents autonomously synthesize, evaluate, and mutate custom Language Symbolism Frameworks (LSFs)—textual protocols containing compact lexicons, grammars, and usage constraints—via an offline evolutionary loop driven by correctness and token efficiency. At test time, a latent-free LLM router inspects query difficulty and LSF profile descriptors to dynamically choose a single LSF call, aggregate multi-LSF outputs, or execute sequential multi-round LSF message passing.
- **Access needed:** api-tokens — The backbone models remain frozen black-box instances requiring access only to text inputs and outputs (tokens) via an API.
- **Measured gains:**
  - CLSR reduces latency-oriented generated completion tokens by 3 to 6 times compared to standard CoT while maintaining accuracy — *metric:* generated token completion; *benchmark:* seven reasoning benchmarks (MMLU-Pro, GPQA, GSM8K, MATH-500, AIME21-24, ScienceQA, HotpotQA); *vs:* standard CoT. ✅
  - CLSR with T=3 reaches 94.8% accuracy on GSM8K using 214 generated tokens — *metric:* accuracy; *benchmark:* GSM8K; *vs:* PoT and PAL. ✅
  - CLSR with T=3 reaches 89.7% accuracy on MATH500 using 417 generated tokens — *metric:* accuracy; *benchmark:* MATH500; *vs:* PoT and PAL. ✅
- **Limitations:** The offline evolution of language protocols requires upfront computational cost and training exemplars with reasoning traces. In addition, highly compressed symbolic reasoning traces are less directly human-interpretable than full natural-language rationales, which may complicate direct human auditing.
- **Tier:** `token`

## 10. Augmenting Multi-Agent Communication with State Delta Trajectory

[EMNLP 2025 main, paper 518](https://aclanthology.org/2025.emnlp-main.518/) · `models/gemini-3.6-flash`

- **Problem:** Using natural language tokens for multi-agent communication introduces information loss because models must downsample continuous internal state vectors to discrete tokens. This sampling discards unchosen reasoning paths and latent logic that would be valuable to other agents sharing the same base LLM.
- **Mechanism:** Agents exchange natural language tokens along with a token-wise state delta trajectory extracted from internal representations. As a sender agent generates each token, State Delta Encoding (SDE) computes the difference between adjacent hidden states at specific selected transformer layers. When a receiver agent processes these output tokens, these state delta vectors are added directly to the receiver's corresponding token hidden states before passing them to subsequent transformer layers.
- **Access needed:** model-internals — Intermediate hidden states at specific, pre-selected transformer layers must be extractable during generation and injectable into another agent's forward pass during token encoding.
- **Measured gains:**
  - SDE improves performance in information asymmetry tasks by 0.3% to 8.9% over the best baseline. — *metric:* EM / F1 / Accuracy; *benchmark:* Information asymmetry tasks (Quasar-T, ComplexWebQuestions, StrategyQA); *vs:* Best-performing baseline (NL or CIPHER). ✅
  - SDE enhances multi-agent debate performance by 0.3% to 13.7% compared to the best baseline. — *metric:* Accuracy; *benchmark:* Multi-agent debate tasks (GSM8K, MMLU Abstract Algebra, College Math, Formal Logic); *vs:* Best-performing baseline (NL or CIPHER). ✅
  - SDE improves multi-agent workflow architectures by up to 17.3% over baselines. — *metric:* Accuracy / EM / F1; *benchmark:* Agent workflow tasks (FEVER, HotpotQA, StrategyQA); *vs:* Best-performing baseline (NL or CIPHER). ✅
- **Limitations:** SDE requires access to model internal states, making it infeasible for agents built on black-box model APIs. Exchanging continuous vector trajectories increases inter-agent communication bandwidth overhead. Furthermore, injecting state deltas into all transformer layers degrades generation performance, requiring careful layer selection.
- **Tier:** `latent`

## 11. A Technical Taxonomy of LLM Agent Communication Protocols

[arXiv:2606.19135](https://arxiv.org/abs/2606.19135) · `models/gemini-3.6-flash`

- **Problem:** The fragmented ecosystem of LLM agent frameworks lacks standardized communication protocols, preventing heterogeneous agents from seamlessly discovering each other and collaborating across distributed multi-agent networks.
- **Mechanism:** Agents and external context systems exchange natural language text, structured data, or hybrid payloads containing both. Exchanged via application-layer APIs over protocols like HTTP, WebSockets, and peer-to-peer networks, communication spans stateless tool calls to stateful multi-turn sessions. Protocols format these exchanges using single static schemas, multiple predefined templates, or dynamic runtime schema negotiation.
- **Access needed:** api-tokens — Communication takes place strictly at the application layer via text and structured JSON payloads passed to standard API endpoints.
- **Measured gains:** none reported
- **Limitations:** The framework excludes proprietary protocols, conceptual models lacking open-source implementations, and protocols not designed specifically for LLM agents. Furthermore, the surveyed protocols consistently lack privacy safeguards, compliance checks, and policy enforcement mechanisms needed for safety-critical deployments.
- **Tier:** `neither`

## 12. LLM Agent Communication Protocol (LACP) Requires Urgent Standardization: A Telecom-Inspired Protocol is Necessary

[arXiv:2510.13821 (LACP)](https://arxiv.org/abs/2510.13821) · `models/gemini-3.5-flash`

- **Problem:** The current LLM agent communication landscape is fragmented with proprietary, ad-hoc protocols, leading to interoperability gaps, security vulnerabilities, and a lack of transactional integrity.
- **Mechanism:** Agents communicate using a three-layer protocol consisting of Semantic, Transactional, and Transport layers. Messages are structured with semantic intents (such as PLAN, ACT, and OBSERVE), wrapped in a JSON Web Signature (JWS) envelope with unique transaction IDs for security and idempotency, and transmitted over transport-agnostic protocols like HTTP/2 or QUIC.
- **Access needed:** api-tokens — Only text-based JSON payloads sent via standard network APIs need to be reachable.
- **Measured gains:**
  - LACP increases latency by an absolute value of only 0.03 ms for large, complex tasks compared to a standard REST baseline. — *metric:* Latency overhead; *benchmark:* Large payload (1,964 Bytes) sequential requests; *vs:* Standard, non-secured RESTful API communication baseline. ✅
  - The payload size overhead of LACP shrinks to +30% for realistic payloads. — *metric:* Size Overhead (%); *benchmark:* Large payload (1,964 Bytes) sequential requests; *vs:* Standard, non-secured RESTful API communication baseline. ✅
  - The performance and overhead analysis was validated by sending 10,000 sequential requests to the endpoints. — *metric:* Number of sequential requests; *benchmark:* Performance and overhead analysis benchmark script; *vs:* Standard, non-secured RESTful API communication baseline. ✅
- **Limitations:** LACP introduces a significant payload size overhead (up to +500%) for small, trivial messages like heartbeats. It also requires additional computational overhead for cryptographic signature verification and transaction tracking.
- **Tier:** `token`

## 13. Large Language Models Do Not Always Need Readable Language

[arXiv:2606.19857](https://arxiv.org/abs/2606.19857) · `models/gemini-3.5-flash`

- **Problem:** Natural language optimized for human communication contains substantial redundancy, which reduces semantic density and creates context overhead bottlenecks in long-context and multi-agent systems. This paper investigates whether semantic information can be encoded in compact, non-standard textual forms that sacrifice human readability while remaining recoverable by LLMs.
- **Mechanism:** Agents exchange BabelTele, a class of compact, non-standard textual representations that combine abbreviations, symbols, cross-lingual fragments, and non-standard syntactic structures. These representations are generated by a compressor LLM using zero-shot prompting that relaxes human readability constraints. The resulting high-density text is then transmitted to and decoded by a reader LLM to perform downstream tasks.
- **Access needed:** api-tokens — Only text input and output via a black-box API is required, with no access to model internals, gradient updates, or tokenizer modifications.
- **Measured gains:**
  - BabelTele maintains 99.5% semantic fidelity while condensing text volume to 27.9% of its original length. — *metric:* semantic fidelity and text volume condensation ratio; *benchmark:* task-agnostic representational paradigm; *vs:* original uncompressed text. ✅
  - On Qwen3.6-Max, BabelTele representations allow the model to capture broader evidence and achieve 62.07% accuracy compared to 55.17% for truncated input. — *metric:* accuracy; *benchmark:* Code Repo QA Long subset from LongBench v2; *vs:* direct truncation of original input. ✅
  - Gemini 3.1 Pro achieves over 95% compression, while GPT-5.4 is more conservative at roughly 75% compression. — *metric:* compression rate; *benchmark:* Short subset of the LongBench v2 benchmark; *vs:* original uncompressed text. ✅
  - BabelTele achieves a compression rate of around 50% on the LoCoMo agent memory benchmark. — *metric:* compression rate; *benchmark:* LoCoMo agent memory benchmark; *vs:* original uncompressed text. ✅
  - The Quality drop when using Gemini-induced BabelTele inputs remains within a relatively narrow range of 10.75 to 14.95 percentage points across Qwen-family models. — *metric:* Quality drop (percentage points); *benchmark:* QuALITY dataset (Qwen-family models); *vs:* original inputs. ✅
- **Limitations:** The evaluation is limited to a selected set of benchmarks and model families, and its effectiveness depends on the specific compressor-reader pair and task setting. Additionally, as an empirical study, it primarily characterizes the phenomenon without fully explaining the underlying theoretical mechanisms of how LLMs form and interpret these representations.
- **Tier:** `token`

## 14. BEYONDTOKENS: A UNIFIEDFRAMEWORK FOR LATENTCOMMUNICATION INLLM-BASED MULTI-AGENTSYSTEMS

[arXiv:2606.05711](https://arxiv.org/abs/2606.05711) · `models/gemini-3.5-flash`

- **Problem:** Multi-agent systems built on large language models typically communicate via natural language, which introduces high inference costs, potential information loss during discretization, and linguistic redundancy. This survey unifies and analyzes the emerging area of latent communication to address these limitations.
- **Mechanism:** Agents exchange continuous model states directly, such as input embeddings, hidden states, or key-value (KV) caches, bypassing the natural language token bottleneck. These continuous representations are aligned across agents (either training-free for identical models or via learned projections/codecs for heterogeneous ones) and fused into the receiver's computation through concatenation, prepending, mathematical operations, or cross-attention.
- **Access needed:** model-internals, training — Access to model internals is required, specifically continuous representations like input embeddings, intermediate hidden states, and KV caches, as well as the ability to inject these into the receiver's layers or train alignment adapters.
- **Measured gains:**
  - AC achieves approximately 27% accuracy improvement over natural language communication — *metric:* accuracy improvement; *benchmark:* representative benchmark suite; *vs:* natural language communication. ✅
  - Interlat achieves up to 24x speedup over natural language communication — *metric:* latency reduction; *benchmark:* long-context multi-agent tasks; *vs:* NL-Comm. ✅
  - Agent Primitives achieves 12.0-16.5% average accuracy improvement over single-agent baselines — *metric:* average accuracy; *benchmark:* multi-agent systems; *vs:* single-agent baselines. ✅
  - RelayCaching achieves up to 4.7x TTFT speedup on math, code, and general knowledge tasks — *metric:* TTFT speedup; *benchmark:* math, code, and general knowledge tasks; *vs:* baselines. ✅
  - Agent Memory achieves up to 136x TTFT speedup on edge devices — *metric:* TTFT speedup; *benchmark:* Gemma 3 12B, DeepSeek-Coder-V2-Lite 16B, and Llama 3.1 8B; *vs:* re-prefill cost. ✅
  - Mixture of Thoughts achieves average gains of 0.38% on in-distribution and 2.92% on out-of-distribution benchmarks — *metric:* average gains; *benchmark:* five in-distribution and three out-of-distribution benchmarks; *vs:* Avengers. ✅
- **Limitations:** Latent communication is highly architecture-dependent, making cross-architecture transfer difficult without training complex alignment adapters. It also suffers from a lack of human interpretability, potential security vulnerabilities (such as untrusted or tampered latent states), and high transport costs for large payloads like full KV-caches.
- **Tier:** `latent`

## 15. Recursive Multi-Agent Systems

[recursivemas.github.io -> arXiv:2604.25917](https://recursivemas.github.io/) · `models/gemini-3.5-flash`

> ⚠️ The listed item is a project page; summarised from the paper it links, arXiv:2604.25917.

- **Problem:** Standard text-based multi-agent systems suffer from high latency due to sequential text generation and are difficult to co-optimize as a whole because text-based interactions cause gradient vanishing during training.
- **Mechanism:** Agents communicate by exchanging continuous latent representations (hidden states) in a recursive loop. An inner RecursiveLink (a two-layer residual projection module) refines an agent's ongoing latent thoughts during auto-regressive generation, while an outer RecursiveLink maps and transfers these latent representations across heterogeneous agents of different types and sizes.
- **Access needed:** model-internals, training — Requires access to the last-layer hidden states and input embeddings of the models, as well as the ability to train the parameters of the lightweight RecursiveLink modules.
- **Measured gains:**
  - Average accuracy improvement of 8.3% — *metric:* accuracy; *benchmark:* 9 benchmarks spanning mathematics, science, medicine, search, and code generation; *vs:* advanced recursive language models and MAS baselines. ✅
  - End-to-end inference speedup of 1.2x to 2.4x — *metric:* inference speedup; *benchmark:* 9 benchmarks spanning mathematics, science, medicine, search, and code generation; *vs:* advanced recursive language models and MAS baselines. ✅
  - Token usage reduction of 34.6% to 75.6% — *metric:* token usage reduction; *benchmark:* 9 benchmarks spanning mathematics, science, medicine, search, and code generation; *vs:* advanced recursive language models and MAS baselines. ✅
  - Accuracy improvement of 20.2% at recursion round r = 3 compared to text-based recursion — *metric:* accuracy; *benchmark:* seven math, science, and code generation tasks; *vs:* text-based recursion. ✅
  - Accuracy improvement of 8.0% for the learner model in distillation style — *metric:* accuracy; *benchmark:* AIME2026, GPQA-D, LiveCodeBench, MBPP+, MedQA; *vs:* Learner model. ✅
  - Accuracy improvement of 4.8% in deliberation style — *metric:* accuracy; *benchmark:* mathematical and search-intensive tasks; *vs:* original tool-calling agent. ✅
- **Limitations:** The framework requires training specialized projection layers (RecursiveLink) for each pair of communicating agents, and its performance gains stabilize once the latent thought length reaches a moderate budget of around 80 steps. Additionally, it requires access to model internals, making it incompatible with closed-source API-only models.
- **Tier:** `latent`

## 16. DroidSpeak: KV Cache Sharing for Cross-LLM Communication and Multi-LLM Serving

[arXiv:2411.02820 (DroidSpeak)](https://arxiv.org/abs/2411.02820) · `models/gemini-3.5-flash`

- **Problem:** In multi-LLM and agentic systems, different models often process inputs sharing the same context prefix, but reusing prefix KV caches across different models remains an open challenge because naive sharing causes severe generation quality drops.
- **Mechanism:** The sender model transmits its KV cache for non-critical layers and its embedding (E) cache at transition layers to the receiver model over the network. The receiver model then selectively recomputes only the critical layers of the KV cache while reusing the rest, pipelining the loading of the reused cache with the layer-wise recomputation to hide network latency.
- **Access needed:** model-internals — The system requires access to the layer-wise KV cache and embedding (E) cache of the models to selectively reuse, recompute, and transfer them.
- **Measured gains:**
  - Up to 4× throughput improvement — *metric:* throughput; *benchmark:* diverse datasets and model pairs under Poisson arrival rate; *vs:* baseline which does not allow any sharing across models. ✅
  - About 3.1× faster prefill (time to first token) — *metric:* prefill (time to first token); *benchmark:* diverse datasets and model pairs; *vs:* baseline which does not allow any sharing across models. ✅
  - Average prefill speedup of 2.1× — *metric:* prefill speedup; *benchmark:* three datasets and eight model pairs; *vs:* full prefill. ✅
  - Prefill latency reduction of 1.7–3.1× — *metric:* prefill latency; *benchmark:* three datasets and eight model pairs; *vs:* full prefill. ✅
  - Only 11% of layers identified as critical on average — *metric:* percentage of critical layers; *benchmark:* eight representative model pairs; *vs:* all layers (100%). ✅
  - 5–33% higher quality than CacheBlend — *metric:* generation quality (F1 score, Rouge-L, or code similarity); *benchmark:* eight model pairs on three datasets; *vs:* CacheBlend. ✅
- **Limitations:** DroidSpeak does not support KV cache sharing across LLMs originating from different foundation models. It may suffer from quality degradation if real test data drifts significantly from the training data used for offline profiling, and its adaptation algorithm does not currently consider changes in network bandwidth.
- **Tier:** `latent`

## 17. Beyond Natural Language: LLMs Leveraging Alternative Formats for Enhanced Reasoning and Communication

[arXiv:2402.18439](https://arxiv.org/abs/2402.18439) · `models/gemini-3.5-flash`

- **Problem:** Natural language is traditionally the default format for LLM reasoning and multi-agent communication, but its inherent ambiguities and redundancies can limit efficiency and precision.
- **Mechanism:** Agents exchange structured, non-natural language formats (such as JSON, markdown tables, lists, logical expressions, or pseudocode) instead of natural language. This is achieved by adding an instruction (AutoForm prompt) that directs the LLMs to autonomously select, devise, and utilize the most suitable non-NL format for the task.
- **Access needed:** api-tokens — Only text input and output via an API are required to send prompts and receive formatted responses.
- **Measured gains:**
  - Allowing LLMs to autonomously select the most suitable format leads to a 3.3 to 5.7% improvement in reasoning efficiency — *metric:* reasoning efficiency; *benchmark:* single-LLM reasoning tasks (Logic Grid Puzzle, Coin Flip, Information Essentiality, Minute Mysteries QA, AQuA); *vs:* plain CoT. ✅
  - AutoForm achieves up to a 72.7% reduction in token usage in multi-agent communication — *metric:* token reduction; *benchmark:* Hotpot QA dataset with the GPT-4 and GPT-3.5 pairing; *vs:* natural language-based interactions. ✅
  - AutoForm yields an overall average performance boost of 5.4% for GPT-3.5 — *metric:* overall average performance boost; *benchmark:* single-LLM reasoning tasks; *vs:* CoT. ✅
  - AutoForm achieves an average performance enhancement of 5.7% for Gemini Pro — *metric:* average performance enhancement; *benchmark:* single-LLM reasoning tasks; *vs:* CoT. ✅
  - AutoForm achieves an average performance uplift of 3.3% for GPT-4 — *metric:* average performance uplift; *benchmark:* single-LLM reasoning tasks; *vs:* CoT. ✅
  - GPT-3.5 accuracy on Coin Flip escalates from 22.2% to 38.0% with AutoForm — *metric:* accuracy; *benchmark:* Coin Flip dataset; *vs:* CoT. ✅
- **Limitations:** The scope of alternative formats explored is not exhaustive, and the effectiveness of format generalization varies depending on task complexity and the specific LLM used. Additionally, less advanced models like GPT-3.5 can produce suboptimal, overly succinct, or hallucinated responses when prompted to use non-NL formats without extra guidance.
- **Tier:** `token`


## Reviewer notes

These are fidelity checks on the summaries, made by the harness (Claude) by reading the summaries and, where noted, the paper text. They are not design judgements.

- **#14 (Beyond Tokens) is a survey.** Its six "gains" are other papers' results as this survey reports them (AC, Interlat, Agent Primitives, RelayCaching, Agent Memory, Mixture of Thoughts); this paper did not measure them. The summariser tiered it `latent`, although its own rule sends surveys to `neither`; it is a survey *of* latent communication.
- **#3 access labels** describe the Bayesian agents in the paper's theory, not access to a deployed LLM. The paper reports no measurements.
- **#2 needs `model-internals`** only because the listener scores candidates by log-probability. The communication itself is text.
- **Validation figures, not efficiency gains:** #5 gains 3–4 (LLM-judge accuracy, annotator agreement) and #12 gain 3 (10,000 requests is the benchmark's size).
- **No usable benchmark named:** #13 gain 1 ("task-agnostic representational paradigm") and #14 gain 1 ("representative benchmark suite") come from abstracts, and the summary names no dataset for either.
- **#17 gain 3** credits the 5.4% to GPT-3.5 though its quote names no model. Checked against the paper text: the paragraph opens "For GPT-3.5" and moves to Gemini Pro only after this sentence, so the attribution is correct.
- **#7 gains 3–4 (ℹ️)** quote bare table rows. The rows exist, but the comparison in the claim was not checked.
- **#1 gains 1 and 3 (⚠️)**: the quotes are verbatim; only the "100-agent" detail is outside them, and the number 100 does appear elsewhere in the paper.

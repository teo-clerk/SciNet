"""The seed tag vocabulary.

The vocabulary is closed by design. An unconstrained LLM asked to tag fifty
papers will produce "deep learning", "Deep Learning", "DL" and "deep-learning"
as four distinct tags, and a filter built on them is worthless. So the model is
given a fixed list and allowed at most a couple of proposals, which land in a
review queue rather than the vocabulary.

The spine is arXiv's category taxonomy — it is the classification scheme most
of this corpus was already filed under — flattened to readable labels and
extended with method and task terms that cut across fields.

It was, for a year, science only. A library that also holds Plato, Mill and
Darwin's *Origin* got them tagged "social-science" and "biology" because those
were the nearest words on offer, and a closed vocabulary is only as good as
what it closes over. The second half of the list is the humanities and the
social sciences, with the methods and the kinds of writing that go with them:
an essay is not a benchmark, and close reading is not an experimental study.
Seeding is idempotent and runs at every tag job, so an existing library picks
the new terms up without a reset.
"""

from __future__ import annotations

from app.models import TagKind

# (slug, label, kind)
SEED_TAGS: tuple[tuple[str, str, str], ...] = (
    # --- domains -------------------------------------------------------
    ("machine-learning", "Machine Learning", TagKind.DOMAIN),
    ("computer-vision", "Computer Vision", TagKind.DOMAIN),
    ("nlp", "Natural Language Processing", TagKind.DOMAIN),
    ("robotics", "Robotics", TagKind.DOMAIN),
    ("information-retrieval", "Information Retrieval", TagKind.DOMAIN),
    ("human-computer-interaction", "Human-Computer Interaction", TagKind.DOMAIN),
    ("distributed-systems", "Distributed Systems", TagKind.DOMAIN),
    ("security-privacy", "Security and Privacy", TagKind.DOMAIN),
    ("databases", "Databases", TagKind.DOMAIN),
    ("programming-languages", "Programming Languages", TagKind.DOMAIN),
    ("computer-graphics", "Computer Graphics", TagKind.DOMAIN),
    ("theory-of-computation", "Theory of Computation", TagKind.DOMAIN),
    ("signal-processing", "Signal Processing", TagKind.DOMAIN),
    ("physics", "Physics", TagKind.DOMAIN),
    ("astronomy", "Astronomy and Astrophysics", TagKind.DOMAIN),
    ("earth-science", "Earth and Environmental Science", TagKind.DOMAIN),
    ("remote-sensing", "Remote Sensing", TagKind.DOMAIN),
    ("climate", "Climate Science", TagKind.DOMAIN),
    ("biology", "Biology", TagKind.DOMAIN),
    ("bioinformatics", "Bioinformatics", TagKind.DOMAIN),
    ("neuroscience", "Neuroscience", TagKind.DOMAIN),
    ("medicine", "Medicine and Clinical Research", TagKind.DOMAIN),
    ("chemistry", "Chemistry", TagKind.DOMAIN),
    ("materials-science", "Materials Science", TagKind.DOMAIN),
    ("mathematics", "Mathematics", TagKind.DOMAIN),
    ("statistics", "Statistics", TagKind.DOMAIN),
    ("economics", "Economics and Finance", TagKind.DOMAIN),
    ("social-science", "Social Science", TagKind.DOMAIN),
    # --- methods -------------------------------------------------------
    ("deep-learning", "Deep Learning", TagKind.METHOD),
    ("transformers", "Transformer Architectures", TagKind.METHOD),
    ("graph-neural-networks", "Graph Neural Networks", TagKind.METHOD),
    ("reinforcement-learning", "Reinforcement Learning", TagKind.METHOD),
    ("self-supervised-learning", "Self-Supervised Learning", TagKind.METHOD),
    ("bayesian-methods", "Bayesian Methods", TagKind.METHOD),
    ("optimization", "Optimization", TagKind.METHOD),
    ("causal-inference", "Causal Inference", TagKind.METHOD),
    ("dimensionality-reduction", "Dimensionality Reduction", TagKind.METHOD),
    ("simulation", "Simulation and Numerical Modelling", TagKind.METHOD),
    ("experimental-study", "Experimental Study", TagKind.METHOD),
    ("theoretical-analysis", "Theoretical Analysis", TagKind.METHOD),
    # --- tasks ---------------------------------------------------------
    ("classification", "Classification", TagKind.TASK),
    ("segmentation", "Segmentation", TagKind.TASK),
    ("generation", "Generative Modelling", TagKind.TASK),
    ("forecasting", "Forecasting and Time Series", TagKind.TASK),
    ("recommendation", "Recommendation", TagKind.TASK),
    ("question-answering", "Question Answering", TagKind.TASK),
    ("anomaly-detection", "Anomaly Detection", TagKind.TASK),
    ("representation-learning", "Representation Learning", TagKind.TASK),
    # --- artifacts -----------------------------------------------------
    ("survey", "Survey or Review", TagKind.ARTIFACT),
    ("benchmark", "Benchmark or Dataset", TagKind.ARTIFACT),
    ("software-tool", "Software or Tool", TagKind.ARTIFACT),
    ("position-paper", "Position or Opinion Paper", TagKind.ARTIFACT),
    ("case-study", "Case Study", TagKind.ARTIFACT),
    ("reproducibility", "Reproducibility Study", TagKind.ARTIFACT),
    # --- the humanities and the social sciences ---------------------------
    # Appended, never interleaved: existing rows keep their ids, and the
    # PaperTag rows that point at them stay valid.
    ("philosophy", "Philosophy", TagKind.DOMAIN),
    ("ethics", "Ethics and Moral Philosophy", TagKind.DOMAIN),
    ("epistemology", "Epistemology", TagKind.DOMAIN),
    ("metaphysics", "Metaphysics", TagKind.DOMAIN),
    ("logic", "Logic", TagKind.DOMAIN),
    ("philosophy-of-mind", "Philosophy of Mind", TagKind.DOMAIN),
    ("philosophy-of-science", "Philosophy of Science", TagKind.DOMAIN),
    ("history", "History", TagKind.DOMAIN),
    ("history-of-science", "History of Science", TagKind.DOMAIN),
    ("literature", "Literature", TagKind.DOMAIN),
    ("literary-theory", "Literary Theory and Criticism", TagKind.DOMAIN),
    ("linguistics", "Linguistics", TagKind.DOMAIN),
    ("psychology", "Psychology", TagKind.DOMAIN),
    ("cognitive-science", "Cognitive Science", TagKind.DOMAIN),
    ("political-theory", "Political Theory", TagKind.DOMAIN),
    ("sociology", "Sociology", TagKind.DOMAIN),
    ("anthropology", "Anthropology", TagKind.DOMAIN),
    ("religion-studies", "Religion and Theology", TagKind.DOMAIN),
    ("classics", "Classics and Ancient World", TagKind.DOMAIN),
    ("art-and-design", "Art and Design", TagKind.DOMAIN),
    ("music", "Music", TagKind.DOMAIN),
    ("education", "Education", TagKind.DOMAIN),
    ("law", "Law", TagKind.DOMAIN),
    ("cultural-studies", "Cultural Studies", TagKind.DOMAIN),
    ("media-studies", "Media and Communication", TagKind.DOMAIN),
    ("archaeology", "Archaeology", TagKind.DOMAIN),
    ("geography", "Geography", TagKind.DOMAIN),
    # --- how humanists work ---------------------------------------------
    ("close-reading", "Close Reading", TagKind.METHOD),
    ("historical-analysis", "Historical Analysis", TagKind.METHOD),
    ("conceptual-analysis", "Conceptual Analysis", TagKind.METHOD),
    ("argumentation", "Argument and Dialectic", TagKind.METHOD),
    ("ethnography", "Ethnography and Fieldwork", TagKind.METHOD),
    ("textual-criticism", "Textual Criticism", TagKind.METHOD),
    ("qualitative-study", "Qualitative Study", TagKind.METHOD),
    ("comparative-analysis", "Comparative Analysis", TagKind.METHOD),
    ("thought-experiment", "Thought Experiment", TagKind.METHOD),
    # --- the kinds of writing -------------------------------------------
    ("essay", "Essay", TagKind.ARTIFACT),
    ("treatise", "Treatise or Monograph", TagKind.ARTIFACT),
    ("lecture", "Lecture", TagKind.ARTIFACT),
    ("commentary", "Commentary", TagKind.ARTIFACT),
    ("dialogue", "Dialogue", TagKind.ARTIFACT),
    ("primary-source", "Primary Source", TagKind.ARTIFACT),
    ("textbook", "Textbook or Introduction", TagKind.ARTIFACT),
    ("book-chapter", "Book Chapter", TagKind.ARTIFACT),
    ("letter-or-memoir", "Letter, Diary or Memoir", TagKind.ARTIFACT),
)

MAX_TAGS_PER_PAPER = 6
MAX_PROPOSED_TAGS = 2
#: Cosine similarity above which a proposed tag is treated as an alias of an
#: existing one rather than a new concept.
MERGE_SIMILARITY = 0.90


def seed_slugs() -> list[str]:
    return [slug for slug, _, _ in SEED_TAGS]

"""The seed tag vocabulary.

The vocabulary is closed by design. An unconstrained LLM asked to tag fifty
papers will produce "deep learning", "Deep Learning", "DL" and "deep-learning"
as four distinct tags, and a filter built on them is worthless. So the model is
given a fixed list and allowed at most a couple of proposals, which land in a
review queue rather than the vocabulary.

The spine is arXiv's category taxonomy — it is the classification scheme most
of this corpus was already filed under — flattened to readable labels and
extended with method and task terms that cut across fields.
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
)

MAX_TAGS_PER_PAPER = 6
MAX_PROPOSED_TAGS = 2
#: Cosine similarity above which a proposed tag is treated as an alias of an
#: existing one rather than a new concept.
MERGE_SIMILARITY = 0.90


def seed_slugs() -> list[str]:
    return [slug for slug, _, _ in SEED_TAGS]

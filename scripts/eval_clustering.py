#!/usr/bin/env python
"""Score the map against known ground truth.

This corpus is filed by domain — `Astrophysics_0812.4574v2.pdf`, `Robotics_...`
— so for once the right answer is known. That turns "do the clusters look
sensible?" into a measurement.

Three numbers, because they fail differently:

* **Adjusted Rand index** — agreement with the true partition, corrected for
  chance. 0 is random, 1 is exact.
* **Homogeneity** — does each cluster contain a single domain? A cautious
  clusterer that splits every domain in two still scores 1.0 here.
* **Completeness** — is each domain kept in a single cluster? A clusterer that
  lumps everything together scores 1.0 here.

Only the pair together says anything: high homogeneity with low completeness
means over-fragmentation, the reverse means over-merging.

    uv run python ../scripts/eval_clustering.py
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.db import session_scope  # noqa: E402
from app.models import Cluster, Paper, Projection, ProjectionRun  # noqa: E402

NOISE = -1
# `Genomics_and_Bio_2401.12345v1.pdf` -> `Genomics_and_Bio`
DOMAIN_RE = re.compile(r"^(.*?)_\d")


def domain_of(pdf_path: str) -> str | None:
    match = DOMAIN_RE.match(Path(pdf_path).name)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, help="projection run (default: active)")
    args = parser.parse_args()

    with session_scope() as session:
        run = (
            session.get(ProjectionRun, args.run)
            if args.run
            else session.query(ProjectionRun)
            .filter(ProjectionRun.is_active.is_(True))
            .one_or_none()
        )
        if run is None:
            print("no projection run to evaluate")
            return 1

        rows = (
            session.query(Projection, Paper)
            .join(Paper, Paper.id == Projection.paper_id)
            .filter(Projection.run_id == run.id)
            .all()
        )
        clusters = {
            c.id: c for c in session.query(Cluster).filter(Cluster.run_id == run.id)
        }

        truth: list[str] = []
        predicted: list[int] = []
        unlabelled = 0
        for projection, paper in rows:
            domain = domain_of(paper.pdf_path)
            if domain is None:
                unlabelled += 1
                continue
            truth.append(domain)
            predicted.append(projection.cluster_id if projection.cluster_id else NOISE)

        if not truth:
            print("no ground-truth labels recoverable from filenames")
            return 1

        print(f"run {run.id} ({run.method}), {len(truth)} labelled papers")
        if unlabelled:
            print(f"  {unlabelled} paper(s) had no recoverable domain, excluded")
        print(f"  {len(clusters)} cluster(s) found, {len(set(truth))} true domains")

        noise = sum(1 for p in predicted if p == NOISE)
        print(
            f"  {noise} paper(s) "
            f"({100 * noise / len(predicted):.0f}%) unclustered as noise"
        )
        print()

        # --- scores ------------------------------------------------------
        from sklearn import metrics

        ari = metrics.adjusted_rand_score(truth, predicted)
        homogeneity, completeness, v_measure = (
            metrics.homogeneity_completeness_v_measure(truth, predicted)
        )
        print("agreement with ground truth")
        print(f"  adjusted Rand index : {ari:.3f}")
        print(f"  homogeneity         : {homogeneity:.3f}  (one domain per cluster)")
        print(f"  completeness        : {completeness:.3f}  (one cluster per domain)")
        print(f"  V-measure           : {v_measure:.3f}")
        print()

        # Scored again ignoring noise, since HDBSCAN declining to classify a
        # paper is a different failure from misclassifying it.
        kept = [(t, p) for t, p in zip(truth, predicted, strict=True) if p != NOISE]
        if kept and len(kept) < len(truth):
            t2, p2 = zip(*kept, strict=True)
            print(f"excluding noise ({len(kept)} papers)")
            print(f"  adjusted Rand index : {metrics.adjusted_rand_score(t2, p2):.3f}")
            print()

        # --- what each cluster actually contains -------------------------
        by_cluster: dict[int, collections.Counter[str]] = collections.defaultdict(
            collections.Counter
        )
        for t, p in zip(truth, predicted, strict=True):
            by_cluster[p][t] += 1

        print(f"{'cluster':<9} {'n':>4}  {'purity':>6}  name / dominant domain")
        print("-" * 78)
        for cluster_id in sorted(
            by_cluster, key=lambda c: -sum(by_cluster[c].values())
        ):
            counts = by_cluster[cluster_id]
            total = sum(counts.values())
            dominant, n = counts.most_common(1)[0]
            purity = n / total
            name = (
                "(noise)"
                if cluster_id == NOISE
                else (
                    clusters.get(cluster_id).llm_label
                    if clusters.get(cluster_id)
                    else None
                )
                or "(unnamed)"
            )
            mix = ", ".join(f"{d}:{c}" for d, c in counts.most_common(3))
            label = f"{cluster_id:<9}" if cluster_id != NOISE else f"{'noise':<9}"
            print(f"{label} {total:>4}  {purity:>6.2f}  {name}")
            print(f"{'':<9} {'':>4}  {'':>6}  └ {mix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"CPU implicit ALS, lexical/semantic content, and explicit normalized hybrid signals."

import hashlib
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits

from cmdl.quality.checks import assess, duplicate_keys

WEIGHTS = {"VIEW": 1, "LIKE": 2, "BOOKMARK": 3, "FOLLOW": 4, "COMMENT CREATED": 5}
MODELS = ("popularity", "tfidf", "als", "fixed_hybrid", "semantic", "quality_hybrid")
SEMANTIC_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SEMANTIC_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
PROTOCOL = "deskdrop-temporal-v2-min5-user-item"
_ENCODER = None


def records_from_snapshot(snapshot):
    records = {
        str(a.metadata.metadata_version_id): asdict(a.metadata)
        for a in snapshot.articles
        if a.metadata
    }
    records.update(
        {str(e.metadata.metadata_version_id): asdict(e.metadata) for e in snapshot.history}
    )
    for version, record in records.items():
        record["metadata_version_id"] = version
        for key in ("share_event_id", "shared_at"):
            record[key] = str(record[key])
    return records


def five_core(pairs, minimum=5):
    "Training-only bipartite five-distinct-partner core; never filter content candidates."
    kept = dict(pairs)
    while kept:
        users = Counter(u for u, _ in kept)
        items = Counter(i for _, i in kept)
        following = {
            (u, i): w for (u, i), w in kept.items() if users[u] >= minimum and items[i] >= minimum
        }
        if len(following) == len(kept):
            break
        kept = following
    return kept


def implicit_als(matrix, factors=24, iterations=10, alpha=20.0, regularization=0.1, seed=42):
    "Hu-style implicit objective: p=1 observed, c=1+alpha*log1p(weight), c=1 otherwise."
    rng = np.random.default_rng(seed)
    users = rng.normal(0, 0.01, (matrix.shape[0], factors))
    items = rng.normal(0, 0.01, (matrix.shape[1], factors))
    identity = regularization * np.eye(factors)
    with threadpool_limits(limits=1):
        for _ in range(iterations):
            for data, fixed, target in ((matrix, items, users), (matrix.T.tocsr(), users, items)):
                gram = fixed.T @ fixed + identity
                for row in range(data.shape[0]):
                    start, end = data.indptr[row : row + 2]
                    indices = data.indices[start:end]
                    if not len(indices):
                        target[row] = 0
                        continue
                    vectors = fixed[indices]
                    confidence_extra = alpha * data.data[start:end]
                    target[row] = np.linalg.solve(
                        gram + (vectors.T * confidence_extra) @ vectors,
                        vectors.T @ (1 + confidence_extra),
                    )
    return users.astype("float32"), items.astype("float32")


def scaled(values):
    positive = np.maximum(np.asarray(values).reshape(-1), 0)
    return positive / positive.max() if positive.size and positive.max() > 0 else positive


class Ranker:
    def __init__(self, snapshot, *, seed=42, alpha=0.5, semantic=False, records=None):
        self.snapshot_id = str(snapshot.snapshot_id)
        self.cutoff = snapshot.as_of_at.isoformat()
        self.alpha = alpha
        self.seed = seed
        self.ids = [a.content_id for a in snapshot.candidates()]
        self.position = {item: i for i, item in enumerate(self.ids)}
        self.records = records or records_from_snapshot(snapshot)
        self.versions = {
            a.content_id: str(a.metadata.metadata_version_id) for a in snapshot.candidates()
        }
        self.catalog = {i: self.records[self.versions[i]] for i in self.ids}
        self.consumed = defaultdict(set)
        for person, item in snapshot.consumed_pairs:
            self.consumed[person].add(item)
        self.history = defaultdict(list)
        pairs = defaultdict(float)
        version_ids = sorted(self.records)
        version_pos = {v: i for i, v in enumerate(version_ids)}
        documents = [self.document(self.records[v]) for v in version_ids]
        self.vectorizer = TfidfVectorizer(
            max_features=30000, sublinear_tf=True, ngram_range=(1, 2), strip_accents="unicode"
        )
        try:
            features = self.vectorizer.fit_transform(documents)
        except ValueError:
            self.vectorizer.fit(["empty corpus placeholder"])
            features = (
                self.vectorizer.transform(documents) if documents else sparse.csr_matrix((0, 3))
            )
        self.content = features[[version_pos[self.versions[i]] for i in self.ids]].tocsr()
        self.profiles = {}
        for event in snapshot.history:
            weight = WEIGHTS[event.event_type]
            pairs[event.person_id, event.content_id] += weight
            self.history[event.person_id].append((str(event.metadata.metadata_version_id), weight))
        core = five_core(pairs)
        self.warm_users = {u for u, _ in core}
        self.support_counts = {
            "raw_pairs": len(pairs),
            "core_pairs": len(core),
            "raw_users": len(self.history),
            "core_users": len(self.warm_users),
            "core_items": len({i for _, i in core}),
        }
        for person in self.history:
            self.history[person] = [
                (v, w)
                for v, w in self.history[person]
                if (person, self.records[v]["content_id"]) in core
            ]
        for person, history in self.history.items():
            weighted = sum(
                (features[version_pos[v]] * w for v, w in history),
                sparse.csr_matrix((1, features.shape[1])),
            )
            self.profiles[person] = normalize(weighted).tocsr()
        self.als_users = {u: n for n, u in enumerate(sorted({u for u, _ in core}))}
        self.als_items = {i: n for n, i in enumerate(sorted({i for _, i in core}))}
        rows, columns, values = [], [], []
        for (person, item), strength in core.items():
            rows.append(self.als_users[person])
            columns.append(self.als_items[item])
            values.append(np.log1p(strength))
        matrix = sparse.csr_matrix(
            (values, (rows, columns)), shape=(len(self.als_users), len(self.als_items))
        )
        self.user_factors, self.item_factors = implicit_als(matrix, seed=seed)
        self.candidate_factors = np.zeros((len(self.ids), 24), dtype="float32")
        for item, index in self.position.items():
            if item in self.als_items:
                self.candidate_factors[index] = self.item_factors[self.als_items[item]]
        popularity = Counter()
        for (_, item), value in core.items():
            popularity[item] += np.log1p(value)
        self.popularity = scaled([popularity[i] for i in self.ids])
        duplicates = duplicate_keys(self.catalog.values())
        self.quality = {i: assess(r, duplicates) for i, r in self.catalog.items()}
        self.semantic = None
        self.semantic_profiles = {}
        self.semantic_manifest = None
        if semantic:
            self.fit_semantic(version_ids, documents, version_pos)
        self.cache = {}

    @staticmethod
    def document(record):
        return (record.get("title", "") + " ") * 2 + record.get("text", "")

    def fit_semantic(self, versions, documents, positions):
        global _ENCODER
        import faiss
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(2)
        if _ENCODER is None:
            _ENCODER = SentenceTransformer(
                SEMANTIC_MODEL,
                device="cpu",
                revision=os.environ.get("CMDL_EMBEDDING_REVISION", SEMANTIC_REVISION),
            )
        encoder = _ENCODER
        encoder.max_seq_length = 128
        cache = Path("artifacts/embedding-cache")
        cache.mkdir(parents=True, exist_ok=True)
        from huggingface_hub import snapshot_download

        cached_path = snapshot_download(
            SEMANTIC_MODEL,
            local_files_only=True,
            revision=os.environ.get("CMDL_EMBEDDING_REVISION", SEMANTIC_REVISION),
        )
        revision = Path(cached_path).name
        paths = [
            cache / (hashlib.sha256((str(revision) + d[:2400]).encode()).hexdigest() + ".npy")
            for d in documents
        ]
        missing = {path: documents[i][:2400] for i, path in enumerate(paths) if not path.exists()}
        if missing:
            encoded = encoder.encode(
                list(missing.values()),
                batch_size=32,
                normalize_embeddings=True,
                show_progress_bar=True,
            )
            for path, vector in zip(missing, encoded, strict=True):
                np.save(path, vector, allow_pickle=False)
        vectors = np.stack([np.load(path, allow_pickle=False) for path in paths])
        self.semantic = np.asarray(
            vectors[[positions[self.versions[i]] for i in self.ids]], dtype="float32"
        )
        self.index = faiss.IndexFlatIP(self.semantic.shape[1])
        self.index.add(self.semantic)
        for person, history in self.history.items():
            profile = sum(
                (vectors[positions[v]] * w for v, w in history),
                np.zeros(vectors.shape[1], dtype="float32"),
            )
            self.semantic_profiles[person] = profile / max(1e-12, np.linalg.norm(profile))
        self.semantic_manifest = {
            "model": SEMANTIC_MODEL,
            "max_seq_length": 128,
            "revision": revision,
            "retrieval": "FAISS IndexFlatIP; normalized vectors",
        }

    def rebuild_content(self, records, *, semantic=False):
        "Corruption keeps interaction histories, ALS, item IDs and candidate membership fixed."
        import copy

        changed = copy.copy(self)
        changed.records = records
        changed.catalog = {i: records[self.versions[i]] for i in self.ids}
        versions = sorted(records)
        positions = {v: i for i, v in enumerate(versions)}
        docs = [self.document(records[v]) for v in versions]
        changed.vectorizer = TfidfVectorizer(
            max_features=30000, sublinear_tf=True, ngram_range=(1, 2), strip_accents="unicode"
        )
        try:
            features = changed.vectorizer.fit_transform(docs)
        except ValueError:
            changed.vectorizer.fit(["empty corpus placeholder"])
            features = changed.vectorizer.transform(docs)
        changed.content = features[[positions[self.versions[i]] for i in self.ids]].tocsr()
        changed.profiles = {}
        for person, history in self.history.items():
            profile = sum(
                (features[positions[v]] * w for v, w in history),
                sparse.csr_matrix((1, features.shape[1])),
            )
            changed.profiles[person] = normalize(profile).tocsr()
        duplicates = duplicate_keys(changed.catalog.values())
        changed.quality = {i: assess(r, duplicates) for i, r in changed.catalog.items()}
        changed.cache = {}
        if semantic:
            changed.fit_semantic(versions, docs, positions)
        return changed

    def signals(self, person=None, current=None, recent=()):
        profile = self.profiles.get(person) if person in self.warm_users else None
        context_items = (
            [i for i in recent if i in self.position][-20:] if person is not None else []
        )
        if current in self.position:
            context_items += [current]
        if context_items:
            context = normalize(
                sparse.csr_matrix(
                    self.content[[self.position[i] for i in context_items]].sum(axis=0)
                )
            )
            profile = normalize(profile * 0.5 + context * 0.5) if profile is not None else context
        content = (
            scaled((self.content @ profile.T).toarray())
            if profile is not None
            else np.zeros(len(self.ids))
        )
        factors = self.user_factors[self.als_users[person]] if person in self.als_users else None
        if context_items:
            context = self.candidate_factors[[self.position[i] for i in context_items]].mean(axis=0)
            if np.linalg.norm(context) > 0:
                factors = 0.7 * factors + 0.3 * context if factors is not None else context
        collaborative = (
            scaled(self.candidate_factors @ factors)
            if factors is not None
            else np.zeros(len(self.ids))
        )
        semantic_scores = np.zeros(len(self.ids))
        if self.semantic is not None:
            query = self.semantic_profiles.get(person) if person in self.warm_users else None
            if context_items:
                context = self.semantic[[self.position[i] for i in context_items]].mean(axis=0)
                query = 0.5 * query + 0.5 * context if query is not None else context
            if query is not None:
                query = np.asarray(query / max(1e-12, np.linalg.norm(query)), dtype="float32")[
                    None, :
                ]
                distances, indices = self.index.search(query, len(self.ids))
                semantic_scores[indices[0]] = np.maximum(distances[0], 0)
                semantic_scores = scaled(semantic_scores)
        return content, collaborative, semantic_scores

    def rank(
        self, person=None, *, current=None, recent=(), excluded=(), mode="quality_hybrid", limit=50
    ):
        if mode not in MODELS:
            raise ValueError("Unknown ranking mode")
        content, collaborative, semantic = self.signals(person, current, recent)
        if mode == "semantic" and self.semantic is None:
            raise ValueError("Semantic model was not trained")
        q = np.array([self.quality.get(i, {}).get("content_reliability", 0.25) for i in self.ids])
        branch = 0.5 * content + 0.5 * semantic if self.semantic is not None else content
        weight = self.alpha if collaborative.max(initial=0) > 0 else 0
        collaborative_part = weight * collaborative
        content_part = (1 - weight) * branch * (q if mode == "quality_hybrid" else 1)
        if mode == "popularity":
            scores = self.popularity.copy()
        elif mode == "tfidf":
            scores = content.copy()
        elif mode == "als":
            scores = collaborative.copy()
        elif mode == "semantic":
            scores = semantic.copy()
        else:
            scores = collaborative_part + content_part
        primary = scores > 1e-12
        # Explicit fallback, not a fabricated content/collaborative explanation.
        scores = np.where(primary, scores + 0.001 * self.popularity, 0.0001 * self.popularity)
        banned = set(excluded) | self.consumed.get(person, set()) | set(recent)
        if current:
            banned.add(current)
        ordered = np.argsort(-scores, kind="stable")
        result = []
        for index in ordered:
            item = self.ids[index]
            if item in banned:
                continue
            signal = {
                "tfidf": float(content[index]),
                "collaborative": float(collaborative[index]),
                "semantic": float(semantic[index]),
                "content_reliability": float(q[index]),
                "collaborative_contribution": float(collaborative_part[index]),
                "content_contribution": float(content_part[index]),
            }
            if not primary[index] or mode == "popularity":
                text, code = "Popular in the permitted historical interaction data.", "popularity"
            elif mode == "als" or (
                mode.endswith("hybrid") and collaborative_part[index] > content_part[index]
            ):
                text, code = (
                    "Estimated affinity from historical reading patterns.",
                    "collaborative",
                )
            else:
                using_history = (
                    person in self.warm_users
                    or person is not None
                    and any(r != current and r in self.position for r in recent)
                )
                text = "Text similarity to " + (
                    "this article and your reading history."
                    if current and using_history
                    else "this article."
                    if current
                    else "your reading history."
                )
                code = (
                    "semantic_similarity"
                    if (
                        mode == "semantic"
                        or mode.endswith("hybrid")
                        and semantic[index] > content[index]
                    )
                    else "content_similarity"
                )
                if code == "semantic_similarity":
                    text = "Semantic " + text[0].lower() + text[1:]
            result.append(
                {
                    "content_id": item,
                    "score": float(scores[index]),
                    "explanation": {"code": code, "text": text},
                    "source": "popularity" if code == "popularity" else "primary",
                    "signals": signal,
                }
            )
            if len(result) >= limit:
                break
        return result

    def prepare(self):
        for person in sorted(self.consumed):
            self.cache[person] = self.rank(person, limit=50)
        return self.cache

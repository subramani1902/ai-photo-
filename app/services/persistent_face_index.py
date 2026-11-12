import json
from pathlib import Path

import faiss
import numpy as np

from app.database.db import SessionLocal
from app.database.models import Face, Photo


class PersistentFaceIndex:

    def __init__(self):

        self.index_dir = Path("indexes")
        self.dimension = 512

        self.index = None
        self.face_ids = []
        self.face_quality = []

        # Keep poor-quality faces in the index initially.
        # Change to False only after evaluating real photographs.
        self.include_poor_quality = True

    # --------------------------------------------------
    # READ OPTIONAL QUALITY METADATA
    # --------------------------------------------------

    def load_quality_metadata(self, embedding_path):

        embedding_path = Path(embedding_path)

        # Example:
        # photo_1_face_1.npy
        # photo_1_face_1.json

        metadata_path = embedding_path.with_suffix(".json")

        if not metadata_path.exists():

            return {
                "quality_status": "unknown",
                "quality_issues": []
            }

        try:

            with metadata_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                metadata = json.load(file)

            quality = metadata.get("quality", {})

            return {
                "quality_status": quality.get(
                    "quality_status",
                    "unknown"
                ),
                "quality_issues": quality.get(
                    "quality_issues",
                    []
                )
            }

        except (OSError, json.JSONDecodeError):

            return {
                "quality_status": "unknown",
                "quality_issues": ["Metadata unavailable"]
            }

    # --------------------------------------------------
    # BUILD EVENT INDEX
    # --------------------------------------------------

    def build_event_index(self, event_id):

        db = SessionLocal()

        try:

            records = (
                db.query(Face, Photo)
                .join(
                    Photo,
                    Face.photo_id == Photo.id
                )
                .filter(
                    Photo.event_id == event_id
                )
                .order_by(Face.id)
                .all()
            )

            embeddings = []
            face_ids = []
            face_quality = []

            indexed_good = 0
            indexed_poor = 0
            indexed_unknown = 0

            skipped_invalid = 0
            skipped_poor = 0

            for face, photo in records:

                try:

                    # Load embedding
                    vector = np.load(
                        face.embedding_path,
                        allow_pickle=False
                    )

                    vector = np.asarray(
                        vector,
                        dtype=np.float32
                    ).reshape(-1)

                    # Validate dimensions
                    if vector.shape != (self.dimension,):

                        print(
                            "Skipping invalid embedding:",
                            face.id
                        )

                        skipped_invalid += 1
                        continue

                    # Validate numeric values
                    if not np.isfinite(vector).all():

                        print(
                            "Skipping invalid values:",
                            face.id
                        )

                        skipped_invalid += 1
                        continue

                    # Validate magnitude
                    if np.linalg.norm(vector) == 0:

                        print(
                            "Skipping zero embedding:",
                            face.id
                        )

                        skipped_invalid += 1
                        continue

                    # Read optional quality information
                    quality = self.load_quality_metadata(
                        face.embedding_path
                    )

                    quality_status = quality["quality_status"]

                    # Optionally exclude poor-quality faces
                    if (
                        quality_status == "poor"
                        and not self.include_poor_quality
                    ):

                        skipped_poor += 1
                        continue

                    embeddings.append(vector)
                    face_ids.append(face.id)

                    face_quality.append({

                        "face_id": face.id,

                        "photo_id": photo.id,

                        "quality_status": quality_status,

                        "quality_issues": quality["quality_issues"]

                    })

                    if quality_status == "good":

                        indexed_good += 1

                    elif quality_status == "poor":

                        indexed_poor += 1

                    else:

                        indexed_unknown += 1

                except (OSError, ValueError) as error:

                    print(
                        f"Skipping face {face.id}: {error}"
                    )

                    skipped_invalid += 1

            matrix = (
                np.vstack(embeddings).astype(np.float32)
                if embeddings
                else np.empty((0, self.dimension), dtype=np.float32)
            )
            if embeddings:
                faiss.normalize_L2(matrix)

            # Create FAISS index
            index = faiss.IndexFlatIP(
                self.dimension
            )

            if embeddings:
                index.add(matrix)

            self.index_dir.mkdir(
                parents=True,
                exist_ok=True
            )

            index_path = (
                self.index_dir /
                f"event_{event_id}.faiss"
            )

            metadata_path = (
                self.index_dir /
                f"event_{event_id}.json"
            )

            temp_index = Path(
                str(index_path) + ".tmp"
            )

            temp_metadata = Path(
                str(metadata_path) + ".tmp"
            )

            # Write index
            faiss.write_index(
                index,
                str(temp_index)
            )

            # Save metadata
            metadata = {

                "event_id": event_id,

                "dimension": self.dimension,

                "total_embeddings": len(face_ids),

                "face_ids": face_ids,

                "face_quality": face_quality,

                "quality_summary": {

                    "indexed_good": indexed_good,

                    "indexed_poor": indexed_poor,

                    "indexed_unknown": indexed_unknown,

                    "skipped_poor": skipped_poor,

                    "skipped_invalid": skipped_invalid

                },

                "include_poor_quality": self.include_poor_quality

            }

            with temp_metadata.open(
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    metadata,
                    file,
                    indent=4
                )

            # Replace previous index files
            temp_index.replace(index_path)
            temp_metadata.replace(metadata_path)

            print("\n========== FAISS INDEX BUILT ==========")

            print("Event ID:", event_id)

            print("Total indexed faces:", len(face_ids))

            print("Good-quality faces:", indexed_good)

            print("Poor-quality faces:", indexed_poor)

            print("Unknown-quality faces:", indexed_unknown)

            print("Skipped poor-quality faces:", skipped_poor)

            print("Invalid embeddings:", skipped_invalid)

            print("Index:", index_path)

            print("Metadata:", metadata_path)

            return len(face_ids)

        finally:

            db.close()

    # --------------------------------------------------
    # LOAD EVENT INDEX
    # --------------------------------------------------

    def load_event_index(self, event_id):

        index_path = (
            self.index_dir /
            f"event_{event_id}.faiss"
        )

        metadata_path = (
            self.index_dir /
            f"event_{event_id}.json"
        )

        if (
            not index_path.exists()
            or not metadata_path.exists()
        ):

            raise FileNotFoundError(
                "FAISS index or metadata file not found."
            )

        with metadata_path.open(
            "r",
            encoding="utf-8"
        ) as file:

            metadata = json.load(file)

        if metadata["dimension"] != self.dimension:

            raise ValueError(
                "Index dimension mismatch."
            )

        index = faiss.read_index(
            str(index_path)
        )

        face_ids = metadata["face_ids"]

        if index.ntotal != len(face_ids):

            raise ValueError(
                "Index and metadata count mismatch."
            )

        if "face_quality" in metadata:

            face_quality = metadata["face_quality"]

        else:

            # Compatibility with older index metadata
            face_quality = [
                {
                    "face_id": face_id,
                    "quality_status": "unknown",
                    "quality_issues": []
                }
                for face_id in face_ids
            ]

        self.index = index
        self.face_ids = face_ids
        self.face_quality = face_quality

        print("\nFAISS index loaded successfully.")

        print(
            "Total indexed faces:",
            self.index.ntotal
        )

    # --------------------------------------------------
    # SEARCH SIMILAR FACES
    # --------------------------------------------------

    def search(self, query_embedding, top_k=10):

        if self.index is None:

            raise RuntimeError(
                "Load an event index first."
            )

        if self.index.ntotal == 0:
            return []

        query = np.asarray(
            query_embedding,
            dtype=np.float32
        ).reshape(1, -1)

        if query.shape[1] != self.dimension:

            raise ValueError(
                "Invalid query embedding dimension."
            )

        if not np.isfinite(query).all():

            raise ValueError(
                "Invalid query embedding values."
            )

        if np.linalg.norm(query) == 0:

            raise ValueError(
                "Query embedding has zero magnitude."
            )

        # Normalize query embedding
        faiss.normalize_L2(query)

        # Search FAISS index
        scores, positions = self.index.search(
            query,
            min(top_k, self.index.ntotal)
        )

        results = []

        for score, position in zip(
            scores[0],
            positions[0]
        ):

            if position < 0:
                continue

            quality_info = self.face_quality[position]

            results.append({

                "face_id": self.face_ids[position],

                "similarity": float(score),

                "quality_status": quality_info.get(
                    "quality_status",
                    "unknown"
                )

            })

        return results

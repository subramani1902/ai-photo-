import csv
import json
from pathlib import Path

import cv2


REPORT_PATH = Path(
    "storage/events/wedding_001/processed/batch_results.json"
)

OUTPUT_PATH = Path(
    "storage/events/wedding_001/reports/validation_report.csv"
)


def validate_dataset():

    if not REPORT_PATH.exists():
        raise FileNotFoundError(
            f"Batch report not found: {REPORT_PATH}"
        )

    with REPORT_PATH.open("r", encoding="utf-8") as file:
        report = json.load(file)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    results = []

    for photo in report.get("photos", []):

        filename = photo.get("filename", "unknown")
        face_count = len(photo.get("faces", []))
        source_path = Path(photo.get("source_path", ""))

        image = cv2.imread(str(source_path))

        if image is None:
            image_status = "Unreadable"
            width = 0
            height = 0

        else:
            height, width = image.shape[:2]
            image_status = "Readable"

        results.append({
            "filename": filename,
            "image_status": image_status,
            "width": width,
            "height": height,
            "face_count": face_count,
            "validation": (
                "Face detected"
                if face_count > 0
                else "No face detected"
            )
        })

    with OUTPUT_PATH.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "filename",
                "image_status",
                "width",
                "height",
                "face_count",
                "validation"
            ]
        )

        writer.writeheader()
        writer.writerows(results)

    total = len(results)
    detected = sum(r["face_count"] > 0 for r in results)
    no_faces = total - detected
    unreadable = sum(
        r["image_status"] == "Unreadable"
        for r in results
    )
    total_faces = sum(r["face_count"] for r in results)

    print("\n========== DATASET VALIDATION ==========")
    print("Total photographs:", total)
    print("Photographs with faces:", detected)
    print("Photographs without faces:", no_faces)
    print("Unreadable photographs:", unreadable)
    print("Total detected faces:", total_faces)

    print("\n========== PHOTOGRAPHS WITHOUT FACES ==========")

    for result in results:
        if result["face_count"] == 0:
            print(
                result["filename"],
                "|",
                result["width"],
                "x",
                result["height"],
                "|",
                result["image_status"]
            )

    print("\nValidation report saved:")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    validate_dataset()
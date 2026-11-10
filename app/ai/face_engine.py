import cv2
import numpy as np

from insightface.app import FaceAnalysis


MAX_PROCESSING_DIMENSION = 4096


def prepare_image_for_processing(image, max_dimension=MAX_PROCESSING_DIMENSION):
    height, width = image.shape[:2]
    largest_dimension = max(height, width)
    if largest_dimension <= max_dimension:
        return image

    scale = max_dimension / largest_dimension
    resized = cv2.resize(
        image,
        (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_AREA
    )
    return resized


class FaceEngine:

    def __init__(self):

        print("Initializing InsightFace...")

        self.model = FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"]
        )

        self.model.prepare(
            ctx_id=-1,
            det_size=(640, 640)
        )

        # Quality assessment thresholds.
        # These are initial heuristics and require
        # calibration against real event photographs.

        self.min_detection_confidence = 0.50
        self.min_face_width = 40
        self.min_face_height = 40
        self.min_blur_score = 20.0
        self.min_brightness = 30.0
        self.max_brightness = 230.0

        print("InsightFace loaded successfully.")

    # --------------------------------------------------
    # FACE QUALITY ASSESSMENT
    # --------------------------------------------------

    def assess_face_quality(self, image, bbox):

        height, width = image.shape[:2]

        x1, y1, x2, y2 = bbox

        # Keep bounding box inside image boundaries

        x1 = max(0, min(x1, width))
        x2 = max(0, min(x2, width))

        y1 = max(0, min(y1, height))
        y2 = max(0, min(y2, height))

        face_width = x2 - x1
        face_height = y2 - y1

        issues = []

        # Check face dimensions

        if face_width < self.min_face_width:
            issues.append("Face too small")

        if face_height < self.min_face_height:
            issues.append("Face too small")

        if face_width <= 0 or face_height <= 0:

            return {
                "face_width": face_width,
                "face_height": face_height,
                "blur_score": 0.0,
                "brightness": 0.0,
                "quality_status": "poor",
                "quality_issues": ["Invalid face region"]
            }

        face_crop = image[
            y1:y2,
            x1:x2
        ]

        if face_crop.size == 0:

            return {
                "face_width": face_width,
                "face_height": face_height,
                "blur_score": 0.0,
                "brightness": 0.0,
                "quality_status": "poor",
                "quality_issues": ["Empty face region"]
            }

        # Convert face crop to grayscale

        gray = cv2.cvtColor(
            face_crop,
            cv2.COLOR_BGR2GRAY
        )

        # Blur measurement using Laplacian variance

        blur_score = float(
            cv2.Laplacian(
                gray,
                cv2.CV_64F
            ).var()
        )

        if blur_score < self.min_blur_score:
            issues.append("Face may be blurry")

        # Calculate average brightness

        brightness = float(
            np.mean(gray)
        )

        if brightness < self.min_brightness:
            issues.append("Face is too dark")

        elif brightness > self.max_brightness:
            issues.append("Face is overexposed")

        # Determine initial quality status

        if issues:

            quality_status = "poor"

        else:

            quality_status = "good"

        return {

            "face_width": face_width,

            "face_height": face_height,

            "blur_score": round(
                blur_score,
                2
            ),

            "brightness": round(
                brightness,
                2
            ),

            "quality_status": quality_status,

            "quality_issues": issues

        }

    # --------------------------------------------------
    # PROCESS IMAGE
    # --------------------------------------------------

    def process_image(
        self,
        image_path,
        output_path=None
    ):

        image = cv2.imread(
            str(image_path)
        )

        if image is None:

            raise ValueError(
                f"Unable to read image: {image_path}"
            )

        image = prepare_image_for_processing(image)

        detected_faces = self.model.get(
            image
        )

        face_results = []

        for index, face in enumerate(
            detected_faces
        ):

            # ------------------------------------------
            # BOUNDING BOX
            # ------------------------------------------

            bbox = face.bbox.astype(int).tolist()

            x1, y1, x2, y2 = bbox

            # ------------------------------------------
            # DETECTION CONFIDENCE
            # ------------------------------------------

            confidence = float(
                face.det_score
            )

            # ------------------------------------------
            # FACE QUALITY
            # ------------------------------------------

            quality = self.assess_face_quality(
                image,
                bbox
            )

            quality_issues = list(
                quality["quality_issues"]
            )

            if confidence < self.min_detection_confidence:

                quality_issues.append(
                    "Low detection confidence"
                )

            if quality_issues:

                quality_status = "poor"

            else:

                quality_status = "good"

            # ------------------------------------------
            # FACE EMBEDDING
            # ------------------------------------------

            embedding = np.asarray(
                face.normed_embedding,
                dtype=np.float32
            )

            if (
                embedding.ndim != 1
                or embedding.shape[0] != 512
                or not np.isfinite(embedding).all()
                or np.linalg.norm(embedding) == 0
            ):

                continue

            # ------------------------------------------
            # FACE RESULT
            # ------------------------------------------

            face_results.append({

                "face_number": index + 1,

                "bbox": bbox,

                "confidence": confidence,

                "embedding": embedding.tolist(),

                "quality": {

                    "face_width": quality["face_width"],

                    "face_height": quality["face_height"],

                    "blur_score": quality["blur_score"],

                    "brightness": quality["brightness"],

                    "quality_status": quality_status,

                    "quality_issues": quality_issues

                }

            })

            # ------------------------------------------
            # ANNOTATE IMAGE
            # ------------------------------------------

            if output_path:

                color = (
                    (0, 255, 0)
                    if quality_status == "good"
                    else (0, 165, 255)
                )

                cv2.rectangle(
                    image,
                    (x1, y1),
                    (x2, y2),
                    color,
                    2
                )

                label = (
                    f"Face {index + 1}: "
                    f"{quality_status}"
                )

                cv2.putText(
                    image,
                    label,
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2
                )

        # ------------------------------------------
        # SAVE ANNOTATED IMAGE
        # ------------------------------------------

        if output_path:

            output_path = str(output_path)

            success = cv2.imwrite(
                output_path,
                image
            )

            if not success:

                raise ValueError(
                    f"Unable to save annotated image: {output_path}"
                )

        # ------------------------------------------
        # FINAL RESPONSE
        # ------------------------------------------

        good_faces = sum(
            1 for face in face_results
            if face["quality"]["quality_status"] == "good"
        )

        poor_faces = (
            len(face_results) - good_faces
        )

        return {

            "total_faces": len(face_results),

            "good_faces": good_faces,

            "poor_faces": poor_faces,

            "faces": face_results

        }
from app.services.photo_processor import PhotoProcessor

if __name__ == "__main__":
    INPUT_FOLDER = "storage/events/wedding_001/originals"
    OUTPUT_FOLDER = "storage/events/wedding_001/processed"

    processor = PhotoProcessor()
    processor.process_folder(INPUT_FOLDER, OUTPUT_FOLDER)

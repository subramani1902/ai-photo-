
import base64

print("Paste the qr_code_base64 value below.")
print("Press Enter when finished.")

qr_data = input("Base64: ").strip()

# Remove the data URI prefix if present
if "," in qr_data and qr_data.startswith("data:"):
    qr_data = qr_data.split(",", 1)[1]

try:
    image_data = base64.b64decode(qr_data, validate=True)

    output_file = "guest_qr.png"

    with open(output_file, "wb") as file:
        file.write(image_data)

    print(f"\nQR image saved successfully: {output_file}")

except Exception as error:
    print("Could not decode QR image:", error)

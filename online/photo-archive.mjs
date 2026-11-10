const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function zipHeader(signature, size) {
  const buffer = new ArrayBuffer(size), view = new DataView(buffer);
  view.setUint32(0, signature, true);
  return view;
}

function uniqueZipName(name, usedNames) {
  const basename = name.split(/[\\/]/).pop().replace(/[<>:"|?*\x00-\x1f]/g, "_") || "photo.jpg";
  const dot = basename.lastIndexOf(".");
  const stem = dot > 0 ? basename.slice(0, dot) : basename;
  const extension = dot > 0 ? basename.slice(dot) : "";
  let candidate = basename, suffix = 2;
  while (usedNames.has(candidate.toLowerCase())) candidate = `${stem} (${suffix++})${extension}`;
  usedNames.add(candidate.toLowerCase());
  return candidate;
}

export async function createPhotoArchive(items) {
  if (items.length > 65535) throw new RangeError("Too many photos for a ZIP archive.");
  const encoder = new TextEncoder(), chunks = [], directory = [], usedNames = new Set();
  let offset = 0;
  for (const item of items) {
    const name = encoder.encode(uniqueZipName(item.filename, usedNames));
    const data = new Uint8Array(await item.blob.arrayBuffer());
    if (data.length > 0xffffffff || offset + 30 + name.length + data.length > 0xffffffff) {
      throw new RangeError("The photo archive is too large.");
    }
    const checksum = crc32(data);
    const local = zipHeader(0x04034b50, 30);
    local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
    local.setUint32(14, checksum, true); local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true); local.setUint16(26, name.length, true);
    chunks.push(local.buffer, name, data);

    const central = zipHeader(0x02014b50, 46);
    central.setUint16(4, 20, true); central.setUint16(6, 20, true);
    central.setUint16(8, 0x0800, true); central.setUint32(16, checksum, true);
    central.setUint32(20, data.length, true); central.setUint32(24, data.length, true);
    central.setUint16(28, name.length, true); central.setUint32(42, offset, true);
    directory.push(central.buffer, name);
    offset += 30 + name.length + data.length;
  }
  const directoryOffset = offset;
  const directorySize = directory.reduce((total, chunk) => total + chunk.byteLength, 0);
  const end = zipHeader(0x06054b50, 22);
  end.setUint16(8, items.length, true); end.setUint16(10, items.length, true);
  end.setUint32(12, directorySize, true); end.setUint32(16, directoryOffset, true);
  return new Blob([...chunks, ...directory, end.buffer], { type: "application/zip" });
}

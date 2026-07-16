const fs = require('node:fs')
const path = require('node:path')

const MAX_SPRITESHEET_BYTES = 24 * 1024 * 1024

function readUInt24LE(buffer, offset) {
  return buffer[offset] | (buffer[offset + 1] << 8) | (buffer[offset + 2] << 16)
}

function readPngDimensions(buffer) {
  if (buffer.length < 24) return null
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  if (!buffer.subarray(0, 8).equals(signature) || buffer.toString('ascii', 12, 16) !== 'IHDR') return null
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20), format: 'png' }
}

function readWebpDimensions(buffer) {
  if (buffer.length < 30 || buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WEBP') {
    return null
  }
  let offset = 12
  while (offset + 8 <= buffer.length) {
    const type = buffer.toString('ascii', offset, offset + 4)
    const length = buffer.readUInt32LE(offset + 4)
    const dataOffset = offset + 8
    if (type === 'VP8X' && length >= 10 && dataOffset + 10 <= buffer.length) {
      return {
        width: readUInt24LE(buffer, dataOffset + 4) + 1,
        height: readUInt24LE(buffer, dataOffset + 7) + 1,
        format: 'webp'
      }
    }
    if (type === 'VP8 ' && length >= 10 && dataOffset + 10 <= buffer.length && buffer[dataOffset + 3] === 0x9d && buffer[dataOffset + 4] === 0x01 && buffer[dataOffset + 5] === 0x2a) {
      return {
        width: buffer.readUInt16LE(dataOffset + 6) & 0x3fff,
        height: buffer.readUInt16LE(dataOffset + 8) & 0x3fff,
        format: 'webp'
      }
    }
    if (type === 'VP8L' && length >= 5 && dataOffset + 5 <= buffer.length && buffer[dataOffset] === 0x2f) {
      const bits = buffer.readUInt32LE(dataOffset + 1)
      return {
        width: (bits & 0x3fff) + 1,
        height: ((bits >>> 14) & 0x3fff) + 1,
        format: 'webp'
      }
    }
    if (dataOffset + length > buffer.length) return null
    offset = dataOffset + length + (length % 2)
  }
  return null
}

function readImageDimensions(filePath) {
  const descriptor = fs.openSync(filePath, 'r')
  try {
    const buffer = Buffer.alloc(256 * 1024)
    const bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, 0)
    const header = buffer.subarray(0, bytesRead)
    return readPngDimensions(header) || readWebpDimensions(header)
  } finally {
    fs.closeSync(descriptor)
  }
}

function inferSpriteVersion(dimensions) {
  if (dimensions?.width === 1536 && dimensions?.height === 2288) return 2
  if (dimensions?.width === 1536 && dimensions?.height === 1872) return 1
  return 0
}

function inspectPetDirectory(directory) {
  const source = path.resolve(String(directory || ''))
  const manifestPath = path.join(source, 'pet.json')
  if (fs.lstatSync(manifestPath).isSymbolicLink()) throw new Error('pet.json 不能是符号链接。')
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  const spritePath = path.resolve(source, String(manifest.spritesheetPath || 'spritesheet.webp'))
  if (!spritePath.startsWith(`${source}${path.sep}`)) throw new Error('宠物图集路径无效。')
  if (fs.lstatSync(spritePath).isSymbolicLink()) throw new Error('宠物图集不能是符号链接。')
  const realSource = fs.realpathSync(source)
  const realSpritePath = fs.realpathSync(spritePath)
  if (!realSpritePath.startsWith(`${realSource}${path.sep}`)) throw new Error('宠物图集路径无效。')
  const extension = path.extname(spritePath).toLowerCase()
  if (extension !== '.webp' && extension !== '.png') throw new Error('仅支持 WebP 或 PNG 宠物图集。')
  const stat = fs.statSync(spritePath)
  if (!stat.isFile() || stat.size <= 0 || stat.size > MAX_SPRITESHEET_BYTES) {
    throw new Error('宠物图集为空或超过 24 MB。')
  }
  const dimensions = readImageDimensions(spritePath)
  const spriteVersionNumber = inferSpriteVersion(dimensions)
  if (!spriteVersionNumber) throw new Error('宠物图集必须是 1536×2288（v2）或 1536×1872（v1）。')
  if (manifest.spriteVersionNumber && Number(manifest.spriteVersionNumber) !== spriteVersionNumber) {
    throw new Error('pet.json 的 spriteVersionNumber 与图集尺寸不一致。')
  }
  return { source, manifest, spritePath, extension, dimensions, spriteVersionNumber }
}

async function extractPetZip(extractZip, zipPath, outputDirectory) {
  let entryCount = 0
  let extractedBytes = 0
  await extractZip(zipPath, {
    dir: outputDirectory,
    onEntry(entry) {
      entryCount += 1
      extractedBytes += Number(entry.uncompressedSize || 0)
      if (entryCount > 256) throw new Error('宠物 ZIP 文件数量超过 256。')
      if (extractedBytes > 64 * 1024 * 1024) throw new Error('宠物 ZIP 解压后超过 64 MB。')
    }
  })
}

function findPetDirectories(rootDirectory, maxDepth = 2) {
  const root = path.resolve(String(rootDirectory || ''))
  const matches = []
  const walk = (directory, depth) => {
    if (fs.existsSync(path.join(directory, 'pet.json'))) {
      matches.push(directory)
      return
    }
    if (depth >= maxDepth) return
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(path.join(directory, entry.name), depth + 1)
    }
  }
  walk(root, 0)
  return matches
}

module.exports = {
  MAX_SPRITESHEET_BYTES,
  extractPetZip,
  findPetDirectories,
  inferSpriteVersion,
  inspectPetDirectory,
  readImageDimensions,
  readPngDimensions,
  readWebpDimensions
}

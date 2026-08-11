import fs from 'node:fs'
import path from 'node:path'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const assetsDir = path.join(projectRoot, 'dist', 'assets')
const assetNames = fs.readdirSync(assetsDir)

function asset(prefix, extension) {
  const matches = assetNames.filter((name) => name.startsWith(prefix) && name.endsWith(extension))
  if (matches.length !== 1) {
    throw new Error(`Expected one ${prefix}*${extension} asset, found ${matches.length}`)
  }
  return matches[0]
}

const entry = asset('index-', '.js')
const preload = asset('preload-helper-', '.js')
const shared = asset('authStorage-', '.js')

const entrySets = {
  login: [
    entry,
    preload,
    asset('loginBootstrap-', '.js'),
    shared,
    asset('loginBootstrap-', '.css'),
  ],
  adminShell: [
    entry,
    preload,
    asset('appBootstrap-', '.js'),
    shared,
    asset('es-', '.js'),
    asset('appBootstrap-', '.css'),
  ],
}

function sizes(name) {
  const buffer = fs.readFileSync(path.join(assetsDir, name))
  return {
    name,
    raw: buffer.length,
    gzipLevel5: gzipSync(buffer, { level: 5 }).length,
    brotliQuality11: brotliCompressSync(buffer, {
      params: { [constants.BROTLI_PARAM_QUALITY]: 11 },
    }).length,
  }
}

const report = Object.fromEntries(
  Object.entries(entrySets).map(([entryName, names]) => {
    const files = names.map(sizes)
    const total = files.reduce(
      (result, file) => ({
        raw: result.raw + file.raw,
        gzipLevel5: result.gzipLevel5 + file.gzipLevel5,
        brotliQuality11: result.brotliQuality11 + file.brotliQuality11,
      }),
      { raw: 0, gzipLevel5: 0, brotliQuality11: 0 },
    )
    return [entryName, { files, total }]
  }),
)

console.log(JSON.stringify(report, null, 2))

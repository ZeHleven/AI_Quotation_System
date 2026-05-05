"""一次性脚本：从 jsDelivr CDN 下载前端依赖到 static/ 目录，替代不稳定的 unpkg.com。"""
import os
import urllib.request

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

FILES = [
    (
        "https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.min.js",
        "vue.global.prod.min.js",
    ),
    (
        "https://cdn.jsdelivr.net/npm/element-plus@2/dist/index.css",
        "element-plus.min.css",
    ),
    (
        "https://cdn.jsdelivr.net/npm/element-plus@2/dist/index.full.min.js",
        "element-plus.full.min.js",
    ),
    (
        "https://cdn.jsdelivr.net/npm/@element-plus/icons-vue@2/dist/index.iife.min.js",
        "element-plus-icons.iife.min.js",
    ),
    (
        "https://cdn.jsdelivr.net/npm/axios@1/dist/axios.min.js",
        "axios.min.js",
    ),
]

for url, filename in FILES:
    dest = os.path.join(STATIC_DIR, filename)
    print(f"Downloading {filename} ...", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest)
        print(f"OK ({size:,} bytes)")
    except Exception as e:
        print(f"FAILED: {e}")

print("\nDone. Files saved to:", STATIC_DIR)

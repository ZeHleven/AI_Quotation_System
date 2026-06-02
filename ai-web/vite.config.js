import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const projectRoot = fileURLToPath(new URL('.', import.meta.url))

// https://vitejs.dev/config/
export default defineConfig({
  root: projectRoot,
  plugins: [vue()],
  server: {
    port: 5173, // 固定端口，防止乱跳
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: fileURLToPath(new URL('index.html', import.meta.url)),
    },
  },
})

import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      // ⚠️ 2026-10-03：原本寫死 `http://localhost:8000`。讀 `HOST_API_LOCAL`，
      //   與 scripts 那側同一個變數 —— 否則本機改埠之後**前端 dev proxy 還打在舊埠**，
      //   而症狀是「proxy 500／連不上」，很容易誤判成前端壞了。
      '/api': {
        target: process.env.HOST_API_LOCAL ?? 'http://127.0.0.1:8000',
        rewrite: (p) => p.replace(/^\/api/, '')
      }
    }
  }
});

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
        // ⚠️ 2026-10-03：預設值原本是 8000，換埠後**指向死埠** —— 症狀是
        //   dev proxy 連不上／500，很容易誤判成前端壞了。與 compose 的
        //   `${API_PORT:-920}` 同一個值；由 test_api_port_single_source.py 守住。
        target: process.env.HOST_API_LOCAL ?? 'http://127.0.0.1:920',
        rewrite: (p) => p.replace(/^\/api/, '')
      }
    }
  }
});

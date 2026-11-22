import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Dev ports follow the platform port table: graph 8080, faultlib 8081,
 * chaos 8082, sim 8083, agent 8084. The browser only ever talks to /api/<svc>
 * so the same paths work in the cluster through nginx.
 */
const proxy = (prefix: string, port: number) => ({
  [prefix]: {
    target: `http://localhost:${port}`,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(prefix, ''),
  },
});

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      ...proxy('/api/chaos', 8082),
      ...proxy('/api/graph', 8080),
      ...proxy('/api/faultlib', 8081),
      ...proxy('/api/agent', 8084),
      ...proxy('/api/sim', 8083),
    },
  },
  preview: {
    port: 5174,
    proxy: {
      ...proxy('/api/chaos', 8082),
      ...proxy('/api/graph', 8080),
      ...proxy('/api/faultlib', 8081),
      ...proxy('/api/agent', 8084),
      ...proxy('/api/sim', 8083),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});

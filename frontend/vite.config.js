import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    plugins: [react()],
    base: "/app/",
    define: {
        __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
    },
    server: {
        port: 5173,
        proxy: {
            "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
        },
    },
});

import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:'http://127.0.0.1:5181',headless:true,channel:'msedge'},webServer:{command:'pnpm.cmd dev --port 5181',url:'http://127.0.0.1:5181',reuseExistingServer:true},workers:1});

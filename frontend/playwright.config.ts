import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./e2e',timeout:90000,workers:1,use:{baseURL:'http://127.0.0.1:5173',viewport:{width:1440,height:1000},screenshot:'only-on-failure'},webServer:[
  {command:'..\\.venv\\Scripts\\python.exe ..\\scripts\\e2e_server.py',url:'http://127.0.0.1:8000/health/live',timeout:30000,reuseExistingServer:false},
  {command:'npm run dev',url:'http://127.0.0.1:5173',timeout:30000,reuseExistingServer:false}
]});

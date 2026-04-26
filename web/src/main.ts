import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './style.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.mount('#app')

console.log('%cYuxi-style Backtrader Frontend 已启动 | 仿 https://github.com/xerrors/Yuxi', 'color:#22d3ee;font-weight:bold')